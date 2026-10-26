from .base_builder import load_base_ontologies
from .common import artifact_path, class_id, fingerprint, has_unique_key, load_json, property_id, save_json
from .llm_client import DC5Client
from .reference_builder import make_context
from .validators import validate_contract, validate_relation_proposal


def physical_relation(fk, tables):
    source = tables[fk["source_table"]]
    nullable = {column["name"]: column["nullable"] for column in source["columns"]}
    optional = any(nullable[name] for name in fk["source_columns"])
    return {"relation_id": fk["relation_id"], "source_class_id": class_id(fk["source_table"]),
            "target_class_id": class_id(fk["target_table"]),
            "source_property_ids": [property_id(fk["source_table"], c) for c in fk["source_columns"]],
            "target_property_ids": [property_id(fk["target_table"], c) for c in fk["target_columns"]],
            "cardinality": {"source_to_target": {"min": 0 if optional else 1, "max": 1},
                            "target_to_source": {"min": 0, "max": 1 if has_unique_key(source, fk["source_columns"]) else "many"}},
            "source_fk": fk, "physical_status": "VERIFIED_FROM_SCHEMA",
            "semantic_status": "PROPOSED", "runtime_contract_status": "NOT_APPROVED"}


def build_relations(config, inventory, client=None, force=False):
    output = config["output_dir"]
    bases = load_base_ontologies(output, inventory)
    contexts = {}
    for table in inventory["tables"]:
        path = artifact_path(output / "reference_contexts", table["name"])
        if not path.exists():
            raise ValueError(f"Missing module 2 context: {table['name']}")
        context = load_json(path)
        if context != make_context(table["name"], inventory, bases):
            raise ValueError(f"Stale or altered context: {table['name']}; rebuild module 2")
        contexts[table["name"]] = context
    owned_client = client is None
    client = client or DC5Client(config["llm"])
    generation = client.generation_fingerprint("describe_relations")
    relations = []
    tables = {table["name"]: table for table in inventory["tables"]}
    try:
        for name, table in tables.items():
            foreign_keys = table["foreign_keys"]
            if not foreign_keys:
                continue
            context = {"reference_context": contexts[name], "owned_foreign_keys": foreign_keys}
            input_hash = fingerprint(context)
            path = artifact_path(output / "relation_annotations", name)
            cached = load_json(path) if path.exists() and not force else None
            if (cached and cached.get("input_fingerprint") == input_hash
                    and cached.get("generation_fingerprint") == generation):
                proposal = cached["proposal"]
                validate_relation_proposal(proposal, foreign_keys)
                print(f"CACHED relations: {name}", flush=True)
            else:
                print(f"BUILD relations: {name} ({len(foreign_keys)} owned FK)", flush=True)
                trace_path = artifact_path(output / "llm_traces" / "relations", name)
                proposal = client.generate("describe_relations", context, trace_path,
                                           lambda result: validate_relation_proposal(result, foreign_keys))
                save_json(path, {"input_fingerprint": input_hash, "generation_fingerprint": generation,
                                 "proposal": proposal, "trace_path": str(trace_path), "llm": client.identity})
            annotations = {row["relation_id"]: row for row in proposal["relations"]}
            for fk in foreign_keys:
                relation = {**annotations[fk["relation_id"]], **physical_relation(fk, tables)}
                validate_contract("relation", relation)
                relations.append(relation)
    finally:
        if owned_client:
            client.close()
    result = {"database_id": inventory["database_id"], "schema_fingerprint": inventory["schema_fingerprint"],
              "base_fingerprint": fingerprint(bases), "relations": sorted(relations, key=lambda r: r["relation_id"])}
    save_json(output / "relations.json", result)
    for name, base in bases.items():
        save_json(artifact_path(output / "class_ontologies", name), {**base,
                  "relations": [row for row in result["relations"] if class_id(name) in
                                {row["source_class_id"], row["target_class_id"]}]})
    ontology = {"database_id": inventory["database_id"], "schema_fingerprint": inventory["schema_fingerprint"],
                "classes": [base["class"] for base in bases.values()],
                "properties": [prop for base in bases.values() for prop in base["properties"]],
                "methods": [method for base in bases.values() for method in base["methods"]],
                "relations": result["relations"], "semantic_review_status": "PENDING",
                "runtime_readiness": "NOT_ASSESSED"}
    save_json(output / "ontology.json", ontology)
    return ontology
