"""Kiểm chứng API đang chạy. --live-llm gọi DC5 thật cho fixture hai bảng."""
import argparse
import base64
import hashlib
import io
import json
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path

import requests

from storage import APP, SAMPLE


def verify(base_url, live):
    session = requests.Session()
    session.trust_env = False
    evidence = []

    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        evidence.append(label)
        print("PASS", label, flush=True)

    def get(path):
        response = session.get(base_url + path, timeout=20)
        response.raise_for_status()
        return response

    def post(path, value):
        return session.post(base_url + path, json=value, timeout=20)

    def upload(filename, content, metadata=None):
        response = post("/api/runs", {"filename": filename,
                         "content_base64": base64.b64encode(content).decode(),
                         "metadata": metadata or {}, "thinking": True})
        response.raise_for_status()
        return response.json()["run_id"]

    def wait_for(run_id, statuses, seconds=45):
        deadline = time.monotonic() + seconds
        previous = ""
        while time.monotonic() < deadline:
            run = get("/api/runs/" + run_id).json()
            signature = f"{run['status']} step={run['step']} table={run.get('current_table','')}"
            if signature != previous:
                print(signature, flush=True)
                previous = signature
            if run["status"] in statuses:
                return run
            time.sleep(1)
        raise AssertionError("Chờ pipeline quá thời gian kiểm thử.")

    source = SAMPLE / "05_validation/output/ontology.json"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    check(get("/api/health").json()["status"] == "ok", "Backend health")
    check("Ontology Studio" in get("/").text, "Landing Page được phục vụ")
    sample = get("/api/runs/sample").json()
    check(sample["sample"] and sample["counts"]["properties"] == 157, "Kết quả mẫu có nhãn và số liệu thật")
    detail = get("/api/runs/sample/classes/products").json()
    check(len(detail["base"]["methods"]) == 5 and len(detail["relations"]) == 7, "Product có 5 Methods, 7 Relations liên quan")
    for step in [2, 4, 5]:
        value = get(f"/api/runs/sample/advanced?table=products&step={step}").json()
        check(value["sent"] and all(key in value for key in ["context", "prompt", "contract"]), f"Context / Prompt / Contract bước {step}")
    bundle = get("/api/runs/sample/download?kind=all")
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        names = archive.namelist()
        check("ontology.json" in names and "class_ontologies/products.json" in names, "ZIP chứa ontology hợp nhất và từng Class")
        check(not any("secret" in n or "trace" in n for n in names), "ZIP không chứa secrets hoặc raw traces")
    check(post("/api/runs/sample/start", {}).status_code == 400, "Không chạy đè kết quả mẫu")
    check(post("/api/runs", {"filename":"x.db", "content_base64":"YQ=="}).status_code == 400, "Từ chối SQLite sai định dạng")
    check(session.post(base_url + "/api/runs", json={}, headers={"Origin":"http://example.invalid"}).status_code == 403,
          "Từ chối POST từ origin khác")
    bad_id = upload("invalid.sql", b"CREATE TABLE x(id INTEGER); INSERT INTO x VALUES(1);")
    bad = wait_for(bad_id, {"ERROR"})
    check("authorized" in bad["error"], "SQL schema không được thực thi INSERT")
    fk_id = upload("bad_fk.sql", b"CREATE TABLE x(id INTEGER PRIMARY KEY, y INTEGER REFERENCES absent(id));")
    bad_fk = wait_for(fk_id, {"ERROR"})
    check(bad_fk["status"] == "ERROR", "Báo lỗi FK thiếu bảng đích")
    ddl = """CREATE TABLE brands(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
    CREATE TABLE products(id INTEGER PRIMARY KEY, sku TEXT NOT NULL UNIQUE,
      name TEXT NOT NULL, brand_id INTEGER REFERENCES brands(id), price REAL CHECK(price>=0));"""
    marker = "PRIVATE_ROW_NOT_FOR_LLM_982713"
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "input.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript(ddl)
        connection.execute("INSERT INTO brands VALUES(1, 'Example')")
        connection.execute("INSERT INTO products VALUES(1,'SKU-01',?,1,10)", (marker,))
        connection.commit()
        connection.close()
        sqlite_bytes = path.read_bytes()
    run_id = upload("review.sqlite3", sqlite_bytes)
    run = wait_for(run_id, {"READY", "ERROR"})
    check(run["status"] == "READY" and run["counts"]["columns"] == 7, "Upload SQLite thật và trích xuất đúng 2 bảng / 7 cột")
    run_folder = APP / "runtime/runs" / run_id
    check((run_folder / "01_schema/input/database.local.sqlite3").read_bytes() == sqlite_bytes, "SQLite upload giữ nguyên nội dung")
    check(marker not in get(f"/api/runs/{run_id}/classes/products").text, "Preview schema không chứa dữ liệu từng dòng")
    if live:
        response = post(f"/api/runs/{run_id}/start", {})
        response.raise_for_status()
        run = wait_for(run_id, {"COMPLETE", "ERROR"}, seconds=600)
        check(run["status"] == "COMPLETE", "Full pipeline qua DC5 thật hoàn tất: " + run.get("error", ""))
        check(run["report"]["structural_status"] == "PASS", "Kiểm định cấu trúc PASS")
        check(run["report"]["ai_review_status"] == "COMPLETED", "Review ngữ nghĩa bằng DC5 hoàn tất")
        check(run["counts"]["classes"] == 2 and run["counts"]["relations"] == 1, "Một FK tạo đúng một Relation")
        generation_requests = []
        for step in ["02_base_ontology", "04_relations", "05_validation"]:
            for trace_path in (run_folder / step / "output/traces").glob("*.json"):
                trace = json.loads(trace_path.read_text())
                generation_requests.append(json.dumps(trace["request"]))
                check(trace["generation"]["enable_thinking"] is True and trace["status"] == "COMPLETE",
                      "DC5 thinking và response hoàn tất: " + step + "/" + trace_path.stem)
        check(all(marker not in request for request in generation_requests), "Request DC5 không có private row")
        output = get(f"/api/runs/{run_id}/download?kind=ontology").json()
        check(output["runtime_readiness"] == "NOT_IMPLEMENTED", "Ontology ghi đúng giới hạn Methods chưa thực thi")
        check(post(f"/api/runs/{run_id}/start", {}).status_code == 400, "Không khởi chạy đè run đã hoàn tất")
    check(hashlib.sha256(source.read_bytes()).hexdigest() == source_hash, "Bộ output mẫu không thay đổi")
    report = {"checks": evidence, "count": len(evidence), "live_llm": live, "run_id": run_id}
    (APP / "runtime/verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"checks": len(evidence), "run_id": run_id, "live_llm": live}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-llm", action="store_true")
    args = parser.parse_args()
    verify("http://127.0.0.1:8787", args.live_llm)
