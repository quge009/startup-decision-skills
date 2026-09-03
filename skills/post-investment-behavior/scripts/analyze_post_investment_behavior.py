"""Build descriptive investor post-investment behavior features.

This script is independent from Chain Pattern analysis. It performs no support
filtering, clustering, ranking, or behavior classification. Every resolved
Interface investor is retained. Event-level output states whether a prior
investment is temporally confirmed, unknown, overlapping, later, or absent.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import statistics
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


SCHEMA_VERSION = "investor-post-investment-behavior-v0.1.0"
RELATION_STATUSES = (
    "CONFIRMED_POST_INVESTMENT",
    "SAME_OR_OVERLAPPING_INVESTMENT_DATE",
    "INVESTMENT_EVIDENCE_ONLY_AFTER_EVENT",
    "TIMING_UNKNOWN",
    "NO_INVESTMENT_EVIDENCE",
)
CASE_CORE_EVENT_TYPES = frozenset({
    "activist_action", "board_change", "exit", "major_holder_change",
    "secondary_transaction", "strategic_investment",
})
PRIMARY_SUPPORT_GATE = {"minimum_core_episode_count": 3, "minimum_all_interface_companies": 3}
LEGACY_RAW_GATE = {"minimum_core_event_rows": 5, "minimum_all_interface_companies": 3}
PARQUET_OPTIONS = {
    "compression": "snappy",
    "version": "2.6",
    "data_page_version": "1.0",
    "use_dictionary": True,
    "write_statistics": True,
    "row_group_size": 65536,
}


@dataclass(frozen=True)
class DateInterval:
    raw: str
    lower: dt.date
    upper: dt.date
    precision: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def parse_date_interval(value: Any) -> DateInterval | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        if re.fullmatch(r"\d{4}", raw):
            year = int(raw)
            return DateInterval(raw, dt.date(year, 1, 1), dt.date(year, 12, 31), "year")
        if re.fullmatch(r"\d{4}-\d{2}", raw):
            year, month = map(int, raw.split("-"))
            last = calendar.monthrange(year, month)[1]
            return DateInterval(raw, dt.date(year, month, 1), dt.date(year, month, last), "month")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            value_date = dt.date.fromisoformat(raw)
            return DateInterval(raw, value_date, value_date, "day")
    except ValueError:
        return None
    return None


def require_columns(table: pa.Table, required: set[str], path: Path) -> None:
    missing = sorted(required - set(table.column_names))
    if missing:
        raise ValueError(f"{path} missing required columns: {missing}")


def validate_output_dir(path: Path, inputs: list[Path]) -> Path:
    output = path.expanduser().resolve(strict=False)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite output directory: {output}")
    if output in {item.resolve() for item in inputs}:
        raise ValueError("output directory aliases an input")
    return output


def investment_evidence_rows(
    funding_rows: list[dict[str, Any]], interface_rows: list[dict[str, Any]],
    valid_investors: set[str], valid_companies: set[str],
) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], Counter]:
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    counts: Counter = Counter()

    def add(row: dict[str, Any], family: str, date_field: str) -> None:
        investor_id, company_id = row.get("investor_id"), row.get("company_id")
        if not investor_id or investor_id not in valid_investors or company_id not in valid_companies:
            return
        interval = parse_date_interval(row.get(date_field))
        by_pair[(investor_id, company_id)].append({
            "source_family": family,
            "source_event_id": row["event_id"],
            "date": row.get(date_field),
            "date_interval": interval,
            "source_url": row.get("source_url"),
        })
        counts[f"{family}_rows"] += 1
        counts[f"{family}_dated_rows"] += interval is not None

    for row in funding_rows:
        add(row, "funding_events", "announce_date")
    for row in interface_rows:
        if row.get("event_type") == "funding_participation":
            add(row, "interface_funding_participation", "event_date")
    for values in by_pair.values():
        values.sort(key=lambda item: (
            item["date_interval"].lower if item["date_interval"] else dt.date.max,
            item["source_family"], item["source_event_id"],
        ))
    return by_pair, counts


def classify_temporal_relation(
    event_date: Any, evidence: list[dict[str, Any]],
) -> tuple[str, DateInterval | None, list[dict[str, Any]]]:
    if not evidence:
        return "NO_INVESTMENT_EVIDENCE", None, []
    event_interval = parse_date_interval(event_date)
    dated = [item for item in evidence if item["date_interval"] is not None]
    if event_interval is None or not dated:
        return "TIMING_UNKNOWN", event_interval, []
    prior = [item for item in dated if item["date_interval"].upper < event_interval.lower]
    if prior:
        return "CONFIRMED_POST_INVESTMENT", event_interval, prior
    overlap = [
        item for item in dated
        if item["date_interval"].lower <= event_interval.upper
        and item["date_interval"].upper >= event_interval.lower
    ]
    if overlap:
        return "SAME_OR_OVERLAPPING_INVESTMENT_DATE", event_interval, []
    later = [item for item in dated if item["date_interval"].lower > event_interval.upper]
    if len(later) == len(dated) and not any(item["date_interval"] is None for item in evidence):
        return "INVESTMENT_EVIDENCE_ONLY_AFTER_EVENT", event_interval, []
    return "TIMING_UNKNOWN", event_interval, []


def evidence_for_json(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "source_family": item["source_family"],
        "source_event_id": item["source_event_id"],
        "date": item["date"],
        "source_url": item["source_url"],
    } for item in evidence]


def build_event_rows(
    interface_rows: list[dict[str, Any]], investor_by_id: dict[str, dict[str, Any]],
    company_by_id: dict[str, dict[str, Any]],
    evidence_by_pair: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    result = []
    for row in interface_rows:
        investor_id = row.get("investor_id")
        if not investor_id:
            continue
        if investor_id not in investor_by_id:
            raise ValueError(f"Interface investor_id missing from investor entity: {investor_id}")
        company_id = row["company_id"]
        if company_id not in company_by_id:
            raise ValueError(f"Interface company_id missing from company entity: {company_id}")
        evidence = evidence_by_pair.get((investor_id, company_id), [])
        status, event_interval, prior = classify_temporal_relation(row.get("event_date"), evidence)
        dated_evidence = [item for item in evidence if item["date_interval"] is not None]
        earliest = dated_evidence[0] if dated_evidence else None
        company = company_by_id[company_id]
        result.append({
            "event_id": row["event_id"],
            "investor_id": investor_id,
            "investor_name": investor_by_id[investor_id]["name"],
            "investor_type_json": canonical_json(investor_by_id[investor_id].get("investor_type") or []),
            "company_id": company_id,
            "company_name": company["company_canonical_name"],
            "company_label": company.get("label_v4"),
            "company_outcome_label": company.get("outcome_label_v4"),
            "raw_investor_name": row.get("raw_investor_name"),
            "event_date": row.get("event_date"),
            "event_date_precision": event_interval.precision if event_interval else None,
            "event_type": row["event_type"],
            "event_subtype": row.get("event_subtype"),
            "title": row.get("title"),
            "summary": row.get("summary"),
            "source_url": row.get("source_url"),
            "temporal_relation_status": status,
            "earliest_observed_investment_date": earliest["date"] if earliest else None,
            "earliest_observed_investment_source": earliest["source_family"] if earliest else None,
            "n_investment_evidence_rows": len(evidence),
            "n_definitely_prior_investment_rows": len(prior),
            "investment_evidence_json": canonical_json(evidence_for_json(evidence)),
            "schema_version": SCHEMA_VERSION,
        })
    result.sort(key=lambda item: item["event_id"].encode("utf-8"))
    return result


def build_profiles(
    event_rows: list[dict[str, Any]], investor_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in event_rows:
        grouped[row["investor_id"]].append(row)
    profiles = []
    for investor_id, rows in sorted(grouped.items(), key=lambda item: item[0].encode("utf-8")):
        entity = investor_by_id[investor_id]
        companies = {row["company_id"] for row in rows}
        by_company = Counter(row["company_id"] for row in rows)
        statuses = Counter(row["temporal_relation_status"] for row in rows)
        all_types = Counter(row["event_type"] for row in rows)
        confirmed = [row for row in rows if row["temporal_relation_status"] == "CONFIRMED_POST_INVESTMENT"]
        confirmed_companies = {row["company_id"] for row in confirmed}
        confirmed_types = Counter(row["event_type"] for row in confirmed)
        company_labels = {row["company_id"]: row["company_label"] for row in rows}
        confirmed_labels = {row["company_id"]: row["company_label"] for row in confirmed}
        dated = [row["event_date"] for row in rows if parse_date_interval(row.get("event_date"))]
        episode_keys = {
            (row["company_id"], row["event_type"], row["event_date"])
            if row.get("event_date") else (row["event_id"],)
            for row in confirmed
        }
        profile = {
            "investor_id": investor_id,
            "investor_name": entity["name"],
            "investor_type_json": canonical_json(entity.get("investor_type") or []),
            "n_interface_event_rows": len(rows),
            "n_interface_companies": len(companies),
            "n_distinct_event_types": len(all_types),
            "n_dated_event_rows": len(dated),
            "first_interface_event_date": min(dated) if dated else None,
            "last_interface_event_date": max(dated) if dated else None,
            "n_repeated_interface_companies": sum(value >= 2 for value in by_company.values()),
            "max_event_rows_per_company": max(by_company.values()),
            "n_confirmed_post_investment_rows": len(confirmed),
            "n_confirmed_post_investment_companies": len(confirmed_companies),
            "n_confirmed_post_investment_episode_keys": len(episode_keys),
            "n_confirmed_post_investment_duplicate_source_rows": len(confirmed) - len(episode_keys),
            "n_same_or_overlapping_investment_date_rows": statuses["SAME_OR_OVERLAPPING_INVESTMENT_DATE"],
            "n_investment_evidence_only_after_event_rows": statuses["INVESTMENT_EVIDENCE_ONLY_AFTER_EVENT"],
            "n_timing_unknown_rows": statuses["TIMING_UNKNOWN"],
            "n_no_investment_evidence_rows": statuses["NO_INVESTMENT_EVIDENCE"],
            "n_success_companies": sum(label == "SUCCESS" for label in company_labels.values()),
            "n_failure_companies": sum(label == "FAILURE" for label in company_labels.values()),
            "n_ambiguous_companies": sum(label not in {"SUCCESS", "FAILURE"} for label in company_labels.values()),
            "n_confirmed_post_success_companies": sum(label == "SUCCESS" for label in confirmed_labels.values()),
            "n_confirmed_post_failure_companies": sum(label == "FAILURE" for label in confirmed_labels.values()),
            "n_confirmed_post_ambiguous_companies": sum(label not in {"SUCCESS", "FAILURE"} for label in confirmed_labels.values()),
            "event_type_counts_json": canonical_json(all_types),
            "confirmed_post_event_type_counts_json": canonical_json(confirmed_types),
            "temporal_relation_status_counts_json": canonical_json(statuses),
            "company_label_counts_json": canonical_json(Counter(company_labels.values())),
            "confirmed_post_company_label_counts_json": canonical_json(Counter(confirmed_labels.values())),
            "schema_version": SCHEMA_VERSION,
        }
        profiles.append(profile)
    return profiles


def episode_key(row: dict[str, Any]) -> tuple[str, ...]:
    if row.get("event_date"):
        return (row["company_id"], row["event_type"], row["event_date"])
    return (row["event_id"],)


def build_descriptive_summary(
    event_rows: list[dict[str, Any]], profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    confirmed = [row for row in event_rows if row["temporal_relation_status"] == "CONFIRMED_POST_INVESTMENT"]
    by_investor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in confirmed:
        by_investor[row["investor_id"]].append(row)
        by_pair[(row["investor_id"], row["company_id"])].append(row)
    event_types = Counter(row["event_type"] for row in confirmed)
    pair_labels = Counter(rows[0]["company_label"] for rows in by_pair.values())
    outcome_labels = Counter(row["company_outcome_label"] for row in confirmed)
    type_investors: dict[str, set[str]] = defaultdict(set)
    type_events: Counter = Counter()
    type_event_types: dict[str, Counter] = defaultdict(Counter)
    profile_type_investors: dict[str, set[str]] = defaultdict(set)
    type_all_events: Counter = Counter()
    type_all_pairs: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for profile in profiles:
        for investor_type in json.loads(profile["investor_type_json"]):
            profile_type_investors[investor_type].add(profile["investor_id"])
    for row in event_rows:
        for investor_type in json.loads(row["investor_type_json"]):
            type_all_events[investor_type] += 1
            type_all_pairs[investor_type].add((row["investor_id"], row["company_id"]))
    for investor_id, rows in by_investor.items():
        for investor_type in json.loads(rows[0]["investor_type_json"]):
            type_investors[investor_type].add(investor_id)
            type_events[investor_type] += len(rows)
            type_event_types[investor_type].update(row["event_type"] for row in rows)

    def pair_behavior_combination(rows: list[dict[str, Any]]) -> str:
        event_type_set = {row["event_type"] for row in rows}
        components = []
        if "funding_participation" in event_type_set:
            components.append("funding")
        if "exit" in event_type_set:
            components.append("exit")
        if "strategic_investment" in event_type_set:
            components.append("strategic_investment")
        if event_type_set & {"activist_action", "board_change", "major_holder_change", "secondary_transaction"}:
            components.append("governance_or_ownership")
        if event_type_set - CASE_CORE_EVENT_TYPES - {"funding_participation"}:
            components.append("other")
        return "+".join(components)

    pair_behavior_counts = Counter(pair_behavior_combination(rows) for rows in by_pair.values())
    type_coverage_and_behavior = {}
    for investor_type in sorted(profile_type_investors):
        universe_ids = profile_type_investors[investor_type]
        confirmed_ids = type_investors[investor_type]
        confirmed_rows = [
            row for row in confirmed
            if investor_type in json.loads(row["investor_type_json"])
        ]
        confirmed_pairs = {
            (row["investor_id"], row["company_id"]) for row in confirmed_rows
        }
        confirmed_by_investor: dict[str, list[dict[str, Any]]] = defaultdict(list)
        confirmed_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in confirmed_rows:
            confirmed_by_investor[row["investor_id"]].append(row)
            confirmed_by_pair[(row["investor_id"], row["company_id"])].append(row)
        investor_event_counts = sorted(len(rows) for rows in confirmed_by_investor.values())
        confirmed_event_types = type_event_types[investor_type]
        funding_rows = confirmed_event_types["funding_participation"]
        non_funding_rows = len(confirmed_rows) - funding_rows
        type_coverage_and_behavior[investor_type] = {
            "profile_investors": len(universe_ids),
            "interface_event_rows": type_all_events[investor_type],
            "interface_investor_company_pairs": len(type_all_pairs[investor_type]),
            "confirmed_investors": len(confirmed_ids),
            "confirmed_investor_share": len(confirmed_ids) / len(universe_ids),
            "confirmed_event_rows": len(confirmed_rows),
            "confirmed_event_row_share": len(confirmed_rows) / type_all_events[investor_type],
            "confirmed_investor_company_pairs": len(confirmed_pairs),
            "confirmed_pair_share": len(confirmed_pairs) / len(type_all_pairs[investor_type]),
            "confirmed_events_per_confirmed_investor_mean": (
                len(confirmed_rows) / len(confirmed_ids) if confirmed_ids else None
            ),
            "confirmed_events_per_confirmed_investor_median": (
                statistics.median(investor_event_counts) if investor_event_counts else None
            ),
            "confirmed_event_type_counts": dict(confirmed_event_types.most_common()),
            "confirmed_event_type_shares": {
                key: value / len(confirmed_rows)
                for key, value in confirmed_event_types.most_common()
            },
            "confirmed_funding_rows": funding_rows,
            "confirmed_funding_row_share": funding_rows / len(confirmed_rows) if confirmed_rows else None,
            "confirmed_non_funding_rows": non_funding_rows,
            "confirmed_non_funding_row_share": non_funding_rows / len(confirmed_rows) if confirmed_rows else None,
            "confirmed_investors_with_only_funding": sum(
                {row["event_type"] for row in rows} == {"funding_participation"}
                for rows in confirmed_by_investor.values()
            ),
            "confirmed_investors_with_non_funding_behavior": sum(
                any(row["event_type"] != "funding_participation" for row in rows)
                for rows in confirmed_by_investor.values()
            ),
            "confirmed_pair_behavior_combination_counts": dict(sorted(Counter(
                pair_behavior_combination(rows) for rows in confirmed_by_pair.values()
            ).items())),
        }
    event_counts = sorted(len(rows) for rows in by_investor.values())
    company_counts = sorted(len({row["company_id"] for row in rows}) for rows in by_investor.values())
    episode_count = len({(row["investor_id"], *episode_key(row)) for row in confirmed})
    ranking = []
    for investor_id, rows in by_investor.items():
        ranking.append({
            "investor_id": investor_id,
            "investor_name": rows[0]["investor_name"],
            "investor_types": json.loads(rows[0]["investor_type_json"]),
            "confirmed_event_rows": len(rows),
            "confirmed_companies": len({row["company_id"] for row in rows}),
            "event_type_counts": dict(sorted(Counter(row["event_type"] for row in rows).items())),
            "company_label_counts": dict(sorted(Counter({row["company_id"]: row["company_label"] for row in rows}.values()).items())),
        })
    ranking.sort(key=lambda row: (-row["confirmed_event_rows"], -row["confirmed_companies"], row["investor_name"].encode("utf-8")))
    return {
        "schema_version": SCHEMA_VERSION,
        "confirmed_post_investment_event_rows": len(confirmed),
        "confirmed_post_investment_episode_count": episode_count,
        "duplicate_source_rows_by_episode_key": len(confirmed) - episode_count,
        "confirmed_investors": len(by_investor),
        "confirmed_investor_company_pairs": len(by_pair),
        "event_type_counts": dict(event_types.most_common()),
        "event_type_shares": {key: value / len(confirmed) for key, value in event_types.most_common()},
        "confirmed_pair_behavior_combination_counts": dict(sorted(pair_behavior_counts.items())),
        "pair_company_label_counts": dict(sorted(pair_labels.items())),
        "event_company_outcome_label_counts": dict(outcome_labels.most_common()),
        "investor_type_coverage_and_behavior": type_coverage_and_behavior,
        "investor_type_summary": {
            key: {
                "investors": len(type_investors[key]),
                "event_rows": type_events[key],
                "event_type_counts": dict(type_event_types[key].most_common()),
            }
            for key in sorted(type_investors)
        },
        "investor_event_count_distribution": {
            "minimum": min(event_counts),
            "median": statistics.median(event_counts),
            "mean": sum(event_counts) / len(event_counts),
            "maximum": max(event_counts),
            "frequency": dict(sorted(Counter(event_counts).items())),
        },
        "investor_company_count_distribution": {
            "minimum": min(company_counts),
            "median": statistics.median(company_counts),
            "mean": sum(company_counts) / len(company_counts),
            "maximum": max(company_counts),
            "frequency": dict(sorted(Counter(company_counts).items())),
        },
        "all_confirmed_investor_rankings": ranking,
        "profile_universe_size": len(profiles),
        "interpretation_limits": [
            "Descriptive counts are not causal effects.",
            "Funding and acquisition events can overlap company outcome-label definitions.",
            "Investor types can be multi-valued, so type counts need not sum to the investor count.",
        ],
    }


def build_support_metrics(event_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in event_rows:
        grouped[row["investor_id"]].append(row)
    metrics = []
    for investor_id, rows in sorted(grouped.items(), key=lambda item: item[0].encode("utf-8")):
        core_rows = [row for row in rows if row["event_type"] in CASE_CORE_EVENT_TYPES]
        episode_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in core_rows:
            episode_groups[episode_key(row)].append(row)
        episode_type_counts = Counter(group[0]["event_type"] for group in episode_groups.values())
        all_companies = {row["company_id"] for row in rows}
        core_companies = {row["company_id"] for row in core_rows}
        by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_company[row["company_id"]].append(row)
        funding_to_exit = []
        for company_id, company_rows in sorted(by_company.items()):
            funding = [row for row in company_rows if row["event_type"] == "funding_participation"]
            exits = [row for row in company_rows if row["event_type"] == "exit"]
            if funding and exits:
                funding_to_exit.append({
                    "company_id": company_id,
                    "company_name": company_rows[0]["company_name"],
                    "funding_event_ids": sorted(row["event_id"] for row in funding),
                    "exit_event_ids": sorted(row["event_id"] for row in exits),
                })
        episode_evidence = []
        for key, group in sorted(episode_groups.items()):
            episode_evidence.append({
                "episode_key": list(key),
                "company_id": group[0]["company_id"],
                "company_name": group[0]["company_name"],
                "event_type": group[0]["event_type"],
                "event_date": group[0]["event_date"],
                "source_row_count": len(group),
                "event_ids": sorted(row["event_id"] for row in group),
                "titles": [row["title"] for row in sorted(group, key=lambda item: item["event_id"])],
                "source_urls": [row["source_url"] for row in sorted(group, key=lambda item: item["event_id"])],
                "event_subtypes": [row["event_subtype"] for row in sorted(group, key=lambda item: item["event_id"])],
            })
        n_core_episodes = len(episode_groups)
        n_all_companies = len(all_companies)
        n_exit_episodes = episode_type_counts["exit"]
        metrics.append({
            "investor_id": investor_id,
            "investor_name": rows[0]["investor_name"],
            "investor_type_json": rows[0]["investor_type_json"],
            "n_interface_event_rows": len(rows),
            "n_all_interface_companies": n_all_companies,
            "n_core_event_rows": len(core_rows),
            "n_core_episode_count": n_core_episodes,
            "n_core_companies": len(core_companies),
            "n_duplicate_core_source_rows": len(core_rows) - n_core_episodes,
            "n_funding_participation_rows": sum(row["event_type"] == "funding_participation" for row in rows),
            "n_exit_event_rows": sum(row["event_type"] == "exit" for row in rows),
            "n_exit_episode_count": n_exit_episodes,
            "n_strategic_investment_episode_count": episode_type_counts["strategic_investment"],
            "n_board_change_episode_count": episode_type_counts["board_change"],
            "n_major_holder_change_episode_count": episode_type_counts["major_holder_change"],
            "n_secondary_transaction_episode_count": episode_type_counts["secondary_transaction"],
            "n_activist_action_episode_count": episode_type_counts["activist_action"],
            "exit_share_of_core_episodes": n_exit_episodes / n_core_episodes if n_core_episodes else None,
            "n_funding_to_exit_companies": len(funding_to_exit),
            "passes_primary_episode_gate_3_3": n_core_episodes >= PRIMARY_SUPPORT_GATE["minimum_core_episode_count"] and n_all_companies >= PRIMARY_SUPPORT_GATE["minimum_all_interface_companies"],
            "passes_legacy_raw_gate_5_3": len(core_rows) >= LEGACY_RAW_GATE["minimum_core_event_rows"] and n_all_companies >= LEGACY_RAW_GATE["minimum_all_interface_companies"],
            "passes_legacy_episode_gate_5_3": n_core_episodes >= LEGACY_RAW_GATE["minimum_core_event_rows"] and n_all_companies >= LEGACY_RAW_GATE["minimum_all_interface_companies"],
            "all_event_type_counts_json": canonical_json(Counter(row["event_type"] for row in rows)),
            "core_row_event_type_counts_json": canonical_json(Counter(row["event_type"] for row in core_rows)),
            "core_episode_event_type_counts_json": canonical_json(episode_type_counts),
            "funding_to_exit_companies_json": canonical_json(funding_to_exit),
            "core_episode_evidence_json": canonical_json(episode_evidence),
            "temporal_relation_status_counts_json": canonical_json(Counter(row["temporal_relation_status"] for row in rows)),
            "schema_version": SCHEMA_VERSION,
        })
    breadth_order = sorted(metrics, key=lambda row: (-row["n_all_interface_companies"], -row["n_core_episode_count"], row["investor_name"].encode("utf-8")))
    episode_order = sorted(metrics, key=lambda row: (-row["n_core_episode_count"], -row["n_all_interface_companies"], row["investor_name"].encode("utf-8")))
    breadth_rank = {row["investor_id"]: index for index, row in enumerate(breadth_order, 1)}
    episode_rank = {row["investor_id"]: index for index, row in enumerate(episode_order, 1)}
    for row in metrics:
        row["breadth_rank_by_all_interface_companies"] = breadth_rank[row["investor_id"]]
        row["core_episode_count_rank"] = episode_rank[row["investor_id"]]
    return metrics


def build_support_analysis(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    def selected(field: str) -> list[dict[str, Any]]:
        values = [row for row in metrics if row[field]]
        values.sort(key=lambda row: (-row["n_core_episode_count"], -row["n_all_interface_companies"], row["investor_name"].encode("utf-8")))
        return values
    primary = selected("passes_primary_episode_gate_3_3")
    legacy_raw = selected("passes_legacy_raw_gate_5_3")
    legacy_episode = selected("passes_legacy_episode_gate_5_3")
    changed = sorted(set(row["investor_id"] for row in legacy_raw) - set(row["investor_id"] for row in legacy_episode))
    max_episodes = max(row["n_core_episode_count"] for row in metrics)
    max_companies = max(row["n_all_interface_companies"] for row in metrics)
    grid = []
    for minimum_episodes in range(1, max_episodes + 1):
        for minimum_companies in range(1, max_companies + 1):
            grid.append({
                "minimum_core_episode_count": minimum_episodes,
                "minimum_all_interface_companies": minimum_companies,
                "investor_count": sum(row["n_core_episode_count"] >= minimum_episodes and row["n_all_interface_companies"] >= minimum_companies for row in metrics),
            })
    def compact(row: dict[str, Any]) -> dict[str, Any]:
        return {key: row[key] for key in (
            "investor_id", "investor_name", "investor_type_json",
            "n_interface_event_rows", "n_all_interface_companies", "n_core_event_rows",
            "n_core_episode_count", "n_core_companies", "n_duplicate_core_source_rows",
            "n_funding_participation_rows", "n_exit_episode_count",
            "n_strategic_investment_episode_count", "n_board_change_episode_count",
            "n_major_holder_change_episode_count", "n_secondary_transaction_episode_count",
            "n_activist_action_episode_count", "exit_share_of_core_episodes", "n_funding_to_exit_companies",
            "breadth_rank_by_all_interface_companies", "core_episode_count_rank",
            "core_episode_event_type_counts_json", "temporal_relation_status_counts_json",
            "funding_to_exit_companies_json", "core_episode_evidence_json",
        )}
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_generation_scope": "all resolved Interface investors; no investor-name filter",
        "core_event_types": sorted(CASE_CORE_EVENT_TYPES),
        "primary_gate": PRIMARY_SUPPORT_GATE,
        "primary_gate_candidates": [compact(row) for row in primary],
        "legacy_raw_gate": LEGACY_RAW_GATE,
        "legacy_raw_gate_candidates": [compact(row) for row in legacy_raw],
        "legacy_episode_dedup_gate_candidates": [compact(row) for row in legacy_episode],
        "legacy_gate_duplicate_sensitive_investor_ids": changed,
        "threshold_count_grid": grid,
        "broadest_primary_candidate": compact(sorted(primary, key=lambda row: (-row["n_all_interface_companies"], row["investor_name"].encode("utf-8")))[0]) if primary else None,
        "acquisition_active_primary_candidates": [compact(row) for row in sorted((row for row in primary if row["n_exit_episode_count"] > 0), key=lambda row: (-row["exit_share_of_core_episodes"], -row["n_exit_episode_count"], row["investor_name"].encode("utf-8")))],
        "interpretation": {
            "primary_gate_origin": "user-confirmed minimum of 3 deduplicated core episodes and 3 all-Interface companies",
            "legacy_gate_purpose": "reproduce raw-row versus episode-dedup sensitivity; not the primary gate",
            "name_filtering": "none",
            "behavior_labels": "not assigned by the script; output contains counts and evidence for downstream interpretation",
        },
    }



def build_primary_candidate_comparison(
    support_analysis: dict[str, Any], event_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare every primary-gate candidate on the same descriptive dimensions."""
    candidates = support_analysis["primary_gate_candidates"]
    candidate_ids = {row["investor_id"] for row in candidates}
    covered_companies = {
        row["company_id"]: row["company_name"]
        for row in event_rows if row["investor_id"] in candidate_ids
    }

    def parse_json_field(row: dict[str, Any], field: str) -> Any:
        return json.loads(row[field])

    comparison_table = []
    behavior_composition = []
    evidence_by_investor = []
    for row in candidates:
        investor_types = parse_json_field(row, "investor_type_json")
        event_type_counts = parse_json_field(row, "core_episode_event_type_counts_json")
        temporal_counts = parse_json_field(row, "temporal_relation_status_counts_json")
        episode_evidence = parse_json_field(row, "core_episode_evidence_json")
        funding_to_exit_evidence = parse_json_field(row, "funding_to_exit_companies_json")
        governance_episodes = sum(row[field] for field in (
            "n_board_change_episode_count",
            "n_major_holder_change_episode_count",
            "n_secondary_transaction_episode_count",
            "n_activist_action_episode_count",
        ))
        signature_components = []
        if row["n_exit_episode_count"] > 0:
            signature_components.append("exit")
        if row["n_strategic_investment_episode_count"] > 0:
            signature_components.append("strategic_investment")
        if governance_episodes > 0:
            signature_components.append("governance_or_ownership")
        core_behavior_signature = "+".join(signature_components)
        core_episodes = row["n_core_episode_count"]
        core_companies = row["n_core_companies"]
        all_companies = row["n_all_interface_companies"]
        comparison_table.append({
            "investor_id": row["investor_id"],
            "investor_name": row["investor_name"],
            "investor_types": investor_types,
            "n_interface_event_rows": row["n_interface_event_rows"],
            "n_all_interface_companies": all_companies,
            "n_core_event_rows": row["n_core_event_rows"],
            "n_core_episode_count": core_episodes,
            "n_core_companies": core_companies,
            "n_duplicate_core_source_rows": row["n_duplicate_core_source_rows"],
            "n_funding_participation_rows": row["n_funding_participation_rows"],
            "n_exit_episode_count": row["n_exit_episode_count"],
            "n_strategic_investment_episode_count": row["n_strategic_investment_episode_count"],
            "n_board_change_episode_count": row["n_board_change_episode_count"],
            "n_major_holder_change_episode_count": row["n_major_holder_change_episode_count"],
            "n_secondary_transaction_episode_count": row["n_secondary_transaction_episode_count"],
            "n_activist_action_episode_count": row["n_activist_action_episode_count"],
            "n_governance_and_ownership_episode_count": governance_episodes,
            "core_behavior_signature": core_behavior_signature,
            "n_confirmed_post_investment_rows": temporal_counts.get("CONFIRMED_POST_INVESTMENT", 0),
            "exit_share_of_core_episodes": row["exit_share_of_core_episodes"],
            "n_funding_to_exit_companies": row["n_funding_to_exit_companies"],
            "breadth_rank_by_all_interface_companies": row["breadth_rank_by_all_interface_companies"],
            "core_episode_count_rank": row["core_episode_count_rank"],
            "temporal_relation_status_counts": temporal_counts,
        })
        behavior_composition.append({
            "investor_id": row["investor_id"],
            "investor_name": row["investor_name"],
            "core_episode_event_type_counts": event_type_counts,
            "core_episode_event_type_shares": {
                event_type: count / core_episodes
                for event_type, count in sorted(event_type_counts.items())
            },
            "core_episodes_per_core_company": core_episodes / core_companies if core_companies else None,
            "core_company_share_of_all_interface_companies": core_companies / all_companies if all_companies else None,
        })
        evidence_by_investor.append({
            "investor_id": row["investor_id"],
            "investor_name": row["investor_name"],
            "core_episode_evidence": episode_evidence,
            "funding_to_exit_evidence": funding_to_exit_evidence,
        })

    signature_members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in comparison_table:
        signature_members[row["core_behavior_signature"]].append(row)
    behavior_signature_groups = []
    for signature, members in sorted(
        signature_members.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        behavior_signature_groups.append({
            "core_behavior_signature": signature,
            "candidate_count": len(members),
            "candidate_ids": [row["investor_id"] for row in members],
            "candidate_names": [row["investor_name"] for row in members],
            "aggregate_core_episode_count": sum(row["n_core_episode_count"] for row in members),
            "aggregate_exit_episode_count": sum(row["n_exit_episode_count"] for row in members),
            "aggregate_strategic_investment_episode_count": sum(
                row["n_strategic_investment_episode_count"] for row in members
            ),
            "aggregate_governance_and_ownership_episode_count": sum(
                row["n_governance_and_ownership_episode_count"] for row in members
            ),
            "aggregate_funding_participation_rows": sum(
                row["n_funding_participation_rows"] for row in members
            ),
            "aggregate_confirmed_post_investment_rows": sum(
                row["n_confirmed_post_investment_rows"] for row in members
            ),
            "all_members_have_same_core_component_presence": True,
            "economic_logic_equivalence": "not asserted; event-component similarity requires evidence-level interpretation",
        })

    def ranked(metric: str, extra_fields: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        ordered = sorted(
            comparison_table,
            key=lambda row: (-row[metric], row["investor_name"].encode("utf-8")),
        )
        return [{
            "candidate_rank": rank,
            "investor_id": row["investor_id"],
            "investor_name": row["investor_name"],
            metric: row[metric],
            **{field: row[field] for field in extra_fields},
        } for rank, row in enumerate(ordered, 1)]

    maximum = lambda field: max((row[field] for row in comparison_table), default=0)
    matching_names = lambda field, value: [
        row["investor_name"] for row in comparison_table if row[field] == value
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "scope_and_gate": {
            "candidate_generation_scope": support_analysis["candidate_generation_scope"],
            "primary_gate": support_analysis["primary_gate"],
            "core_event_types": support_analysis["core_event_types"],
            "comparison_population": "every investor passing the primary gate; no name or ID selection",
        },
        "candidate_count": len(candidates),
        "candidate_ids": [row["investor_id"] for row in candidates],
        "candidate_names": [row["investor_name"] for row in candidates],
        "candidate_company_coverage": {
            "distinct_all_interface_companies": len(covered_companies),
            "companies": [
                {"company_id": company_id, "company_name": covered_companies[company_id]}
                for company_id in sorted(covered_companies)
            ],
        },
        "comparison_table": comparison_table,
        "behavior_composition": behavior_composition,
        "behavior_signature_groups": behavior_signature_groups,
        "cross_candidate_behavior_structure": {
            "repeated_signature_groups": [
                group for group in behavior_signature_groups if group["candidate_count"] >= 2
            ],
            "candidate_count_in_repeated_signature_groups": sum(
                group["candidate_count"] for group in behavior_signature_groups
                if group["candidate_count"] >= 2
            ),
            "candidates_with_exit_component": sum(
                row["n_exit_episode_count"] > 0 for row in comparison_table
            ),
            "candidates_with_strategic_investment_component": sum(
                row["n_strategic_investment_episode_count"] > 0 for row in comparison_table
            ),
            "candidates_with_governance_or_ownership_component": sum(
                row["n_governance_and_ownership_episode_count"] > 0 for row in comparison_table
            ),
            "candidates_with_confirmed_post_investment_rows": sum(
                row["n_confirmed_post_investment_rows"] > 0 for row in comparison_table
            ),
            "interpretation": "Repeated signatures establish common event-component composition, not equivalent economic intent or causal behavior.",
        },
        "breadth_ranking": ranked(
            "n_all_interface_companies",
            ("n_interface_event_rows", "breadth_rank_by_all_interface_companies"),
        ),
        "core_activity_ranking": ranked(
            "n_core_episode_count",
            ("n_core_event_rows", "n_core_companies", "core_episode_count_rank"),
        ),
        "acquisition_exit_ranking": ranked(
            "n_exit_episode_count",
            ("exit_share_of_core_episodes",),
        ),
        "strategic_investment_ranking": ranked("n_strategic_investment_episode_count"),
        "governance_and_ownership_ranking": ranked(
            "n_governance_and_ownership_episode_count",
            (
                "n_board_change_episode_count",
                "n_major_holder_change_episode_count",
                "n_secondary_transaction_episode_count",
                "n_activist_action_episode_count",
            ),
        ),
        "follow_on_funding_ranking": ranked("n_funding_participation_rows"),
        "funding_to_exit_ranking": ranked("n_funding_to_exit_companies"),
        "duplicate_sensitivity": {
            "episode_definition": "investor + company + core event type + event date; undated rows remain separate by event ID",
            "candidates_with_duplicate_core_source_rows": [
                {
                    "investor_id": row["investor_id"],
                    "investor_name": row["investor_name"],
                    "n_duplicate_core_source_rows": row["n_duplicate_core_source_rows"],
                }
                for row in comparison_table if row["n_duplicate_core_source_rows"] > 0
            ],
            "legacy_raw_gate_context": support_analysis["legacy_gate_duplicate_sensitive_investor_ids"],
        },
        "comparative_observations": {
            "breadth_leaders": matching_names(
                "n_all_interface_companies", maximum("n_all_interface_companies")
            ),
            "core_episode_leaders": matching_names(
                "n_core_episode_count", maximum("n_core_episode_count")
            ),
            "majority_exit_composition_candidates": [
                row["investor_name"] for row in comparison_table
                if row["exit_share_of_core_episodes"] > 0.5
            ],
            "governance_or_ownership_activity_candidates": [
                row["investor_name"] for row in comparison_table
                if row["n_governance_and_ownership_episode_count"] > 0
            ],
            "strategic_investment_activity_candidates": [
                row["investor_name"] for row in comparison_table
                if row["n_strategic_investment_episode_count"] > 0
            ],
            "observed_funding_to_exit_candidates": [
                row["investor_name"] for row in comparison_table
                if row["n_funding_to_exit_companies"] > 0
            ],
            "core_activity_concentrated_below_all_company_breadth": [
                row["investor_name"] for row in comparison_table
                if row["n_core_companies"] < row["n_all_interface_companies"]
            ],
        },
        "evidence_by_investor": evidence_by_investor,
        "interpretation_limits": [
            "The comparison is descriptive and does not estimate causal post-investment effects.",
            "The company threshold uses all Interface companies, while core episodes may be concentrated in fewer companies.",
            "Observed row and episode counts reflect available source coverage and should not be treated as complete investor histories.",
        ],
    }

def table_with_metadata(rows: list[dict[str, Any]], artifact: str) -> pa.Table:
    if not rows:
        raise ValueError(f"refusing to write empty {artifact}")
    table = pa.Table.from_pylist(rows)
    metadata = dict(table.schema.metadata or {})
    metadata.update({b"schema_version": SCHEMA_VERSION.encode(), b"artifact": artifact.encode()})
    return table.replace_schema_metadata(metadata)


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    paths = {
        "investors": Path(args.investor_path).expanduser().resolve(),
        "funding": Path(args.funding_path).expanduser().resolve(),
        "interface": Path(args.interface_path).expanduser().resolve(),
        "companies": Path(args.company_path).expanduser().resolve(),
    }
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"missing {name} input: {path}")
    output_dir = validate_output_dir(Path(args.output_dir), list(paths.values()))
    protected_hashes = {name: sha256_file(path) for name, path in paths.items()}
    tables = {name: pq.read_table(path) for name, path in paths.items()}
    require_columns(tables["investors"], {"investor_id", "name", "investor_type"}, paths["investors"])
    require_columns(tables["funding"], {"event_id", "company_id", "investor_id", "announce_date", "source_url"}, paths["funding"])
    require_columns(tables["interface"], {"event_id", "company_id", "investor_id", "raw_investor_name", "event_date", "event_type", "event_subtype", "title", "summary", "source_url"}, paths["interface"])
    require_columns(tables["companies"], {"company_id", "company_canonical_name", "label_v4", "outcome_label_v4"}, paths["companies"])

    source_rows = {name: table.to_pylist() for name, table in tables.items()}
    investor_by_id = {row["investor_id"]: row for row in source_rows["investors"]}
    company_by_id = {row["company_id"]: row for row in source_rows["companies"]}
    if len(investor_by_id) != len(source_rows["investors"]):
        raise ValueError("duplicate investor_id in investor entity")
    if len(company_by_id) != len(source_rows["companies"]):
        raise ValueError("duplicate company_id in company entity")

    evidence_by_pair, evidence_counts = investment_evidence_rows(
        source_rows["funding"], source_rows["interface"], set(investor_by_id), set(company_by_id),
    )
    event_rows = build_event_rows(source_rows["interface"], investor_by_id, company_by_id, evidence_by_pair)
    profiles = build_profiles(event_rows, investor_by_id)
    descriptive_summary = build_descriptive_summary(event_rows, profiles)
    support_metrics = build_support_metrics(event_rows)
    support_analysis = build_support_analysis(support_metrics)
    primary_candidate_comparison = build_primary_candidate_comparison(support_analysis, event_rows)
    status_counts = Counter(row["temporal_relation_status"] for row in event_rows)
    coverage = {
        "schema_version": SCHEMA_VERSION,
        "total_interface_rows": len(source_rows["interface"]),
        "resolved_investor_interface_rows": len(event_rows),
        "distinct_resolved_investors": len(profiles),
        "distinct_resolved_investor_company_pairs": len({(row["investor_id"], row["company_id"]) for row in event_rows}),
        "temporal_relation_status_counts": dict(sorted(status_counts.items())),
        "investors_with_confirmed_post_investment_rows": sum(profile["n_confirmed_post_investment_rows"] > 0 for profile in profiles),
        "confirmed_post_investment_rows": status_counts["CONFIRMED_POST_INVESTMENT"],
        "confirmed_post_investment_pairs": len({(row["investor_id"], row["company_id"]) for row in event_rows if row["temporal_relation_status"] == "CONFIRMED_POST_INVESTMENT"}),
        "investment_evidence_counts": dict(sorted(evidence_counts.items())),
        "method": {
            "prior_relation": "investment interval upper bound is earlier than Interface event interval lower bound",
            "partial_date_intervals": {"year": "Jan-01..Dec-31", "month": "month first..last day", "day": "exact"},
            "investment_sources": ["funding_events", "interface_funding_participation"],
            "filtering": "none for event and profile outputs",
            "post_investment_support_gate": "none",
            "behavior_profile_support_gate": PRIMARY_SUPPORT_GATE,
            "behavior_profile_candidate_generation": "all resolved investors; no investor-name filter",
            "clustering": "none",
            "classification": "none",
        },
        "behavior_support_candidate_counts": {
            "primary_episode_gate_3_3": len(support_analysis["primary_gate_candidates"]),
            "legacy_raw_gate_5_3": len(support_analysis["legacy_raw_gate_candidates"]),
            "legacy_episode_gate_5_3": len(support_analysis["legacy_episode_dedup_gate_candidates"]),
        },
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=output_dir.name + ".tmp-", dir=output_dir.parent))
    try:
        outputs = {
            "investor_company_post_investment_events_v0.1.parquet": table_with_metadata(event_rows, "investor_company_post_investment_events"),
            "investor_post_investment_profiles_v0.1.parquet": table_with_metadata(profiles, "investor_post_investment_profiles"),
            "investor_behavior_support_metrics_v0.1.parquet": table_with_metadata(support_metrics, "investor_behavior_support_metrics"),
        }
        output_locks = {}
        for name, table in outputs.items():
            path = staging / name
            pq.write_table(table, path, **PARQUET_OPTIONS)
            reread = pq.read_table(path)
            if reread.num_rows != table.num_rows or not reread.schema.equals(table.schema, check_metadata=True):
                raise RuntimeError(f"serialized output validation failed: {name}")
            output_locks[name] = {"rows": table.num_rows, "columns": table.num_columns, "sha256": sha256_file(path)}
        coverage_path = staging / "coverage_v0.1.json"
        coverage_path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        output_locks[coverage_path.name] = {"rows": 1, "columns": len(coverage), "sha256": sha256_file(coverage_path)}
        json_artifacts = {
            "post_investment_descriptive_summary_v0.1.json": descriptive_summary,
            "behavior_support_sensitivity_v0.1.json": support_analysis,
            "primary_candidate_behavior_comparison_v0.1.json": primary_candidate_comparison,
        }
        for name, payload in json_artifacts.items():
            path = staging / name
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")
            output_locks[name] = {"rows": 1, "columns": len(payload), "sha256": sha256_file(path)}
        manifest = {
            "schema_version": "investor-post-investment-behavior-manifest-v0.1.0",
            "inputs": {name: {"path": str(path), "rows": tables[name].num_rows, "sha256": protected_hashes[name]} for name, path in paths.items()},
            "outputs": output_locks,
            "statistics": coverage,
            "runtime": {"pyarrow_version": pa.__version__, "network_access": "none", "parquet_options": PARQUET_OPTIONS},
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        for name, path in paths.items():
            if sha256_file(path) != protected_hashes[name]:
                raise RuntimeError(f"protected input changed: {name}")
        os.replace(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return coverage


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--investor-path", required=True)
    parser.add_argument("--funding-path", required=True)
    parser.add_argument("--interface-path", required=True)
    parser.add_argument("--company-path", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    coverage = materialize(parse_args(argv))
    print(json.dumps(coverage, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
