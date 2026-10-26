import json
import os
from importlib.resources import files

import requests
import yaml

from .common import ROOT, fingerprint, save_json
from .validators import contract


def configure_proxy(settings):
    mode = settings.get("proxy_mode", "company")
    if mode not in {"company", "environment", "none"}:
        raise ValueError("proxy_mode must be company, environment or none")
    if mode == "company":
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            os.environ[key] = settings.get("proxy_url", "http://proxy.hcm.fpt.vn:80")
        os.environ["no_proxy"] = ""
        os.environ["NO_PROXY"] = ""
    return mode != "none"


def parse_json_response(text):
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be one JSON object")
    return parsed


class DC5Client:
    def __init__(self, settings):
        self.settings = settings
        config_path = (ROOT / settings["config_path"]).resolve()
        profiles = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.profile = profiles[settings.get("profile", "dc5")]
        for field in ("base_url", "api_key", "model"):
            if not self.profile.get(field):
                raise ValueError(f"DC5 profile is missing {field}")
        self.session = requests.Session()
        self.session.trust_env = configure_proxy(settings)
        self.identity = {"model": self.profile["model"], "base_url": self.profile["base_url"],
                         "temperature": settings.get("temperature", 0), "max_tokens": settings.get("max_tokens", 6000),
                         "enable_thinking": settings.get("enable_thinking", False)}

    def generation_fingerprint(self, task):
        prompt = files("database_base_ontology").joinpath("prompts", task + ".txt").read_text(encoding="utf-8")
        schema = contract("base_proposal" if task == "build_base_ontology" else "relation_proposal")
        return fingerprint({"client": self.identity, "prompt": prompt, "contract": schema})

    def generate(self, task, context, trace_path, validate):
        system = files("database_base_ontology").joinpath("prompts", task + ".txt").read_text(encoding="utf-8")
        schema = contract("base_proposal" if task == "build_base_ontology" else "relation_proposal")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(
            {"contract": schema, "context": context}, ensure_ascii=False)}]
        attempts = []
        limit = self.settings.get("json_attempts", 2)
        if not isinstance(limit, int) or limit < 1 or limit > 3:
            raise ValueError("json_attempts must be between 1 and 3")
        for attempt in range(1, limit + 1):
            payload = {**{k: v for k, v in self.identity.items() if k not in {"base_url", "enable_thinking"}},
                       "messages": messages, "chat_template_kwargs": {"enable_thinking": self.identity["enable_thinking"]}}
            trace = {"task": task, "llm": self.identity, "messages": messages, "attempts": attempts}
            save_json(trace_path, trace)
            response = self.session.post(self.profile["base_url"].rstrip("/") + "/chat/completions",
                                         headers={"Authorization": "Bearer " + self.profile["api_key"]}, json=payload,
                                         timeout=(self.settings.get("connect_timeout", 15), self.settings.get("read_timeout", 120)))
            if not response.ok:
                error_body = response.text.replace(self.profile["api_key"], "[REDACTED]")[:2000]
                save_json(trace_path, {**trace, "http_status": response.status_code, "error_body": error_body})
                raise ValueError(f"DC5 HTTP {response.status_code}; inspect trace {trace_path}")
            body = response.json()
            choice = body["choices"][0]
            raw = choice["message"].get("content") or ""
            record = {"attempt": attempt, "raw_output": raw, "finish_reason": choice.get("finish_reason"),
                      "usage": body.get("usage", {})}
            attempts.append(record)
            try:
                if choice.get("finish_reason") == "length":
                    raise ValueError("Response truncated; increase llm.max_tokens")
                result = parse_json_response(raw)
                validate(result)
            except ValueError as error:
                record["validation_error"] = str(error)
                save_json(trace_path, trace)
                if attempt == limit:
                    raise ValueError(f"Invalid {task} response; inspect {trace_path}: {error}") from error
                messages = messages + [{"role": "assistant", "content": raw}, {"role": "user", "content":
                    "Sửa JSON theo contract và input. Không thay schema. Lỗi kiểm định: " + str(error)}]
                continue
            save_json(trace_path, trace)
            return result

    def close(self):
        self.session.close()
