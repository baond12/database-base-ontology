import argparse
import platform
import sqlite3
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import version

import requests

from . import __version__
from .base_builder import build_base_ontologies, load_base_ontologies
from .common import artifact_path, class_id, fingerprint, load_config, load_json, save_json
from .reference_builder import build_reference_contexts, make_context
from .relation_builder import build_relations, physical_relation
from .schema_reader import extract_schema, read_schema
from .validators import unique, validate_contract


def validate_ontology(config, inventory=None):
    output = config["output_dir"]
    inventory = inventory or load_json(output / "schema_inventory.json")
    findings = []
    current = read_schema(config["sqlite_path"], config["database_id"], config["metadata_path"])
    if inventory["schema_fingerprint"] != current["schema_fingerprint"]:
        findings.append({"severity": "HIGH", "reason": "Schema or metadata changed; run extraction and rebuild"})
    bases = load_base_ontologies(output, inventory)
    for table in inventory["tables"]:
        context = load_json(artifact_path(output / "reference_contexts", table["name"]))
        if context != make_context(table["name"], inventory, bases):
            findings.append({"severity": "HIGH", "reason": f"Stale/altered reference context: {table['name']}"})
    result = load_json(output / "relations.json")
    tables = {table["name"]: table for table in inventory["tables"]}
    expected = {fk["relation_id"]: physical_relation(fk, tables) for table in inventory["tables"] for fk in table["foreign_keys"]}
    unique([row["relation_id"] for row in result["relations"]], "global relations")
    actual = {row["relation_id"]: row for row in result["relations"]}
    if set(expected) != set(actual):
        findings.append({"severity": "HIGH", "reason": "Physical FK relation coverage mismatch"})
    for rid, row in actual.items():
        validate_contract("relation", row)
        if rid in expected and any(row.get(key) != value for key, value in expected[rid].items()):
            findings.append({"severity": "HIGH", "reason": f"Altered FK/cardinality/status: {rid}"})
    if result["base_fingerprint"] != fingerprint(bases) or result["schema_fingerprint"] != inventory["schema_fingerprint"]:
        findings.append({"severity": "HIGH", "reason": "Relations were generated from stale inputs"})
    ontology = load_json(output / "ontology.json")
    if (ontology.get("database_id") != inventory["database_id"]
            or ontology.get("schema_fingerprint") != inventory["schema_fingerprint"]
            or ontology.get("semantic_review_status") != "PENDING"
            or ontology.get("runtime_readiness") != "NOT_ASSESSED"):
        findings.append({"severity": "HIGH", "reason": "Merged ontology identity or approval status was altered"})
    expected_parts = {"classes": [b["class"] for b in bases.values()],
                      "properties": [p for b in bases.values() for p in b["properties"]],
                      "methods": [m for b in bases.values() for m in b["methods"]], "relations": result["relations"]}
    for key, rows in expected_parts.items():
        if ontology.get(key) != rows:
            findings.append({"severity": "HIGH", "reason": f"Merged ontology differs from source artifacts: {key}"})
    unique([row["class_id"] for row in ontology["classes"]], "class IDs")
    unique([row["name"] for row in ontology["classes"]], "class names; review names of similarly named tables")
    unique([row["property_id"] for row in ontology["properties"]], "property IDs")
    unique([row["method_id"] for row in ontology["methods"]], "method IDs")
    for name, base in bases.items():
        full = load_json(artifact_path(output / "class_ontologies", name))
        expected_full = {**base, "relations": [row for row in result["relations"] if class_id(name) in
                                               {row["source_class_id"], row["target_class_id"]}]}
        if full != expected_full:
            findings.append({"severity": "HIGH", "reason": f"Per-class ontology differs: {name}"})
    report = {"database_id": inventory["database_id"], "structural_status": "PASS" if not findings else "FAIL",
              "semantic_review_status": "PENDING", "runtime_readiness": "NOT_ASSESSED",
              "counts": {key: len(rows) for key, rows in expected_parts.items()}, "findings": findings,
              "scope_note": "Checks physical coverage, identity, single-table method references, direct FK context and cardinality. "
                            "Does not prove business semantics, method executability or generated SQL correctness."}
    save_json(output / "validation_report.json", report)
    return report


def run_pipeline(config, client=None, force=False):
    started = time.monotonic()
    inventory = extract_schema(config)
    build_base_ontologies(config, inventory, client=client, force=force)
    build_reference_contexts(config, inventory)
    build_relations(config, inventory, client=client, force=force)
    report = validate_ontology(config, inventory)
    manifest = {"database_id": config["database_id"], "run_id": config["run_id"],
                "version": __version__, "python_version": platform.python_version(),
                "dependency_versions": {name: version(name) for name in ("requests", "PyYAML", "jsonschema")},
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "source": inventory["source"], "schema_fingerprint": inventory["schema_fingerprint"],
                "base_generation_fingerprints": sorted({b["provenance"]["generation_fingerprint"]
                                                        for b in load_base_ontologies(config["output_dir"], inventory).values()}),
                "output_dir": str(config["output_dir"]), "validation": report}
    save_json(config["output_dir"] / "run_manifest.json", manifest)
    return report


def main(step="run"):
    parser = argparse.ArgumentParser(description="Build base ontologies and direct FK relations")
    parser.add_argument("--config", required=True, help="Pipeline YAML file")
    parser.add_argument("--force", action="store_true", help="Regenerate LLM annotations instead of using valid cache")
    if step == "base":
        parser.add_argument("--tables", nargs="+", help="Smoke-test selected tables; module 2 still requires all tables")
    args = parser.parse_args()
    config = load_config(args.config)
    save_json(config["output_dir"] / "run_status.json", {"step": step, "status": "RUNNING",
                                                        "timestamp_utc": datetime.now(timezone.utc).isoformat()})
    try:
        if step == "extract":
            result = extract_schema(config)["counts"]
        elif step == "run":
            result = run_pipeline(config, force=args.force)
        else:
            inventory = load_json(config["output_dir"] / "schema_inventory.json")
            if step == "base":
                result = {"base_classes": len(build_base_ontologies(config, inventory, tables=args.tables, force=args.force))}
            elif step == "references":
                result = {"reference_contexts": len(build_reference_contexts(config, inventory))}
            elif step == "relations":
                result = {"relations": len(build_relations(config, inventory, force=args.force)["relations"])}
            else:
                result = validate_ontology(config, inventory)
    except (ValueError, KeyError, OSError, sqlite3.Error, requests.RequestException) as error:
        failure = {"step": step, "status": "FAILED", "error_type": type(error).__name__,
                   "reason": str(error), "timestamp_utc": datetime.now(timezone.utc).isoformat()}
        save_json(config["output_dir"] / "last_error.json", failure)
        save_json(config["output_dir"] / "run_status.json", failure)
        print(f"FAILED {step}: {error}", file=sys.stderr)
        return 1
    import json
    failed = result.get("structural_status") == "FAIL"
    save_json(config["output_dir"] / "run_status.json", {"step": step, "status": "FAILED" if failed else "COMPLETED",
                                                        "timestamp_utc": datetime.now(timezone.utc).isoformat()})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failed else 0
