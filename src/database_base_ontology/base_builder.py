from .common import artifact_path, class_id, fingerprint, load_json, property_id, save_json
from .llm_client import DC5Client
from .validators import unique, validate_base, validate_base_proposal


def build_base_ontologies(config, inventory, client=None, tables=None, force=False):
    owned_client = client is None
    client = client or DC5Client(config["llm"])
    output = config["output_dir"]
    selected = set(tables) if tables else {table["name"] for table in inventory["tables"]}
    unknown = selected - {table["name"] for table in inventory["tables"]}
    if unknown:
        raise ValueError(f"Unknown table selection: {sorted(unknown)}")
    generation = client.generation_fingerprint("build_base_ontology")
    results = {}
    try:
        for table in inventory["tables"]:
            name = table["name"]
            if name not in selected:
                continue
            path = artifact_path(output / "base_ontologies", name)
            context = {"database_id": inventory["database_id"], "table": table}
            input_hash = fingerprint(context)
            if path.exists() and not force:
                cached = load_json(path)
                if (cached["provenance"]["input_fingerprint"] == input_hash
                        and cached["provenance"]["generation_fingerprint"] == generation):
                    validate_base(cached, table, inventory["database_id"])
                    results[name] = cached
                    print(f"CACHED base: {name}", flush=True)
                    continue
            print(f"BUILD base: {name} ({len(table['columns'])} columns)", flush=True)
            trace_path = artifact_path(output / "llm_traces" / "base", name)
            proposal = client.generate("build_base_ontology", context, trace_path,
                                       lambda result: validate_base_proposal(result, table))
            annotations = {prop["column_name"]: prop for prop in proposal["properties"]}
            fk_columns = {column for fk in table["foreign_keys"] for column in fk["source_columns"]}
            result = {"database_id": inventory["database_id"],
                      "class": {"class_id": class_id(name), "name": proposal["class_name"],
                                "source_table": name, "description": proposal["description"],
                                "metadata_description": table["description"], "confidence": proposal["confidence"],
                                "semantic_status": "PROPOSED"},
                      "properties": [], "methods": [], "assumptions": proposal["assumptions"],
                      "provenance": {"input_fingerprint": input_hash, "generation_fingerprint": generation,
                                     "llm": client.identity, "trace_path": str(trace_path)}}
            for column in table["columns"]:
                annotation = annotations[column["name"]]
                result["properties"].append({"property_id": property_id(name, column["name"]),
                    "name": annotation["name"], "description": annotation["description"],
                    "confidence": annotation["confidence"], "source_table": name, "source_column": column["name"],
                    "data_type": column["data_type"], "nullable": column["nullable"],
                    "primary_key_position": column["primary_key_position"], "default": column["default"],
                    "generated": column["generated"], "is_foreign_key": column["name"] in fk_columns,
                    "metadata_description": column["description"]})
            for method in proposal["methods"]:
                result["methods"].append({**method, "method_id": "method:" + name + "." + method["name"],
                                          "owner_class_id": class_id(name), "source_table": name,
                                          "scope": "BASE_CLASS", "status": "PROPOSED_READ_ONLY"})
            validate_base(result, table, inventory["database_id"])
            save_json(path, result)
            results[name] = result
    finally:
        if owned_client:
            client.close()
    return results


def load_base_ontologies(output_dir, inventory):
    results = {}
    for table in inventory["tables"]:
        path = artifact_path(output_dir / "base_ontologies", table["name"])
        if not path.is_file():
            raise ValueError(f"Missing base ontology: {table['name']}; complete module 1 for all tables first")
        ontology = load_json(path)
        validate_base(ontology, table, inventory["database_id"])
        results[table["name"]] = ontology
    unique([base["class"]["name"] for base in results.values()], "class names; review colliding Class proposals")
    return results
