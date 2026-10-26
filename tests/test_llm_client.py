import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from database_base_ontology.common import load_json, save_json
from database_base_ontology.llm_client import DC5Client


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.secret = "fixture-secret-never-in-trace"
        path = self.root / "config.yaml"
        save_json(path, {"dc5": {"base_url": "https://example.invalid/v1", "api_key": self.secret, "model": "fixture-qwen"}})
        self.client = DC5Client({"config_path": str(path), "proxy_mode": "none", "json_attempts": 2})
        self.client.session = Mock()

    def tearDown(self):
        self.temp.cleanup()

    def response(self, raw, finish_reason="stop"):
        response = Mock(ok=True)
        response.json.return_value = {"choices": [{"message": {"content": raw}, "finish_reason": finish_reason}], "usage": {"total_tokens": 1}}
        return response

    def test_repairs_invalid_json_and_traces_without_api_key(self):
        self.client.session.post.side_effect = [self.response("not JSON"), self.response('{"relations": []}')]
        trace_path = self.root / "trace.json"
        result = self.client.generate("describe_relations", {"owned_foreign_keys": []}, trace_path, lambda value: None)
        self.assertEqual(result, {"relations": []})
        trace = load_json(trace_path)
        self.assertEqual(len(trace["attempts"]), 2)
        self.assertIn("validation_error", trace["attempts"][0])
        self.assertNotIn(self.secret, trace_path.read_text())
        self.assertIn("Authorization", self.client.session.post.call_args.kwargs["headers"])

    def test_invalid_output_stops_without_fallback(self):
        self.client.session.post.return_value = self.response("not JSON")
        with self.assertRaisesRegex(ValueError, "Invalid describe_relations response"):
            self.client.generate("describe_relations", {}, self.root / "trace.json", lambda value: None)
        self.assertEqual(self.client.session.post.call_count, 2)

    def test_truncated_json_cannot_be_accepted(self):
        self.client.session.post.return_value = self.response('{"relations": []}', finish_reason="length")
        with self.assertRaisesRegex(ValueError, "truncated"):
            self.client.generate("describe_relations", {}, self.root / "trace.json", lambda value: None)

    def test_http_error_is_logged(self):
        self.client.session.post.return_value = Mock(ok=False, status_code=503, text="Rejected " + self.secret)
        trace_path = self.root / "trace.json"
        with self.assertRaisesRegex(ValueError, "HTTP 503"):
            self.client.generate("describe_relations", {}, trace_path, lambda value: None)
        self.assertEqual(load_json(trace_path)["http_status"], 503)
        self.assertNotIn(self.secret, trace_path.read_text())


if __name__ == "__main__":
    unittest.main()
