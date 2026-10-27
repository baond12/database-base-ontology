"""Các hàm pipeline từ ontology_pipeline.ipynb, giữ nguyên quy tắc kiểm định.

Bản web cấu hình thư mục riêng cho mỗi tiến trình worker qua configure().
Không import/chạy notebook và không ghi vào bộ kết quả mẫu.
"""
from pathlib import Path
from contextlib import closing
from collections import Counter, defaultdict
import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import requests
from jsonschema import Draft202012Validator


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"
    key = os.environ.get("DC5_API_KEY", "")
    secret_path = DATA / "secrets.local.json"
    if not key and secret_path.is_file():
        key = read_json(secret_path).get("api_key", "")
    if key:
        text = text.replace(key, "[REDACTED]")
    path.write_text(text, encoding="utf-8")

def fingerprint(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()

def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(stream.read()).hexdigest()

def require(condition, message):
    if not condition:
        raise ValueError(message)

def unique(values):
    return len(values) == len(set(values))

def copy_file(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)

def schema_errors(contract, value):
    return [f"{list(e.path)}: {e.message}" for e in Draft202012Validator(contract).iter_errors(value)]

def quote_name(name):
    return '"' + name.replace('"', '""') + '"'

def read_indexes(connection, table_name):
    indexes = []
    for index in connection.execute(f"PRAGMA index_list({quote_name(table_name)})"):
        parts = connection.execute(f"PRAGMA index_xinfo({quote_name(index['name'])})").fetchall()
        definition = connection.execute("SELECT sql FROM sqlite_schema WHERE name=?", (index["name"],)).fetchone()
        indexes.append({
            "name": index["name"], "unique": bool(index["unique"]),
            "partial": bool(index["partial"]), "origin": index["origin"],
            "columns": [part["name"] for part in parts if part["key"]],
            "ddl": definition["sql"] if definition else None,
        })
    return sorted(indexes, key=lambda item: item["name"])

def read_columns(connection, table_name, flags, indexes, metadata):
    raw = connection.execute(f"PRAGMA table_xinfo({quote_name(table_name)})").fetchall()
    pk = [c["name"] for c in sorted(raw, key=lambda c: c["pk"]) if c["pk"]]
    rowid_alias = len(pk) == 1 and not any(i["origin"] == "pk" for i in indexes)
    require(not set(metadata) - {c["name"] for c in raw}, f"Metadata có cột không tồn tại: {table_name}")
    columns = []
    for column in raw:
        description = metadata.get(column["name"], {}).get("description", "")
        description = "" if description is None else description
        require(isinstance(description, str), f"Description phải là chuỗi: {table_name}.{column['name']}")
        implicit_not_null = column["pk"] and (
            flags["wr"] or flags["strict"] or (rowid_alias and column["type"].upper() == "INTEGER"))
        columns.append({
            "name": column["name"], "data_type": column["type"],
            "nullable": not bool(column["notnull"] or implicit_not_null),
            "primary_key_position": column["pk"], "default": column["dflt_value"],
            "generated": column["hidden"] in (2, 3), "description": description,
        })
    return columns, pk

def read_foreign_keys(connection, table_name):
    groups = defaultdict(list)
    for row in connection.execute(f"PRAGMA foreign_key_list({quote_name(table_name)})"):
        groups[row["id"]].append(dict(row))
    foreign_keys = []
    for parts in groups.values():
        parts.sort(key=lambda row: row["seq"])
        foreign_keys.append({
            "source_table": table_name, "source_columns": [p["from"] for p in parts],
            "target_table": parts[0]["table"], "target_columns": [p["to"] for p in parts],
            "on_update": parts[0]["on_update"], "on_delete": parts[0]["on_delete"], "match": parts[0]["match"],
        })
    return foreign_keys

def unique_keys(table):
    keys = [table["primary_key"]] if table["primary_key"] else []
    for index in table["indexes"]:
        if index["unique"] and not index["partial"] and None not in index["columns"]:
            keys.append(index["columns"])
    return keys

def has_unique_key(table, columns):
    return any(set(key) <= set(columns) for key in unique_keys(table))

def normalize_foreign_keys(tables):
    by_name = {table["name"]: table for table in tables}
    canonical = {name.lower(): name for name in by_name}
    for table in tables:
        source_names = {c["name"].lower(): c["name"] for c in table["columns"]}
        seen = Counter()
        for fk in table["foreign_keys"]:
            fk["target_table"] = canonical.get(fk["target_table"].lower(), fk["target_table"])
            require(fk["target_table"] in by_name, f"Thiếu bảng đích FK: {fk}")
            target = by_name[fk["target_table"]]
            target_names = {c["name"].lower(): c["name"] for c in target["columns"]}
            fk["source_columns"] = [source_names[name.lower()] for name in fk["source_columns"]]
            if all(name is None for name in fk["target_columns"]):
                fk["target_columns"] = target["primary_key"][:]
            else:
                require(all(name is not None for name in fk["target_columns"]), f"FK đích không đầy đủ: {fk}")
                fk["target_columns"] = [target_names[name.lower()] for name in fk["target_columns"]]
            require(len(fk["source_columns"]) == len(fk["target_columns"]), f"Số cột FK không khớp: {fk}")
            require(any(set(key) == set(fk["target_columns"]) for key in unique_keys(target)), f"Đích FK thiếu PK/UNIQUE: {fk}")
            signature = fingerprint(fk)[:24]
            seen[signature] += 1
            fk["relation_id"] = f"fk:{signature}:{seen[signature]}"
        table["foreign_keys"].sort(key=lambda fk: fk["relation_id"])
    return tables

def read_schema(connection, metadata):
    require(sqlite3.sqlite_version_info >= (3, 37), "Cần SQLite >= 3.37")
    require(isinstance(metadata, dict), "Metadata phải là JSON object")
    descriptions = metadata.get("tables", {})
    require(isinstance(descriptions, dict), "metadata.tables phải là object")
    connection.row_factory = sqlite3.Row
    definitions = connection.execute(
        "SELECT name,sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    flags = {row["name"]: dict(row) for row in connection.execute("PRAGMA table_list")}
    require(not set(descriptions) - {row["name"] for row in definitions}, "Metadata có bảng không tồn tại")
    tables = []
    for definition in definitions:
        name = definition["name"]
        require(flags[name]["type"] == "table", f"Chưa hỗ trợ virtual/shadow table: {name}")
        info = descriptions.get(name, {})
        require(isinstance(info, dict) and isinstance(info.get("columns", {}), dict), f"Metadata sai cấu trúc: {name}")
        require(all(isinstance(item, dict) for item in info.get("columns", {}).values()), f"Metadata cột sai: {name}")
        description = info.get("description", "")
        description = "" if description is None else description
        require(isinstance(description, str), f"Description bảng phải là chuỗi: {name}")
        indexes = read_indexes(connection, name)
        columns, primary_key = read_columns(connection, name, flags[name], indexes, info.get("columns", {}))
        tables.append({"name": name, "description": description, "ddl": definition["sql"],
                       "columns": columns, "primary_key": primary_key, "indexes": indexes,
                       "foreign_keys": read_foreign_keys(connection, name)})
    violations = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
    require(not violations, f"Database có {len(violations)} vi phạm FK")
    return normalize_foreign_keys(tables)

def numeric_column(column):
    declared = column["data_type"].upper()
    return any(word in declared for word in ("INT", "REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL"))

def parameter_type(column):
    declared = column["data_type"].upper()
    if "BOOL" in declared:
        return "boolean"
    if "INT" in declared:
        return "integer"
    if numeric_column(column):
        return "number"
    return "string"

def method_errors(method, table):
    errors = []
    columns = {c["name"]: c for c in table["columns"]}
    params = method["input_parameters"]
    output = method["output"]
    groups = method["group_by"]
    metrics = output["aggregations"]
    used = set(output["source_columns"]) | set(groups)
    used.update(p["source_column"] for p in params)
    used.update(m["source_column"] for m in metrics if m["source_column"] is not None)
    if used != set(method["source_columns"]):
        errors.append("source_columns phải bằng hợp chính xác các cột input/output/group/aggregate")
    if not used <= set(columns):
        return errors + [f"Tham chiếu cột không tồn tại hoặc ngoài bảng: {sorted(used - set(columns))}"]
    if not unique([p["name"] for p in params]):
        errors.append("Tên input parameter bị trùng")
    for param in params:
        column = columns[param["source_column"]]
        if param["type"] != parameter_type(column):
            errors.append(f"Sai type của parameter {param['name']}")
        if any(word in column["data_type"].upper() for word in ("JSON", "BLOB")):
            errors.append("Chưa hỗ trợ filter trực tiếp JSON/BLOB")
        if param["operator"] == "contains" and param["type"] != "string":
            errors.append("contains chỉ dùng với string")
    if not unique([m["name"] for m in metrics] + groups):
        errors.append("Tên aggregation trùng nhau hoặc trùng cột group_by")
    for metric in metrics:
        name = metric["source_column"]
        if name is None and metric["function"] != "count":
            errors.append("Chỉ count(*) được dùng source_column=null")
        if name and metric["function"] in ("sum", "avg") and not numeric_column(columns[name]):
            errors.append("sum/avg chỉ dùng cột số")
    operation = method["operation"]
    if operation in ("list", "lookup"):
        if groups or metrics or not output["source_columns"]:
            errors.append("list/lookup phải trả cột thật, không group/aggregate")
        expected_kind = "rows" if operation == "list" else "object"
        if output["kind"] != expected_kind:
            errors.append(f"{operation} phải trả {expected_kind}")
    if operation == "lookup":
        equal_required = {p["source_column"] for p in params if p["required"] and p["operator"] == "eq"}
        if not has_unique_key(table, equal_required):
            errors.append("lookup phải equality-filter đầy đủ PK/UNIQUE bằng parameter required")
    if operation == "count":
        if (groups or output["kind"] != "scalar" or output["source_columns"] or len(metrics) != 1
                or metrics[0]["function"] != "count" or metrics[0]["source_column"] is not None):
            errors.append("count phải là COUNT(*) scalar, không group, không trả cột thô")
    if operation == "aggregate":
        if not metrics:
            errors.append("aggregate phải khai báo phép tổng hợp")
        if groups:
            if output["kind"] != "rows" or set(output["source_columns"]) != set(groups):
                errors.append("aggregate có nhóm phải trả rows và đủ đúng các cột nhóm")
        elif output["kind"] != "scalar" or output["source_columns"] or len(metrics) != 1:
            errors.append("aggregate không nhóm phải trả đúng một scalar metric")
    return errors

def base_proposal_errors(proposal, table):
    errors = []  # Contract đã được kiểm tra trước khi gọi hàm này.
    properties = proposal["properties"]
    names = [p["column_name"] for p in properties]
    if not unique(names) or set(names) != {c["name"] for c in table["columns"]}:
        errors.append("Properties phải bao phủ mỗi cột đúng một lần")
    self_fk_columns = {c for fk in table["foreign_keys"] if fk["target_table"] == table["name"]
                       for c in fk["source_columns"]}
    self_descriptions = [proposal["description"]] + [p["description"] for p in properties if p["column_name"] in self_fk_columns]
    if self_fk_columns and any(re.search(r"\bkhác\b", text.lower()) for text in self_descriptions):
        errors.append("Mô tả self-FK dùng 'trong cùng bảng', tránh 'khác' gây hiểu bắt buộc khác bản ghi")
    if not unique([p["name"] for p in properties]):
        errors.append("Tên Properties bị trùng")
    descriptions = [proposal["description"]] + [p["description"] for p in properties] + proposal["assumptions"]
    if "AUTOINCREMENT" not in table["ddl"].upper():
        if any(phrase in text.lower() for text in descriptions
               for phrase in ("tự động tăng", "tự tăng", "tăng theo thứ tự chèn")):
            errors.append("Không dùng khẳng định tự tăng khi DDL không khai báo AUTOINCREMENT; mô tả là khóa chính INTEGER")
    if not unique([m["name"] for m in proposal["methods"]]):
        errors.append("Tên Methods bị trùng")
    plans = []
    for method in proposal["methods"]:
        errors.extend(f"{method['name']}: {error}" for error in method_errors(method, table))
        plan = {key: method[key] for key in ("operation", "input_parameters", "group_by", "output")}
        # Parameter/metric display names do not distinguish the underlying query plan.
        plan["input_parameters"] = sorted(
            [{k: v for k, v in p.items() if k != "name"} for p in plan["input_parameters"]],
            key=lambda p: (p["source_column"], p["operator"], p["required"]))
        plan["output"] = copy.deepcopy(plan["output"])
        plan["output"]["source_columns"] = sorted(plan["output"]["source_columns"])
        plan["group_by"] = sorted(plan["group_by"])
        plan["output"]["aggregations"] = [
            {k: v for k, v in a.items() if k != "name"} for a in plan["output"]["aggregations"]]
        plan["output"]["aggregations"].sort(key=lambda a: (a["function"], a["source_column"] or ""))
        plans.append(fingerprint(plan))
    if not unique(plans):
        errors.append("Methods có kế hoạch trùng nhau, chỉ khác tên")
    return errors

def generation_contract(value):
    # DC5/xgrammar không hỗ trợ uniqueItems; giữ kiểm tra này ở validator local.
    if isinstance(value, dict):
        return {key: generation_contract(item) for key, item in value.items() if key != "uniqueItems"}
    if isinstance(value, list):
        return [generation_contract(item) for item in value]
    return value

def parse_response(content):
    content = content.strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(content)

def api_key():
    key = os.environ.get("DC5_API_KEY", "")
    if not key and (DATA / "secrets.local.json").is_file():
        key = read_json(DATA / "secrets.local.json").get("api_key", "")
    require(bool(key) and key != "SET_YOUR_DC5_API_KEY_HERE", "Cần DC5_API_KEY hoặc secrets.local.json")
    return key

def ask_llm(step, name, context, check):
    prompt = (step / "input/prompt.txt").read_text(encoding="utf-8")
    feedback_path = step / "input/review_feedback.json"
    if feedback_path.is_file():
        feedback = read_json(feedback_path).get(name, "")
        if feedback:
            prompt += "\nLƯU Ý REVIEW ĐÃ ĐỐI CHIẾU SCHEMA:\n" + feedback
    contract = read_json(step / "input/response_contract.json")
    if step == S4:
        expected_ids = [fk["relation_id"] for fk in context["owned_foreign_keys"]]
        relation_schema = contract["properties"]["relations"]
        relation_schema.update(minItems=len(expected_ids), maxItems=len(expected_ids))
        relation_schema["items"]["properties"]["relation_id"]["enum"] = expected_ids
    generation = {key: settings[key] for key in ("base_url", "model", "enable_thinking", "max_tokens")}
    generation["structured_output"] = "json_schema"
    request_hash = fingerprint({"context": context, "prompt": prompt, "contract": contract, "generation": generation})
    trace_path = step / "output/traces" / f"{name}.json"
    if trace_path.is_file() and not FORCE:
        cached = read_json(trace_path)
        if cached.get("request_fingerprint") == request_hash and cached.get("status") == "COMPLETE":
            result = cached["proposal"]
            errors = schema_errors(contract, result)
            if not errors:
                errors = check(result)
            require(not errors, " | ".join(errors))
            print("CACHED", step.name, name, flush=True)
            return result
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(
        {"response_contract": contract, "context": context}, ensure_ascii=False)}]
    trace = {"request_fingerprint": request_hash, "generation": generation, "attempts": [], "status": "RUNNING"}
    print("CALL", step.name, name, flush=True)
    with requests.Session() as session:
        session.trust_env = False
        proxy = settings.get("proxy_url", "")
        proxies = {"http": proxy, "https": proxy} if proxy else {}
        for attempt in (1, 2):
            payload = {"model": settings["model"], "temperature": 0,
                       "max_tokens": settings["max_tokens"], "messages": messages,
                       "chat_template_kwargs": {"enable_thinking": settings["enable_thinking"]},
                       "response_format": {"type": "json_schema", "json_schema": {
                           "name": "ontology_response", "strict": True, "schema": generation_contract(contract)}}}
            trace["request"] = payload
            write_json(trace_path, trace)
            started = time.monotonic()
            response = session.post(settings["base_url"].rstrip("/") + "/chat/completions",
                                    headers={"Authorization": "Bearer " + api_key()}, json=payload,
                                    proxies=proxies, timeout=(15, settings["timeout_seconds"]))
            record = {"attempt": attempt, "http_status": response.status_code,
                      "elapsed_seconds": round(time.monotonic() - started, 3)}
            trace["attempts"].append(record)
            if not response.ok:
                record["error_body"] = response.text[:2000]
                trace["status"] = "HTTP_ERROR"
                write_json(trace_path, trace)
                response.raise_for_status()
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
            record.update({"content": message.get("content") or "",
                           "reasoning_content": message.get("reasoning_content") or "",
                           "finish_reason": choice.get("finish_reason"), "usage": body.get("usage", {}),
                           "model": body.get("model"), "response_id": body.get("id")})
            write_json(trace_path, trace)
            try:
                require(choice.get("finish_reason") != "length", "Response bị cắt: tăng max_tokens")
                result = parse_response(record["content"])
                errors = schema_errors(contract, result)
                if not errors:
                    errors = check(result)
                require(not errors, " | ".join(errors))
            except ValueError as error:
                record["validation_error"] = str(error)
                trace["status"] = "INVALID_RESPONSE"
                write_json(trace_path, trace)
                if attempt == 2:
                    raise
                messages = messages + [{"role": "assistant", "content": record["content"]},
                                       {"role": "user", "content": "Sửa JSON theo input/contract. Lỗi: " + str(error)}]
                continue
            trace.update(status="COMPLETE", proposal=result)
            write_json(trace_path, trace)
            return result

def assemble_base(table, proposal, database_id):
    name = table["name"]
    annotations = {p["column_name"]: p for p in proposal["properties"]}
    fk_columns = {c for fk in table["foreign_keys"] for c in fk["source_columns"]}
    properties = []
    for column in table["columns"]:
        annotation = annotations[column["name"]]
        properties.append({
            "property_id": f"property:{name}.{column['name']}", "name": annotation["name"],
            "description": annotation["description"], "confidence": annotation["confidence"],
            "source_table": name, "source_column": column["name"],
            **{key: column[key] for key in ("data_type", "nullable", "primary_key_position", "default", "generated")},
            "is_foreign_key": column["name"] in fk_columns, "metadata_description": column["description"],
        })
    methods = []
    for method in proposal["methods"]:
        methods.append({**method, "method_id": f"method:{name}.{method['name']}",
                        "owner_class_id": f"class:{name}", "source_table": name,
                        "scope": "BASE_CLASS", "status": "PROPOSED_READ_ONLY"})
    return {
        "database_id": database_id,
        "class": {"class_id": f"class:{name}", "name": proposal["class_name"], "source_table": name,
                  "description": proposal["description"], "metadata_description": table["description"],
                  "confidence": proposal["confidence"], "semantic_status": "PROPOSED"},
        "properties": properties, "methods": methods, "assumptions": proposal["assumptions"],
        "input_fingerprint": fingerprint({"database_id": database_id, "table": table}),
    }

def build_bases():
    inventory = read_json(S2 / "input/schema_inventory.json")
    bases = {}
    for table in inventory["tables"]:
        name = table["name"]
        context = {"database_id": inventory["database_id"], "table": table}
        proposal = ask_llm(S2, name, context, lambda result: base_proposal_errors(result, table))
        base = assemble_base(table, proposal, inventory["database_id"])
        write_json(S2 / "output/proposals" / f"{name}.json", proposal)
        write_json(S2 / "output/base_ontologies" / f"{name}.json", base)
        bases[name] = base
    require(unique([b["class"]["name"] for b in bases.values()]), "Tên Class bị trùng giữa các bảng")
    return {"classes": len(bases), "properties": sum(len(b["properties"]) for b in bases.values()),
            "methods": sum(len(b["methods"]) for b in bases.values())}

def ontology_view(base):
    return {"class": base["class"], "properties": base["properties"],
            "input_fingerprint": base["input_fingerprint"]}

def make_context(table_name, inventory, bases):
    foreign_keys = [fk for t in inventory["tables"] for fk in t["foreign_keys"]
                    if table_name in (fk["source_table"], fk["target_table"])]
    neighbors = sorted({name for fk in foreign_keys
                        for name in (fk["source_table"], fk["target_table"])} - {table_name})
    payload = {
        "database_id": inventory["database_id"], "class_id": f"class:{table_name}",
        "current": ontology_view(bases[table_name]),
        "references": [{"class_id": f"class:{name}", "ontology": ontology_view(bases[name])} for name in neighbors],
        "foreign_keys": foreign_keys,
    }
    return {**payload, "context_fingerprint": fingerprint(payload)}

def build_contexts():
    inventory = read_json(S3 / "input/schema_inventory.json")
    bases = {t["name"]: read_json(S3 / "input/base_ontologies" / f"{t['name']}.json")
             for t in inventory["tables"]}
    for table in inventory["tables"]:
        expected_hash = fingerprint({"database_id": inventory["database_id"], "table": table})
        require(bases[table["name"]]["input_fingerprint"] == expected_hash,
                f"Base ontology cũ: {table['name']}. Chạy lại bước 2.")
    rows = []
    for table in inventory["tables"]:
        context = make_context(table["name"], inventory, bases)
        write_json(S3 / "output/reference_contexts" / f"{table['name']}.json", context)
        rows.append({"table": table["name"], "references": len(context["references"]),
                     "foreign_keys": len(context["foreign_keys"])})
    return rows

def physical_relation(fk, tables):
    source = tables[fk["source_table"]]
    nullable = {c["name"]: c["nullable"] for c in source["columns"]}
    return {
        "relation_id": fk["relation_id"],
        "source_class_id": f"class:{fk['source_table']}", "target_class_id": f"class:{fk['target_table']}",
        "source_property_ids": [f"property:{fk['source_table']}.{c}" for c in fk["source_columns"]],
        "target_property_ids": [f"property:{fk['target_table']}.{c}" for c in fk["target_columns"]],
        "cardinality": {
            "source_to_target": {"min": 0 if any(nullable[c] for c in fk["source_columns"]) else 1, "max": 1},
            "target_to_source": {"min": 0, "max": 1 if has_unique_key(source, fk["source_columns"]) else "many"},
        },
        "source_fk": fk, "physical_status": "VERIFIED_FROM_SCHEMA", "semantic_status": "PROPOSED",
    }

def relation_proposal_errors(proposal, foreign_keys):
    errors = []  # Contract đã được kiểm tra trước khi gọi hàm này.
    ids = [r["relation_id"] for r in proposal["relations"]]
    if not unique(ids) or set(ids) != {fk["relation_id"] for fk in foreign_keys}:
        errors.append(f"Mỗi FK đúng một Relation. Expected IDs={sorted(fk['relation_id'] for fk in foreign_keys)}; actual={ids}")
    return errors

def build_relations():
    inventory = read_json(S4 / "input/schema_inventory.json")
    tables = {t["name"]: t for t in inventory["tables"]}
    relations = []
    for name, table in tables.items():
        owned = table["foreign_keys"]
        if not owned:
            continue
        context = read_json(S4 / "input/reference_contexts" / f"{name}.json")
        payload = {key: value for key, value in context.items() if key != "context_fingerprint"}
        require(context["context_fingerprint"] == fingerprint(payload), f"Context bị sửa: {name}")
        expected_fks = [fk for t in tables.values() for fk in t["foreign_keys"]
                        if name in (fk["source_table"], fk["target_table"])]
        require(context["foreign_keys"] == expected_fks, f"Context có FK cũ: {name}")
        views = [context["current"]] + [r["ontology"] for r in context["references"]]
        for view in views:
            source_name = view["class"]["source_table"]
            expected_hash = fingerprint({"database_id": inventory["database_id"], "table": tables[source_name]})
            require(view["input_fingerprint"] == expected_hash, f"Context có ontology cũ: {source_name}")
        request = {"reference_context": context, "owned_foreign_keys": owned,
                   "physical_relations": [physical_relation(fk, tables) for fk in owned]}
        proposal = ask_llm(S4, name, request, lambda result: relation_proposal_errors(result, owned))
        write_json(S4 / "output/annotations" / f"{name}.json", proposal)
        annotations = {row["relation_id"]: row for row in proposal["relations"]}
        for fk in owned:
            relations.append({**annotations[fk["relation_id"]], **physical_relation(fk, tables)})
    require(unique([r["relation_id"] for r in relations]), "Relation IDs bị trùng")
    result = {"database_id": inventory["database_id"], "schema_fingerprint": inventory["schema_fingerprint"],
              "relations": sorted(relations, key=lambda r: r["relation_id"])}
    write_json(S4 / "output/relations.json", result)
    return {"relations": len(relations)}

def prepare_validation_inputs():
    copy_file(S1 / "output/schema_inventory.json", S5 / "input/schema_inventory.json")
    copy_file(S4 / "output/relations.json", S5 / "input/relations.json")
    for table in read_json(S5 / "input/schema_inventory.json")["tables"]:
        name = table["name"]
        copy_file(S2 / "output/base_ontologies" / f"{name}.json", S5 / "input/base_ontologies" / f"{name}.json")
        copy_file(S2 / "output/proposals" / f"{name}.json", S5 / "input/proposals" / f"{name}.json")
        copy_file(S2 / "output/traces" / f"{name}.json", S5 / "input/generation_traces/base" / f"{name}.json")
        if table["foreign_keys"]:
            copy_file(S4 / "output/traces" / f"{name}.json", S5 / "input/generation_traces/relations" / f"{name}.json")
        copy_file(S3 / "output/reference_contexts" / f"{name}.json", S5 / "input/reference_contexts" / f"{name}.json")
    copy_file(S2 / "input/response_contract.json", S5 / "input/base_response_contract.json")
    copy_file(S4 / "input/response_contract.json", S5 / "input/relation_response_contract.json")

def structural_report(inventory, bases, proposals, contexts, relations):
    findings = []
    tables = {t["name"]: t for t in inventory["tables"]}
    payload = {"database_id": inventory["database_id"], "tables": inventory["tables"]}
    if inventory["schema_fingerprint"] != fingerprint(payload):
        findings.append("Inventory fingerprint không khớp nội dung")
    base_contract = read_json(S5 / "input/base_response_contract.json")
    for name, table in tables.items():
        contract_findings = schema_errors(base_contract, proposals[name])
        if contract_findings:
            findings.extend(f"{name}: {e}" for e in contract_findings)
            continue
        findings.extend(f"{name}: {e}" for e in base_proposal_errors(proposals[name], table))
        expected = assemble_base(table, proposals[name], inventory["database_id"])
        if bases[name] != expected:
            findings.append(f"Base ontology bị sửa/stale: {name}")
        if contexts[name] != make_context(name, inventory, bases):
            findings.append(f"Context bị sửa/stale: {name}")
    expected_relations = {fk["relation_id"]: physical_relation(fk, tables)
                          for t in tables.values() for fk in t["foreign_keys"]}
    ids = [r["relation_id"] for r in relations]
    if not unique(ids) or set(ids) != set(expected_relations):
        findings.append("Relation coverage/IDs không khớp các FK")
    annotations = [{key: r.get(key) for key in ("relation_id", "name", "description", "confidence", "assumptions")}
                   for r in relations]
    findings.extend(schema_errors(read_json(S5 / "input/relation_response_contract.json"), {"relations": annotations}))
    for relation in relations:
        expected = expected_relations.get(relation["relation_id"], {})
        if any(relation.get(key) != value for key, value in expected.items()):
            findings.append(f"Relation endpoints/cardinality bị sửa: {relation['relation_id']}")
    if not unique([b["class"]["name"] for b in bases.values()]):
        findings.append("Tên Class bị trùng")
    return {"structural_status": "PASS" if not findings else "FAIL", "findings": findings,
            "counts": {"classes": len(bases), "properties": sum(len(b["properties"]) for b in bases.values()),
                       "methods": sum(len(b["methods"]) for b in bases.values()), "relations": len(relations)},
            "runtime_readiness": "NOT_IMPLEMENTED", "human_review_status": "PENDING",
            "business_semantics": "PROPOSED_NOT_PROVEN"}

def review_errors(result, table_name):
    errors = []  # Contract đã được kiểm tra trước khi gọi hàm này.
    if result["table_name"] != table_name:
        errors.append("Reviewer trả sai table_name")
    return errors

def validate_and_export():
    inventory = read_json(S5 / "input/schema_inventory.json")
    names = [t["name"] for t in inventory["tables"]]
    bases = {name: read_json(S5 / "input/base_ontologies" / f"{name}.json") for name in names}
    proposals = {name: read_json(S5 / "input/proposals" / f"{name}.json") for name in names}
    contexts = {name: read_json(S5 / "input/reference_contexts" / f"{name}.json") for name in names}
    relation_file = read_json(S5 / "input/relations.json")
    relations = relation_file["relations"]
    report = structural_report(inventory, bases, proposals, contexts, relations)
    if relation_file["schema_fingerprint"] != inventory["schema_fingerprint"]:
        report["findings"].append("Relations sinh từ schema cũ")
        report["structural_status"] = "FAIL"
    write_json(S5 / "output/validation_report.json", report)
    require(report["structural_status"] == "PASS", str(report["findings"]))
    reviews = []
    if RUN_AI_REVIEW:
        for table in inventory["tables"]:
            name = table["name"]
            related = [r for r in relations if f"class:{name}" in (r["source_class_id"], r["target_class_id"])]
            request = {"table": table, "base": bases[name], "relations": related}
            review = ask_llm(S5, name, request, lambda result: review_errors(result, name))
            write_json(S5 / "output/semantic_reviews" / f"{name}.json", review)
            reviews.append(review)
    review_findings = [{"table": r["table_name"], **f} for r in reviews for f in r["findings"]]
    report["ai_review_status"] = "COMPLETED" if RUN_AI_REVIEW else "NOT_RUN"
    report["ai_review_findings"] = review_findings
    report["semantic_status"] = "REVIEW_REQUIRED" if review_findings else "AI_REVIEWED_WITH_LIMITATIONS" if RUN_AI_REVIEW else "NOT_REVIEWED"
    write_json(S5 / "output/validation_report.json", report)
    ontology = {"database_id": inventory["database_id"], "schema_fingerprint": inventory["schema_fingerprint"],
                "classes": [b["class"] for b in bases.values()],
                "properties": [p for b in bases.values() for p in b["properties"]],
                "methods": [m for b in bases.values() for m in b["methods"]], "relations": relations,
                "semantic_status": report["semantic_status"], "human_review_status": "PENDING",
                "runtime_readiness": "NOT_IMPLEMENTED"}
    write_json(S5 / "output/ontology.json", ontology)
    require(read_json(S5 / "output/ontology.json") == ontology, "Ontology export không khớp")
    for name, base in bases.items():
        full = {**base, "relations": [r for r in relations if f"class:{name}" in (r["source_class_id"], r["target_class_id"])]}
        path = S5 / "output/class_ontologies" / f"{name}.json"
        write_json(path, full)
        require(read_json(path) == full, f"Class export không khớp: {name}")
    return report
