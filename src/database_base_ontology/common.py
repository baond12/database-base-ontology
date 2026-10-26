import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote

import yaml


ROOT = Path(__file__).resolve().parents[2]


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def fingerprint(data):
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_path(root, table):
    return Path(root) / (quote(table, safe="") + ".json")


def class_id(table):
    return "class:" + table


def property_id(table, column):
    return "property:" + table + "." + column


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def has_unique_key(table, columns):
    keys = [table["primary_key"]] if table["primary_key"] else []
    keys.extend(index["columns"] for index in table["indexes"]
                if index["unique"] and not index["partial"] and None not in index["columns"])
    return any(set(key) <= set(columns) for key in keys)


def load_config(path):
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    for key in ("database_id", "sqlite_path", "run_id"):
        if not config.get(key):
            raise ValueError(f"Missing config field: {key}")
    for key in ("database_id", "run_id"):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", config[key]):
            raise ValueError(f"{key} must contain only letters, numbers, underscores or hyphens")
    for key in ("sqlite_path", "metadata_path", "output_root"):
        value = config.get(key, "artifacts" if key == "output_root" else None)
        config[key] = (ROOT / value).resolve() if value else None
    config["output_dir"] = config["output_root"] / config["database_id"] / config["run_id"]
    config["config_path"] = str(Path(path).resolve())
    return config
