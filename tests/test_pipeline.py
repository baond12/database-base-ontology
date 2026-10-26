import copy
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from database_base_ontology.base_builder import build_base_ontologies
from database_base_ontology.common import artifact_path, fingerprint, load_config, load_json, save_json
from database_base_ontology.llm_client import configure_proxy, parse_json_response
from database_base_ontology.pipeline import run_pipeline, validate_ontology
from database_base_ontology.reference_builder import build_reference_contexts
from database_base_ontology.relation_builder import physical_relation
from database_base_ontology.schema_reader import read_schema
from database_base_ontology.validators import validate_base_proposal


class FixtureClient:
    """Test-only responses; never used as a production fallback."""

    identity = {"model": "fixture-only", "base_url": "http://fixture.invalid"}

    def __init__(self):
        self.calls = []

    def generation_fingerprint(self, task):
        return fingerprint({"task": task, "fixture": 1})

    def generate(self, task, context, trace_path, validate):
        self.calls.append((task, copy.deepcopy(context)))
        if task == "build_base_ontology":
            table = context["table"]
            result = {"class_name": "".join(part.capitalize() for part in table["name"].split("_")),
                      "description": table["description"] or "Fixture description", "confidence": 0.5,
                      "properties": [{"column_name": c["name"], "name": "p" + str(i),
                                      "description": c["description"] or "Fixture property", "confidence": 0.5}
                                     for i, c in enumerate(table["columns"])],
                      "methods": [{"name": "count", "description": "Count rows", "operation": "count",
                                   "input_parameters": [], "output": {"kind": "scalar", "source_columns": []},
                                   "source_columns": [], "confidence": 0.5}], "assumptions": []}
        else:
            result = {"relations": [{"relation_id": fk["relation_id"], "name": "referencesParent",
                                     "description": "Fixture FK description", "confidence": 0.5, "assumptions": []}
                                    for fk in context["owned_foreign_keys"]]}
        validate(result)
        save_json(trace_path, {"fixture": True, "response": result})
        return result


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db = self.root / "fixture.sqlite3"
        with sqlite3.connect(self.db) as connection:
            connection.executescript('''
                PRAGMA foreign_keys=ON;
                CREATE TABLE parents(a TEXT NOT NULL, b TEXT NOT NULL, PRIMARY KEY(a,b));
                CREATE TABLE children(id INTEGER PRIMARY KEY, a TEXT, b TEXT,
                  FOREIGN KEY(a,b) REFERENCES parents, UNIQUE(a,b));
                CREATE TABLE partial_children(id INTEGER PRIMARY KEY, a TEXT NOT NULL, b TEXT NOT NULL,
                  FOREIGN KEY(a,b) REFERENCES parents(a,b));
                CREATE UNIQUE INDEX partial_key ON partial_children(a,b) WHERE id>0;
                CREATE TABLE nodes(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES nodes(id));
                CREATE TABLE isolated(id TEXT PRIMARY KEY, value INTEGER,
                  doubled INTEGER GENERATED ALWAYS AS (value*2) STORED);
            ''')
        self.config = {"database_id": "fixture", "sqlite_path": self.db, "metadata_path": None,
                       "output_dir": self.root / "output", "run_id": "test", "llm": {}}
        self.client = FixtureClient()

    def tearDown(self):
        self.temporary.cleanup()

    def inventory(self):
        return read_schema(self.db, "fixture", self.config["metadata_path"])

    def test_read_only_and_optional_empty_metadata(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        inventory = self.inventory()
        self.assertEqual(inventory["counts"], {"tables": 5, "columns": 13, "foreign_keys": 3})
        self.assertTrue(all(table["description"] == "" for table in inventory["tables"]))
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        metadata = self.root / "empty.json"
        save_json(metadata, {})
        self.assertEqual(inventory["schema_fingerprint"], read_schema(self.db, "fixture", metadata)["schema_fingerprint"])

    def test_metadata_preserved_and_no_data_value_sampling(self):
        metadata = self.root / "metadata.json"
        save_json(metadata, {"tables": {"nodes": {"description": "A hierarchy", "columns": {"parent_id": {"description": "Parent node"}}}}})
        self.config["metadata_path"] = metadata
        run_pipeline(self.config, client=self.client)
        node = load_json(artifact_path(self.config["output_dir"] / "base_ontologies", "nodes"))
        self.assertEqual(node["class"]["metadata_description"], "A hierarchy")
        self.assertEqual(node["properties"][1]["metadata_description"], "Parent node")
        self.assertNotIn("sample_values", json.dumps(self.client.calls))

    def test_unknown_metadata_column_rejected(self):
        path = self.root / "metadata.json"
        save_json(path, {"tables": {"nodes": {"columns": {"ghost": {"description": "x"}}}}})
        with self.assertRaisesRegex(ValueError, "unknown columns"):
            read_schema(self.db, "fixture", path)

    def test_null_descriptions_are_empty_metadata(self):
        path = self.root / "metadata.json"
        save_json(path, {"tables": {"nodes": {"description": None, "columns": {"parent_id": {"description": None}}}}})
        self.assertEqual(read_schema(self.db, "fixture", path)["schema_fingerprint"], self.inventory()["schema_fingerprint"])

    def test_composite_fk_cardinality_and_partial_unique(self):
        tables = {table["name"]: table for table in self.inventory()["tables"]}
        child = tables["children"]["foreign_keys"][0]
        self.assertEqual(child["target_columns"], ["a", "b"])
        relation = physical_relation(child, tables)
        self.assertEqual(relation["cardinality"], {"source_to_target": {"min": 0, "max": 1}, "target_to_source": {"min": 0, "max": 1}})
        partial = physical_relation(tables["partial_children"]["foreign_keys"][0], tables)
        self.assertEqual(partial["cardinality"], {"source_to_target": {"min": 1, "max": 1}, "target_to_source": {"min": 0, "max": "many"}})

    def test_sqlite_primary_key_nullability_and_generated_column(self):
        tables = {table["name"]: table for table in self.inventory()["tables"]}
        self.assertFalse(tables["nodes"]["columns"][0]["nullable"])
        self.assertTrue(tables["isolated"]["columns"][0]["nullable"])
        self.assertTrue(tables["isolated"]["columns"][2]["generated"])

    def test_fk_identifier_case_matches_sqlite_identifiers(self):
        path = self.root / "mixed.sqlite3"
        with sqlite3.connect(path) as connection:
            connection.executescript('CREATE TABLE Parent(id INTEGER PRIMARY KEY); '
                                     'CREATE TABLE Child(id INTEGER PRIMARY KEY, parent_id INTEGER, '
                                     'FOREIGN KEY(PARENT_ID) REFERENCES PARENT(ID));')
        inventory = read_schema(path, "mixed")
        fk = next(t for t in inventory["tables"] if t["name"] == "Child")["foreign_keys"][0]
        self.assertEqual(fk["target_table"], "Parent")
        self.assertEqual(fk["source_columns"], ["parent_id"])
        self.assertEqual(fk["target_columns"], ["id"])

    def test_end_to_end_and_single_canonical_self_relation(self):
        report = run_pipeline(self.config, client=self.client)
        self.assertEqual(report["structural_status"], "PASS")
        self.assertEqual(report["counts"], {"classes": 5, "properties": 13, "methods": 5, "relations": 3})
        context = load_json(artifact_path(self.config["output_dir"] / "reference_contexts", "nodes"))
        self.assertEqual(context["references"], [])
        self.assertEqual(len(context["foreign_keys"]), 1)
        parent = load_json(artifact_path(self.config["output_dir"] / "reference_contexts", "parents"))
        self.assertEqual(len(parent["references"]), 2)
        self.assertEqual(len(self.client.calls), 8)
        self.assertEqual(report["runtime_readiness"], "NOT_ASSESSED")

    def test_resume_uses_cache_and_metadata_invalidates_dependents(self):
        run_pipeline(self.config, client=self.client)
        before = len(self.client.calls)
        run_pipeline(self.config, client=self.client)
        self.assertEqual(len(self.client.calls), before)
        path = self.root / "metadata.json"
        save_json(path, {"tables": {"parents": {"description": "Updated parent description"}}})
        self.config["metadata_path"] = path
        run_pipeline(self.config, client=self.client)
        new_calls = self.client.calls[before:]
        self.assertEqual(sum(task == "build_base_ontology" for task, _ in new_calls), 1)
        self.assertEqual(sum(task == "describe_relations" for task, _ in new_calls), 2)

    def test_relation_builder_requires_all_base_classes(self):
        inventory = self.inventory()
        build_base_ontologies(self.config, inventory, client=self.client, tables=["nodes"])
        with self.assertRaisesRegex(ValueError, "complete module 1"):
            build_reference_contexts(self.config, inventory)

    def test_cross_table_method_and_property_hallucination_rejected(self):
        table = self.inventory()["tables"][0]
        valid = self.client.generate("build_base_ontology", {"table": table}, self.root / "t.json", lambda result: None)
        invalid = copy.deepcopy(valid)
        invalid["methods"][0]["source_columns"] = ["parents.a"]
        with self.assertRaisesRegex(ValueError, "cross-table"):
            validate_base_proposal(invalid, table)
        invalid = copy.deepcopy(valid)
        invalid["properties"].pop()
        with self.assertRaisesRegex(ValueError, "coverage mismatch"):
            validate_base_proposal(invalid, table)

    def test_lookup_cannot_assume_unique_fk_filter(self):
        table = next(t for t in self.inventory()["tables"] if t["name"] == "nodes")
        proposal = self.client.generate("build_base_ontology", {"table": table}, self.root / "t.json", lambda value: None)
        method = proposal["methods"][0]
        method.update({"name": "findChild", "operation": "lookup", "source_columns": ["parent_id"],
                       "input_parameters": [{"name": "parentId", "type": "integer", "required": True, "source_columns": ["parent_id"]}],
                       "output": {"kind": "object", "source_columns": ["parent_id"]}})
        with self.assertRaisesRegex(ValueError, "full PK/UNIQUE"):
            validate_base_proposal(proposal, table)
        method["input_parameters"][0]["source_columns"] = ["id"]
        method["source_columns"] = ["id", "parent_id"]
        validate_base_proposal(proposal, table)

    def test_mutated_relation_cardinality_is_detected(self):
        run_pipeline(self.config, client=self.client)
        path = self.config["output_dir"] / "relations.json"
        relations = load_json(path)
        relations["relations"][0]["cardinality"]["source_to_target"]["min"] = 99
        save_json(path, relations)
        report = validate_ontology(self.config)
        self.assertEqual(report["structural_status"], "FAIL")
        self.assertTrue(any("Altered FK" in f["reason"] for f in report["findings"]))

    def test_stale_schema_is_detected(self):
        run_pipeline(self.config, client=self.client)
        with sqlite3.connect(self.db) as connection:
            connection.execute("ALTER TABLE isolated ADD COLUMN extra TEXT")
        report = validate_ontology(self.config)
        self.assertEqual(report["structural_status"], "FAIL")

    def test_proxy_modes_and_json_parsing(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(configure_proxy({"proxy_mode": "company"}))
            self.assertEqual(os.environ["https_proxy"], "http://proxy.hcm.fpt.vn:80")
            self.assertEqual(os.environ["NO_PROXY"], "")
            self.assertFalse(configure_proxy({"proxy_mode": "none"}))
        self.assertEqual(parse_json_response('```json\n{"a":1}\n```'), {"a": 1})
        with self.assertRaises(ValueError):
            parse_json_response('[]')


if __name__ == "__main__":
    unittest.main()
