"""Đường dẫn và file trạng thái dùng chung cho API và worker."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parent
REPO = APP.parent
SAMPLE = REPO / "ontology_pipeline"
RUNTIME = APP / "runtime"
STEPS = ["01_schema", "02_base_ontology", "03_reference_context", "04_relations", "05_validation"]


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def update_state(folder, **changes):
    state = read_json(folder / "state.json", {})
    state.update(changes, updated_at=datetime.now(timezone.utc).isoformat())
    write_json(folder / "state.json", state)
    return state


def server_key():
    return os.environ.get("DC5_API_KEY") or read_json(SAMPLE / "secrets.local.json", {}).get("api_key", "")


def redact(message):
    key = server_key()
    return str(message).replace(key, "[REDACTED]") if key else str(message)
