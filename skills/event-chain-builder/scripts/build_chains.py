"""Materialize event chains from the three event Parquets.

One chain row represents one unique funding-level outcome. Funding participant
rows are grouped into a single chain end. Only dated exposure/interface events
can enter temporal windows; undated middle events remain in their source tables
and are reported as unassigned diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
from schema_contract_loader import load_schema_contract

SCHEMA_VERSION = "v0.3.0"

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas/chains_v0.3.schema.json"
CHAINS_SCHEMA, OUTCOME_TYPES = load_schema_contract(SCHEMA_PATH)


def _parse_date(value):
    if pd.isna(value) or str(value) in {"", "unknown", "nan", "None"}:
        return None
    parsed = pd.to_datetime(str(value)[:10], errors="coerce")
    return None if pd.isna(parsed) else parsed


def _nullable_string(value):
    return None if pd.isna(value) else str(value)


def _unique_non_null(values) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if pd.isna(value):
            continue
        value = str(value)
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _chain_id(
    company_id: str,
    outcome_type: str,
    outcome_round: str | None,
    end_date: str,
) -> str:
    key = "|".join([company_id, outcome_type, outcome_round or "", end_date])
    return "chain:" + hashlib.sha1(key.encode()).hexdigest()[:20]


def _require_columns(df: pd.DataFrame, columns: set[str], source: Path) -> None:
    missing = sorted(columns - set(df.columns))
    if missing:
        raise SystemExit(f"missing columns in {source}: {missing}")


def _middle_slice(df: pd.DataFrame | None, start_dt, end_dt) -> pd.DataFrame:
    if df is None or end_dt is None:
        return pd.DataFrame(columns=["event_id", "event_type", "_dt"])
    dated = df[df["_dt"].notna()]
    if start_dt is None:
        mask = dated["_dt"] < end_dt
    else:
        mask = (dated["_dt"] >= start_dt) & (dated["_dt"] < end_dt)
    return dated[mask].sort_values(["_dt", "event_id"], kind="stable")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Build event chains from funding, exposure, and interface Parquets")
    ap.add_argument("--entity-path", required=True)
    ap.add_argument("--funding-path", required=True)
    ap.add_argument("--exposure-path", required=True)
    ap.add_argument("--interface-path", required=True)
    ap.add_argument("--output-path", required=True)
    ap.add_argument("--force-output", action="store_true")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    entity_path = Path(args.entity_path).expanduser()
    funding_path = Path(args.funding_path).expanduser()
    exposure_path = Path(args.exposure_path).expanduser()
    interface_path = Path(args.interface_path).expanduser()
    output_path = Path(args.output_path).expanduser()

    if output_path.exists() and not args.force_output:
        raise SystemExit(
            f"refusing to overwrite existing output: {output_path} "
            "(pass --force-output to replace it)")
    for source in (entity_path, funding_path, exposure_path, interface_path):
        if not source.is_file():
            raise SystemExit(f"input parquet missing: {source}")

    entity = pq.read_table(entity_path).to_pandas()
    funding = pq.read_table(funding_path).to_pandas()
    exposure = pq.read_table(exposure_path).to_pandas()
    interface = pq.read_table(interface_path).to_pandas()

    _require_columns(entity, {
        "company_id", "company_canonical_name", "label_v4", "outcome_label_v4",
    }, entity_path)
    _require_columns(funding, {
        "event_id", "company_id", "investor_id", "raw_investor_name",
        "event_type", "round_name", "announce_date", "schema_version",
    }, funding_path)
    for frame, source in ((exposure, exposure_path), (interface, interface_path)):
        _require_columns(frame, {
            "event_id", "company_id", "event_type", "event_date", "schema_version",
        }, source)

    for frame, source in (
        (funding, funding_path), (exposure, exposure_path), (interface, interface_path),
    ):
        versions = set(frame["schema_version"].dropna().astype(str))
        if versions != {SCHEMA_VERSION}:
            raise SystemExit(f"unexpected schema versions in {source}: {sorted(versions)}")

    invalid_outcomes = sorted(set(funding["event_type"].dropna()) - OUTCOME_TYPES)
    if invalid_outcomes:
        raise SystemExit(f"unsupported funding outcome types: {invalid_outcomes}")

    entity_by_id = entity.set_index("company_id").to_dict("index")
    known_ids = set(entity_by_id)
    for frame, source in (
        (funding, funding_path), (exposure, exposure_path), (interface, interface_path),
    ):
        unknown = set(frame["company_id"].dropna()) - known_ids
        if unknown:
            raise SystemExit(f"unknown company_id values in {source}: {sorted(unknown)[:5]}")

    funding["_dt"] = funding["announce_date"].apply(_parse_date)
    exposure["_dt"] = exposure["event_date"].apply(_parse_date)
    interface["_dt"] = interface["event_date"].apply(_parse_date)

    per_company_exposure = {cid: group for cid, group in exposure.groupby("company_id")}
    per_company_interface = {cid: group for cid, group in interface.groupby("company_id")}

    outcome_key = ["company_id", "event_type", "round_name", "announce_date"]
    outcomes_by_company: dict[str, list[dict]] = {}
    for key, group in funding.groupby(outcome_key, dropna=False, sort=False):
        company_id, outcome_type, outcome_round, announce_date = key
        end_dt = _parse_date(announce_date)
        outcomes_by_company.setdefault(company_id, []).append({
            "outcome_type": str(outcome_type),
            "outcome_round": _nullable_string(outcome_round),
            "announce_date": _nullable_string(announce_date),
            "end_dt": end_dt,
            "participants": group,
        })

    rows = []
    for company_id, outcomes in outcomes_by_company.items():
        company = entity_by_id[company_id]
        outcomes.sort(key=lambda item: (
            item["end_dt"] is None,
            item["end_dt"] if item["end_dt"] is not None else pd.Timestamp.max,
            item["outcome_type"],
            item["outcome_round"] or "",
        ))
        previous_end = None
        company_exposure = per_company_exposure.get(company_id)
        company_interface = per_company_interface.get(company_id)

        for sequence, outcome in enumerate(outcomes, start=1):
            end_dt = outcome["end_dt"]
            end_date = str(end_dt.date()) if end_dt is not None else "unknown"
            start_date = str(previous_end.date()) if previous_end is not None else "founding"
            start_reason = (
                "previous_outcome" if previous_end is not None else "founding_or_prehistory"
            )
            window_status = "dated" if end_dt is not None else "unknown_end_date"
            exposure_middle = _middle_slice(company_exposure, previous_end, end_dt)
            interface_middle = _middle_slice(company_interface, previous_end, end_dt)
            participants = outcome["participants"]

            rows.append({
                "chain_id": _chain_id(
                    company_id, outcome["outcome_type"],
                    outcome["outcome_round"], end_date,
                ),
                "company_id": company_id,
                "company_canonical_name": str(company["company_canonical_name"]),
                "company_label_v4": str(company["label_v4"]),
                "company_outcome_label_v4": str(company["outcome_label_v4"]),
                "chain_sequence": sequence,
                "chain_start_date": start_date,
                "chain_start_reason": start_reason,
                "chain_end_date": end_date,
                "chain_window_status": window_status,
                "outcome_type": outcome["outcome_type"],
                "outcome_round": outcome["outcome_round"],
                "outcome_event_ids": _unique_non_null(participants["event_id"]),
                "n_participant_rows": len(participants),
                "investor_ids": _unique_non_null(participants["investor_id"]),
                "investor_raw_names": _unique_non_null(
                    participants["raw_investor_name"]),
                "n_exposure": len(exposure_middle),
                "exposure_event_ids": exposure_middle["event_id"].astype(str).tolist(),
                "exposure_types": exposure_middle["event_type"].astype(str).tolist(),
                "n_interface": len(interface_middle),
                "interface_event_ids": interface_middle["event_id"].astype(str).tolist(),
                "interface_types": interface_middle["event_type"].astype(str).tolist(),
                "schema_version": SCHEMA_VERSION,
            })
            if end_dt is not None:
                previous_end = end_dt

    table = pa.Table.from_pylist(rows, schema=CHAINS_SCHEMA)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, output_path)

    undated_exposure = int(exposure["_dt"].isna().sum())
    undated_interface = int(interface["_dt"].isna().sum())
    unknown_end_chains = sum(row["chain_window_status"] == "unknown_end_date" for row in rows)
    print(f"funding participant rows: {len(funding)}")
    print(f"unique chain outcomes: {table.num_rows}")
    print(f"chain companies: {len(outcomes_by_company)}")
    print(f"unknown-end chains: {unknown_end_chains}")
    print(f"unassigned undated exposure/interface: {undated_exposure}/{undated_interface}")
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
