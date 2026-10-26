from importlib.resources import files

from jsonschema import Draft202012Validator

from .common import class_id, fingerprint, has_unique_key, load_json, property_id


def contract(name):
    return load_json(files("database_base_ontology").joinpath("contracts", name + ".schema.json"))


def validate_contract(name, data):
    errors = sorted(Draft202012Validator(contract(name)).iter_errors(data), key=lambda e: str(e.path))
    if errors:
        raise ValueError("; ".join(f"{'.'.join(map(str, e.path))}: {e.message}" for e in errors[:5]))


def unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")


def validate_base_proposal(proposal, table):
    validate_contract("base_proposal", proposal)
    actual = [p["column_name"] for p in proposal["properties"]]
    expected = {column["name"] for column in table["columns"]}
    unique(actual, "columns in LLM response")
    if set(actual) != expected:
        raise ValueError(f"Property coverage mismatch: missing={expected - set(actual)}, extra={set(actual) - expected}")
    unique([p["name"] for p in proposal["properties"]], "property names")
    unique([method["name"] for method in proposal["methods"]], "method names")
    errors = []
    for method in proposal["methods"]:
        unique([parameter["name"] for parameter in method["input_parameters"]], "parameter names")
        referenced = set(method["output"]["source_columns"])
        for parameter in method["input_parameters"]:
            referenced.update(parameter["source_columns"])
        if not referenced <= set(method["source_columns"]):
            errors.append(f"Method {method['name']} does not declare all input/output source_columns")
        if not set(method["source_columns"]) <= expected:
            errors.append(f"Method {method['name']} references unknown or cross-table columns")
        if method["operation"] == "lookup":
            filters = {column for parameter in method["input_parameters"] if parameter["required"]
                       for column in parameter["source_columns"]}
            if method["output"]["kind"] != "object" or not has_unique_key(table, filters):
                errors.append(f"Lookup {method['name']} must bind a full PK/UNIQUE key with required parameters. "
                              "Otherwise use operation=list and output.kind=rows, not a unique object assumption.")
        if method["operation"] == "list" and method["output"]["kind"] != "rows":
            errors.append(f"List {method['name']} must return rows")
        if method["operation"] == "count" and method["output"]["kind"] != "scalar":
            errors.append(f"Count {method['name']} must return a scalar")
    if errors:
        raise ValueError("; ".join(errors))


def validate_base(ontology, table, database_id):
    validate_contract("base_ontology", ontology)
    if ontology["database_id"] != database_id or ontology["class"]["class_id"] != class_id(table["name"]):
        raise ValueError("Class identity does not match database/table")
    if ontology["class"]["source_table"] != table["name"]:
        raise ValueError("Class source_table mismatch")
    if ontology["class"]["semantic_status"] != "PROPOSED":
        raise ValueError("Generated class semantics must remain PROPOSED")
    if ontology["class"]["metadata_description"] != table["description"]:
        raise ValueError("Table metadata was altered")
    if ontology["provenance"]["input_fingerprint"] != fingerprint({"database_id": database_id, "table": table}):
        raise ValueError(f"Stale base ontology for {table['name']}; rebuild module 1")
    props = ontology["properties"]
    unique([p["source_column"] for p in props], "physical properties")
    if {p["source_column"] for p in props} != {c["name"] for c in table["columns"]}:
        raise ValueError("Base property coverage mismatch")
    physical = {column["name"]: column for column in table["columns"]}
    fk_columns = {column for fk in table["foreign_keys"] for column in fk["source_columns"]}
    for prop in props:
        column = physical[prop["source_column"]]
        expected = {"property_id": property_id(table["name"], column["name"]), "source_table": table["name"],
                    "data_type": column["data_type"], "nullable": column["nullable"],
                    "primary_key_position": column["primary_key_position"],
                    "default": column["default"], "generated": column["generated"],
                    "is_foreign_key": column["name"] in fk_columns, "metadata_description": column["description"]}
        if any(prop.get(key) != value for key, value in expected.items()):
            raise ValueError(f"Physical property mismatch: {prop['property_id']}")
    for method in ontology["methods"]:
        if (method["owner_class_id"] != class_id(table["name"]) or method["source_table"] != table["name"]
                or method["scope"] != "BASE_CLASS" or method["status"] != "PROPOSED_READ_ONLY"
                or method["method_id"] != "method:" + table["name"] + "." + method["name"]):
            raise ValueError("Method owner, scope, status or ID mismatch")
    proposal = {"class_name": ontology["class"]["name"], "description": ontology["class"]["description"],
                "confidence": ontology["class"]["confidence"], "assumptions": ontology["assumptions"],
                "properties": [{"column_name": p["source_column"], "name": p["name"],
                                "description": p["description"], "confidence": p["confidence"]} for p in props],
                "methods": [{key: m[key] for key in ("name", "description", "operation", "input_parameters",
                                                       "output", "source_columns", "confidence")} for m in ontology["methods"]]}
    validate_base_proposal(proposal, table)


def validate_relation_proposal(proposal, foreign_keys):
    validate_contract("relation_proposal", proposal)
    ids = [relation["relation_id"] for relation in proposal["relations"]]
    unique(ids, "relation descriptions")
    if set(ids) != {fk["relation_id"] for fk in foreign_keys}:
        raise ValueError("Relation descriptions do not exactly cover owned FK IDs")
