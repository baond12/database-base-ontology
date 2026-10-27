"""Web local: API Python + frontend không cần npm/build.

Chạy: ../.venv/bin/python server.py --port 8787
"""
import argparse
import base64
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote
from wsgiref.simple_server import make_server

import pipeline_core as core
from storage import APP, RUNTIME, SAMPLE, STEPS, read_json, redact, server_key, update_state, write_json

PROCESSES = {}
MAX_UPLOAD = 20 * 1024 * 1024


def folder_for(run_id):
    if run_id == "sample":
        return SAMPLE
    if not re.fullmatch(r"[0-9a-f]{16}", run_id):
        raise ValueError("Mã lần chạy không hợp lệ.")
    folder = RUNTIME / "runs" / run_id
    if not (folder / "state.json").is_file():
        raise FileNotFoundError("Không tìm thấy lần chạy.")
    return folder


def snapshot(run_id):
    folder = folder_for(run_id)
    state = read_json(folder / "state.json", {})
    if run_id == "sample":
        state = {"run_id": "sample", "status": "COMPLETE", "step": 5, "sample": True,
                 "name": "Omnichannel Retail", "message": "Kết quả mẫu từ lần chạy đã lưu.",
                 "completed": 20, "total": 20, "current_table": ""}
    process = PROCESSES.get(run_id)
    if process and process.poll() is not None and state.get("status") in {"RUNNING", "EXTRACTING"}:
        state = update_state(folder, status="ERROR", error="Worker đã dừng. Bạn có thể thử lại.")
    inventory = read_json(folder / "01_schema/output/schema_inventory.json", {})
    tables, bases = [], []
    for table in inventory.get("tables", []):
        name = table["name"]
        base = read_json(folder / f"02_base_ontology/output/base_ontologies/{name}.json", {})
        if base:
            bases.append(base)
        tables.append({"name": name, "class_name": base.get("class", {}).get("name", ""),
                       "columns": len(table["columns"]), "foreign_keys": len(table["foreign_keys"]),
                       "base_complete": bool(base)})
    relations = read_json(folder / "04_relations/output/relations.json", {}).get("relations", [])
    report = read_json(folder / "05_validation/output/validation_report.json", {})
    counts = {"tables": len(tables), "classes": len(bases),
              "properties": sum(len(b["properties"]) for b in bases),
              "methods": sum(len(b["methods"]) for b in bases), "relations": len(relations),
              "columns": sum(t["columns"] for t in tables),
              "foreign_keys": sum(t["foreign_keys"] for t in tables),
              "findings": len(report.get("findings", [])) + len(report.get("ai_review_findings", []))}
    settings = read_json(folder / "settings.json", {})
    return {**state, "pid": None, "tables": tables, "counts": counts, "report": report,
            "model": settings.get("model"), "thinking": settings.get("enable_thinking", True),
            "database_id": inventory.get("database_id", ""),
            "export_ready": (folder / "05_validation/output/ontology.json").is_file()}


def table_detail(run_id, name):
    folder = folder_for(run_id)
    inventory = read_json(folder / "01_schema/output/schema_inventory.json", {})
    table = next((t for t in inventory.get("tables", []) if t["name"] == name), None)
    if table is None:
        raise FileNotFoundError("Không tìm thấy bảng.")
    base = read_json(folder / f"05_validation/output/class_ontologies/{name}.json")
    if base is None:
        base = read_json(folder / f"02_base_ontology/output/base_ontologies/{name}.json", {})
    all_relations = read_json(folder / "04_relations/output/relations.json", {}).get("relations", [])
    related = [r for r in all_relations if f"class:{name}" in (r["source_class_id"], r["target_class_id"])]
    context = read_json(folder / f"03_reference_context/output/reference_contexts/{name}.json")
    review = read_json(folder / f"05_validation/output/semantic_reviews/{name}.json")
    return {"schema": table, "base": base, "relations": related, "context": context, "review": review}


def advanced(run_id, name, step):
    if step not in {2, 4, 5}:
        raise ValueError("Bước này không gọi LLM.")
    folder = folder_for(run_id)
    detail = table_detail(run_id, name)
    step_folder = folder / STEPS[step - 1]
    trace = read_json(step_folder / f"output/traces/{name}.json", {})
    if trace.get("request"):
        messages = trace["request"]["messages"]
        request = json.loads(messages[1]["content"])
        return {"prompt": messages[0]["content"], "contract": request["response_contract"],
                "context": request["context"], "sent": True}
    context = {"table": detail["schema"]}
    if step == 4:
        inventory = read_json(folder / "01_schema/output/schema_inventory.json")
        tables = {t["name"]: t for t in inventory["tables"]}
        owned = detail["schema"]["foreign_keys"]
        context = {"reference_context": detail["context"], "owned_foreign_keys": owned,
                   "physical_relations": [core.physical_relation(fk, tables) for fk in owned]}
    if step == 5:
        context = {"table": detail["schema"], "base": detail["base"], "relations": detail["relations"]}
    return {"prompt": (step_folder / "input/prompt.txt").read_text(),
            "contract": read_json(step_folder / "input/response_contract.json"), "context": context, "sent": False}


def launch(folder, mode):
    log = folder / "worker.log"
    with log.open("ab") as stream:
        process = subprocess.Popen([sys.executable, str(APP / "worker.py"), str(folder), mode],
                                   stdout=stream, stderr=stream, start_new_session=True)
    PROCESSES[folder.name] = process


def create_run(data):
    filename = data.get("filename", "")
    suffix = filename.lower().rsplit(".", 1)[-1]
    if suffix not in {"sql", "sqlite", "sqlite3", "db"}:
        raise ValueError("Chọn file .sql, .sqlite, .sqlite3 hoặc .db.")
    content = base64.b64decode(data.get("content_base64", ""), validate=True)
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError("File cần có nội dung và không quá 20 MB.")
    if suffix != "sql" and not content.startswith(b"SQLite format 3\x00"):
        raise ValueError("File không có định dạng SQLite hợp lệ.")
    if suffix == "sql":
        content.decode("utf-8-sig")
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Metadata JSON cần là một object.")
    run_id = uuid.uuid4().hex[:16]
    folder = RUNTIME / "runs" / run_id
    folder.mkdir(parents=True, mode=0o700)
    settings = read_json(SAMPLE / "settings.json")
    settings.update(database_id="database_" + run_id, enable_thinking=bool(data.get("thinking", True)))
    write_json(folder / "settings.json", settings)
    for step in STEPS:
        (folder / step / "input").mkdir(parents=True)
        (folder / step / "output").mkdir()
        for name in ["prompt.txt", "response_contract.json"]:
            source = SAMPLE / step / "input" / name
            if source.is_file():
                shutil.copy2(source, folder / step / "input" / name)
    source_name = "schema.sql" if suffix == "sql" else "database.local.sqlite3"
    (folder / "01_schema/input" / source_name).write_bytes(content)
    write_json(folder / "01_schema/input/metadata.json", metadata)
    update_state(folder, run_id=run_id, name=filename[:160], sample=False, status="EXTRACTING", step=1,
                 completed=0, total=1, current_table="", message="Đang trích xuất schema.",
                 created_at=datetime.now(timezone.utc).isoformat())
    launch(folder, "extract")
    return {"run_id": run_id}


def start_run(run_id):
    if run_id == "sample":
        raise ValueError("Đây là kết quả mẫu. Hãy tạo một lần chạy mới với schema của bạn.")
    folder = folder_for(run_id)
    state = read_json(folder / "state.json")
    if state["status"] not in {"READY", "ERROR"}:
        raise ValueError("Lần chạy đang xử lý hoặc đã hoàn thành.")
    if any(process.poll() is None for process in PROCESSES.values()):
        raise ValueError("Một tác vụ khác đang chạy. Vui lòng chờ tác vụ đó hoàn thành.")
    has_schema = (folder / "01_schema/output/schema_inventory.json").is_file()
    if has_schema and not server_key():
        raise ValueError("Server chưa có DC5 API key. Cấu hình DC5_API_KEY hoặc secrets.local.json trên server.")
    update_state(folder, status="RUNNING" if has_schema else "EXTRACTING", error="", step=2 if has_schema else 1,
                 message="Đang bắt đầu xử lý.")
    launch(folder, "run" if has_schema else "extract")
    return {"run_id": run_id}


def download(run_id, kind, table):
    folder = folder_for(run_id)
    output = folder / "05_validation/output"
    if kind == "class":
        table_detail(run_id, table)
        path = output / "class_ontologies" / f"{table}.json"
        filename = f"{table}.json"
    elif kind in {"ontology", "report"}:
        filename = "ontology.json" if kind == "ontology" else "validation_report.json"
        path = output / filename
    elif kind == "all":
        if not (output / "ontology.json").is_file():
            raise ValueError("Chưa có ontology hoàn chỉnh để tải.")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as bundle:
            for file in sorted(output.rglob("*.json")):
                if file.parts[-2] not in {"traces"} and file.name in {"ontology.json", "validation_report.json"} or file.parent.name in {"class_ontologies", "semantic_reviews"}:
                    bundle.write(file, str(file.relative_to(output)))
        return stream.getvalue(), "application/zip", f"ontology_{run_id}.zip"
    else:
        raise ValueError("Loại file không hợp lệ.")
    if not path.is_file():
        raise FileNotFoundError("Kết quả chưa sẵn sàng.")
    return path.read_bytes(), "application/json; charset=utf-8", filename


def response(start, data, status="200 OK", mime="application/json; charset=utf-8", headers=None):
    body = json.dumps(data, ensure_ascii=False).encode() if not isinstance(data, bytes) else data
    start(status, [("Content-Type", mime), ("Content-Length", str(len(body))),
                   ("X-Content-Type-Options", "nosniff"), ("Cache-Control", "no-store"), *(headers or [])])
    return [body]


def application(environ, start):
    path = unquote(environ.get("PATH_INFO", "/"))
    method = environ["REQUEST_METHOD"]
    query = parse_qs(environ.get("QUERY_STRING", ""))
    try:
        if method == "POST":
            origin = environ.get("HTTP_ORIGIN")
            if origin and origin != "http://" + environ.get("HTTP_HOST", ""):
                return response(start, {"error": "Origin không hợp lệ."}, "403 Forbidden")
            if environ.get("CONTENT_TYPE", "").split(";", 1)[0] != "application/json":
                raise ValueError("API yêu cầu application/json.")
            length = int(environ.get("CONTENT_LENGTH") or 0)
            if length <= 0 or length > MAX_UPLOAD * 1.5:
                raise ValueError("Request trống hoặc vượt giới hạn upload.")
            data = json.loads(environ["wsgi.input"].read(length))
            if not isinstance(data, dict):
                raise ValueError("Request cần là một JSON object.")
            if path == "/api/runs":
                return response(start, create_run(data), "201 Created")
            match = re.fullmatch(r"/api/runs/([a-z0-9]+)/start", path)
            if match:
                return response(start, start_run(match[1]), "202 Accepted")
        if method == "GET":
            if path == "/api/config":
                settings = read_json(SAMPLE / "settings.json")
                return response(start, {"model": settings["model"], "thinking": settings["enable_thinking"],
                                        "configured": bool(server_key()), "max_upload_mb": 20})
            if path == "/api/health":
                return response(start, {"status": "ok"})
            match = re.fullmatch(r"/api/runs/([a-z0-9]+)(?:/(classes|advanced|download)(?:/([^/]+))?)?", path)
            if match:
                run_id, action, table = match.groups()
                if not action:
                    return response(start, snapshot(run_id))
                if action == "classes":
                    return response(start, table_detail(run_id, table))
                if action == "advanced":
                    return response(start, advanced(run_id, query.get("table", [""])[0], int(query.get("step", ["2"])[0])))
                if action == "download":
                    body, mime, filename = download(run_id, query.get("kind", ["ontology"])[0], query.get("table", [""])[0])
                    return response(start, body, mime=mime, headers=[("Content-Disposition", f'attachment; filename="{filename}"')])
            assets = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
            if path in assets:
                file = APP / "static" / assets[path]
                return response(start, file.read_bytes(), mime=mimetypes.guess_type(str(file))[0] + "; charset=utf-8")
        return response(start, {"error": "Không tìm thấy đường dẫn."}, "404 Not Found")
    except FileNotFoundError as error:
        return response(start, {"error": redact(error)}, "404 Not Found")
    except (ValueError, TypeError, KeyError) as error:
        return response(start, {"error": redact(error)}, "400 Bad Request")
    except Exception as error:
        return response(start, {"error": redact(error)}, "500 Internal Server Error")


def main():
    parser = argparse.ArgumentParser(description="Ontology Studio — web local")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    os.umask(0o077)
    RUNTIME.mkdir(exist_ok=True)
    # Sau khi server khởi động lại, worker cũ không được tự động gọi thêm API.
    for file in (RUNTIME / "runs").glob("*/state.json"):
        state = read_json(file)
        if state.get("status") in {"RUNNING", "EXTRACTING"}:
            update_state(file.parent, status="ERROR", error="Server đã khởi động lại. Kiểm tra rồi thử lại.")
    with make_server("127.0.0.1", args.port, application) as server:
        print(f"Ontology Studio: http://127.0.0.1:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
