import sqlite3
from collections import defaultdict
from pathlib import Path

from .common import file_sha256, fingerprint, load_json, quote_identifier, save_json


def read_schema(sqlite_path, database_id, metadata_path=None):
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise ValueError("SQLite >= 3.37 is required for PRAGMA table_list; update the Python SQLite runtime")
    path = Path(sqlite_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"SQLite file does not exist: {path}")
    metadata = load_json(metadata_path) if metadata_path else {}
    if not isinstance(metadata, dict):
        raise ValueError("Metadata must be a JSON object")
    descriptions = metadata.get("tables", {})
    if not isinstance(descriptions, dict):
        raise ValueError("Metadata tables must be an object")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    with connection:
        definitions = connection.execute(
            "SELECT name, sql FROM sqlite_schema WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        flags = {row["name"]: dict(row) for row in connection.execute("PRAGMA table_list")}
        tables = []
        for definition in definitions:
            name = definition["name"]
            if flags[name]["type"] != "table":
                raise ValueError(f"Virtual/shadow tables are outside this SQLite reader's scope: {name}")
            table_metadata = descriptions.get(name, {})
            if not isinstance(table_metadata, dict):
                raise ValueError(f"Metadata for {name} must be an object")
            column_metadata = table_metadata.get("columns", {})
            if not isinstance(column_metadata, dict):
                raise ValueError(f"Metadata columns must be an object: {name}")
            indexes = []
            for index in connection.execute(f"PRAGMA index_list({quote_identifier(name)})"):
                parts = [dict(row) for row in connection.execute(
                    f"PRAGMA index_xinfo({quote_identifier(index['name'])})"
                ) if row["key"]]
                indexes.append({"name": index["name"], "unique": bool(index["unique"]),
                                "partial": bool(index["partial"]), "origin": index["origin"],
                                "columns": [part["name"] for part in parts]})
            raw_columns = [dict(row) for row in connection.execute(
                f"PRAGMA table_xinfo({quote_identifier(name)})"
            )]
            primary_key = [column["name"] for column in sorted(raw_columns, key=lambda c: c["pk"]) if column["pk"]]
            rowid_alias = len(primary_key) == 1 and not any(i["origin"] == "pk" for i in indexes)
            columns = []
            for column in raw_columns:
                info = column_metadata.get(column["name"], {})
                if not isinstance(info, dict):
                    raise ValueError(f"Invalid column metadata: {name}.{column['name']}")
                column_description = info.get("description", "")
                if column_description is None:
                    column_description = ""
                if not isinstance(column_description, str):
                    raise ValueError(f"Column description must be a string: {name}.{column['name']}")
                implicit_not_null = column["pk"] and (
                    flags[name]["wr"] or flags[name]["strict"] or
                    (rowid_alias and column["type"].upper() == "INTEGER")
                )
                columns.append({"name": column["name"], "data_type": column["type"],
                                "nullable": not bool(column["notnull"] or implicit_not_null),
                                "primary_key_position": column["pk"], "default": column["dflt_value"],
                                "generated": column["hidden"] in (2, 3),
                                "description": column_description})
            unknown = set(column_metadata) - {column["name"] for column in columns}
            if unknown:
                raise ValueError(f"Metadata references unknown columns in {name}: {sorted(unknown)}")
            grouped = defaultdict(list)
            for row in connection.execute(f"PRAGMA foreign_key_list({quote_identifier(name)})"):
                grouped[row["id"]].append(dict(row))
            foreign_keys = []
            for parts in grouped.values():
                parts.sort(key=lambda part: part["seq"])
                foreign_keys.append({"source_table": name, "source_columns": [p["from"] for p in parts],
                                     "target_table": parts[0]["table"], "target_columns": [p["to"] for p in parts],
                                     "on_update": parts[0]["on_update"], "on_delete": parts[0]["on_delete"],
                                     "match": parts[0]["match"]})
            description = table_metadata.get("description", "")
            if description is None:
                description = ""
            if not isinstance(description, str):
                raise ValueError(f"Table description must be a string: {name}")
            tables.append({"name": name, "description": description, "ddl": definition["sql"],
                           "columns": columns, "primary_key": primary_key, "indexes": indexes,
                           "foreign_keys": foreign_keys})
        by_name = {table["name"]: table for table in tables}
        canonical_tables = {name.lower(): name for name in by_name}
        if set(descriptions) - set(by_name):
            raise ValueError(f"Metadata references unknown tables: {sorted(set(descriptions) - set(by_name))}")
        for table in tables:
            for fk in table["foreign_keys"]:
                fk["target_table"] = canonical_tables.get(fk["target_table"].lower(), fk["target_table"])
                if fk["target_table"] not in by_name:
                    raise ValueError(f"FK target table missing: {fk['target_table']}")
                source_names = {column["name"].lower(): column["name"] for column in table["columns"]}
                target_names = {column["name"].lower(): column["name"] for column in by_name[fk["target_table"]]["columns"]}
                fk["source_columns"] = [source_names.get(name.lower(), name) for name in fk["source_columns"]]
                if all(column is None for column in fk["target_columns"]):
                    fk["target_columns"] = by_name[fk["target_table"]]["primary_key"][:]
                else:
                    fk["target_columns"] = [target_names.get(name.lower(), name) if name is not None else None
                                            for name in fk["target_columns"]]
                if len(fk["source_columns"]) != len(fk["target_columns"]):
                    raise ValueError(f"FK column count mismatch: {fk}")
                if not set(fk["target_columns"]) <= {c["name"] for c in by_name[fk["target_table"]]["columns"]}:
                    raise ValueError(f"FK target columns missing: {fk}")
                fk["relation_id"] = "fk:" + fingerprint(fk)[:24]
            table["foreign_keys"].sort(key=lambda fk: fk["relation_id"])
        violations = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
        if violations:
            raise ValueError(f"Database has {len(violations)} FK violations")
    connection.close()
    payload = {"database_id": database_id, "tables": tables}
    return {**payload, "schema_fingerprint": fingerprint(payload),
            "source": {"sqlite_path": str(path), "sqlite_sha256": file_sha256(path),
                       "metadata_path": str(metadata_path) if metadata_path else None},
            "counts": {"tables": len(tables), "columns": sum(len(t["columns"]) for t in tables),
                       "foreign_keys": sum(len(t["foreign_keys"]) for t in tables)}}


def extract_schema(config):
    inventory = read_schema(config["sqlite_path"], config["database_id"], config["metadata_path"])
    save_json(config["output_dir"] / "schema_inventory.json", inventory)
    return inventory
