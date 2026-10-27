"""Một worker cho một lần chạy. Chỉ dùng hàm; không dùng framework điều phối."""
import os
import shutil
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import pipeline_core as core
from storage import STEPS, read_json, redact, server_key, update_state


def configure(folder):
    core.DATA = folder
    core.S1, core.S2, core.S3, core.S4, core.S5 = [folder / name for name in STEPS]
    core.settings = read_json(folder / "settings.json")
    core.FORCE = False
    core.RUN_AI_REVIEW = True
    os.environ["DC5_API_KEY"] = server_key()


def ddl_authorizer(action, first, second, database, trigger):
    # Chỉ dựng bảng/index trong RAM; không chạy SQL upload như một script tùy ý.
    permitted = {sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_REINDEX,
                 sqlite3.SQLITE_READ, sqlite3.SQLITE_TRANSACTION}
    if action in permitted:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and second not in {"load_extension", "readfile", "writefile"}:
        return sqlite3.SQLITE_OK
    if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE} and first == "sqlite_master":
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def extract(folder):
    source = core.S1 / "input"
    database = source / "database.local.sqlite3"
    sql = source / "schema.sql"
    metadata = read_json(source / "metadata.json", {})
    started = time.monotonic()
    if database.is_file():
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        kind = "sqlite_read_only"
        input_path = database
    else:
        connection = sqlite3.connect(":memory:")
        connection.set_authorizer(ddl_authorizer)
        connection.set_progress_handler(lambda: int(time.monotonic() - started > 15), 10000)
        connection.executescript(sql.read_text(encoding="utf-8"))
        connection.set_authorizer(None)
        kind = "schema_sql_in_memory"
        input_path = sql
    with closing(connection):
        connection.set_progress_handler(lambda: int(time.monotonic() - started > 30), 10000)
        tables = core.read_schema(connection, metadata)
    core.require(bool(tables), "Không tìm thấy bảng trong schema.")
    core.require(len(tables) <= 100, "Bản local hỗ trợ tối đa 100 bảng mỗi lần chạy.")
    core.require(all(t["name"].isidentifier() and len(t["name"]) <= 100 for t in tables),
                 "Tên bảng cần gồm chữ, số hoặc dấu gạch dưới, bắt đầu bằng chữ/gạch dưới.")
    payload = {"database_id": core.settings["database_id"], "tables": tables}
    inventory = {**payload, "schema_fingerprint": core.fingerprint(payload),
                 "source": {"kind": kind, "sha256": core.file_hash(input_path)}}
    core.write_json(core.S1 / "output/schema_inventory.json", inventory)
    update_state(folder, status="READY", step=1, completed=1, total=1, current_table="",
                 message="Schema đã sẵn sàng. Kiểm tra preview rồi bắt đầu tạo Ontology.",
                 table_count=len(tables), pid=None)


def prepare_step(step):
    inventory = core.S1 / "output/schema_inventory.json"
    destination = [core.S1, core.S2, core.S3, core.S4, core.S5][step - 1]
    core.copy_file(inventory, destination / "input/schema_inventory.json")
    names = [t["name"] for t in read_json(inventory)["tables"]]
    if step == 3:
        for name in names:
            core.copy_file(core.S2 / f"output/base_ontologies/{name}.json",
                           core.S3 / f"input/base_ontologies/{name}.json")
    if step == 4:
        for name in names:
            core.copy_file(core.S3 / f"output/reference_contexts/{name}.json",
                           core.S4 / f"input/reference_contexts/{name}.json")


def run_pipeline(folder):
    # Hook tại biên gọi LLM để ghi tiến độ thật, không dự đoán phần trăm theo thời gian.
    original_ask = core.ask_llm
    finished = {}
    inventory = read_json(core.S1 / "output/schema_inventory.json")
    totals = {2: len(inventory["tables"]),
              4: sum(bool(t["foreign_keys"]) for t in inventory["tables"]),
              5: len(inventory["tables"])}

    def tracked_ask(step, name, context, check):
        number = STEPS.index(step.name) + 1
        update_state(folder, step=number, current_table=name, completed=finished.get(number, 0),
                     total=totals[number], message=f"Đang xử lý bảng {name}.")
        result = original_ask(step, name, context, check)
        finished[number] = finished.get(number, 0) + 1
        update_state(folder, completed=finished[number])
        return result

    core.ask_llm = tracked_ask
    for number, function in [(2, core.build_bases), (3, core.build_contexts), (4, core.build_relations)]:
        update_state(folder, step=number, current_table="", completed=0, total=totals.get(number, 1),
                     message=f"Đang thực hiện bước {number}.")
        prepare_step(number)
        function()
    update_state(folder, step=5, current_table="", completed=0, total=totals[5],
                 message="Đang kiểm định cấu trúc và review ngữ nghĩa.")
    core.prepare_validation_inputs()
    report = core.validate_and_export()
    update_state(folder, status="COMPLETE", current_table="", completed=totals[5], total=totals[5],
                 message="Đã hoàn tất pipeline. Xem kết quả và báo cáo kiểm định.", pid=None,
                 findings=len(report.get("findings", [])) + len(report.get("ai_review_findings", [])))


def main():
    folder = Path(sys.argv[1]).resolve()
    configure(folder)
    try:
        if sys.argv[2] == "extract":
            extract(folder)
        else:
            run_pipeline(folder)
    except Exception as error:
        # Một biên lỗi cho worker: báo lỗi rõ và giữ artifacts đã làm được để thử lại.
        message = redact(error)
        update_state(folder, status="ERROR", error=message, message="Xử lý thất bại. Xem chi tiết để thử lại.", pid=None)
        print(message, file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
