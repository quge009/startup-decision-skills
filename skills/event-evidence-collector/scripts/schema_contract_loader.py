"""Load v0.3 machine-readable JSON schema contracts into PyArrow."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa

_SIMPLE_TYPES = {
    "large_string": pa.large_string(),
    "int64": pa.int64(),
    "float64": pa.float64(),
    "double": pa.float64(),
}


def _arrow_type(type_name: str) -> pa.DataType:
    if type_name in _SIMPLE_TYPES:
        return _SIMPLE_TYPES[type_name]
    if type_name == "list<element: large_string>":
        return pa.list_(pa.field("element", pa.large_string()))
    raise ValueError(f"unsupported Arrow type in schema contract: {type_name!r}")


def _load_document(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"machine schema contract missing: {path}")
    document = json.loads(path.read_text())
    if not isinstance(document, dict):
        raise ValueError(f"schema contract must be a JSON object: {path}")
    return document


def load_event_type_contract(path: Path) -> tuple[list[str], dict[str, str]]:
    """Load ordered event enum and its authoritative semantic definitions."""
    document = _load_document(path)
    event_types = document.get("event_type_enum")
    definitions = document.get("event_type_definitions")
    if not isinstance(event_types, list) or not all(
        isinstance(value, str) and value for value in event_types
    ):
        raise ValueError(f"invalid event_type_enum in schema contract: {path}")
    if not isinstance(definitions, dict) or set(definitions) != set(event_types):
        raise ValueError(f"event_type_definitions must exactly cover enum: {path}")
    if not all(isinstance(definitions[value], str) and definitions[value].strip() for value in event_types):
        raise ValueError(f"invalid event_type definition in schema contract: {path}")
    return event_types, {value: definitions[value].strip() for value in event_types}


def load_schema_contract(path: Path) -> tuple[pa.Schema, set[str]]:
    """Load Arrow schema and event/outcome enum from a JSON contract."""
    document = _load_document(path)
    if document.get("format") != "pyarrow-schema-json-v1":
        raise ValueError(f"unsupported schema contract format in {path}")

    fields = []
    for item in document.get("fields", []):
        fields.append(pa.field(
            item["name"],
            _arrow_type(item["type"]),
            nullable=bool(item["nullable"]),
        ))
    if not fields:
        raise ValueError(f"schema contract has no fields: {path}")

    metadata_document = document.get("metadata", {})
    metadata_order = document.get("metadata_order")
    if not isinstance(metadata_order, list) or set(metadata_order) != set(metadata_document):
        raise ValueError(f"invalid metadata_order in schema contract: {path}")
    metadata = {
        str(key).encode(): str(metadata_document[key]).encode()
        for key in metadata_order
    }
    schema = pa.schema(fields, metadata=metadata)
    declared_version = document.get("schema_version")
    metadata_version = (schema.metadata or {}).get(b"schema_version", b"").decode()
    if declared_version != metadata_version:
        raise ValueError(
            f"schema_version mismatch in {path}: "
            f"document={declared_version!r}, metadata={metadata_version!r}"
        )

    event_types, _ = load_event_type_contract(path)
    return schema, set(event_types)
