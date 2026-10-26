from .base_builder import load_base_ontologies
from .common import artifact_path, class_id, fingerprint, save_json
from .validators import validate_contract


def make_context(table_name, inventory, bases):
    foreign_keys = [fk for table in inventory["tables"] for fk in table["foreign_keys"]
                    if table_name in {fk["source_table"], fk["target_table"]}]
    neighbors = {name for fk in foreign_keys for name in (fk["source_table"], fk["target_table"])} - {table_name}
    payload = {"database_id": inventory["database_id"], "class_id": class_id(table_name),
               "current": bases[table_name],
               "references": [{"class_id": class_id(name), "ontology": bases[name]} for name in sorted(neighbors)],
               "foreign_keys": foreign_keys}
    result = {**payload, "context_fingerprint": fingerprint(payload)}
    validate_contract("reference_context", result)
    return result


def build_reference_contexts(config, inventory):
    bases = load_base_ontologies(config["output_dir"], inventory)
    contexts = {}
    for table in inventory["tables"]:
        name = table["name"]
        context = make_context(name, inventory, bases)
        save_json(artifact_path(config["output_dir"] / "reference_contexts", name), context)
        contexts[name] = context
        print(f"CONTEXT: {name} ({len(context['references'])} direct references)", flush=True)
    return contexts
