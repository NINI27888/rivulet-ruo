import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
from workspace import provider_llm  # noqa: E402
from workspace.chat import ChatService  # noqa: E402
from workspace.store import Store  # noqa: E402
from workspace.tools import Registry, Tool  # noqa: E402


GO_BASE = "https://opencode.ai/inference/go/openai/v1"
MESSAGE = [{"role": "user", "content": "Reply OK."}]
SESSION_HEADER = "x-opencode-session"


class FixtureStatusTool(Tool):
    name = "fixture.status"
    description = "Return fixture status."

    def run(self, args):
        return {"available": True}


class OpenCodeGoTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.responses = []
        opener = patch.object(provider_llm._opener, "open", side_effect=self._open)
        self.addCleanup(opener.stop)
        opener.start()

    def _open(self, request, timeout):
        self.requests.append((
            request.full_url,
            {name.lower(): value for name, value in request.header_items()},
            json.loads(request.data),
            timeout,
        ))
        message = self.responses.pop(0) if self.responses else {"content": " OK "}
        return io.BytesIO(json.dumps({"choices": [{"message": message}]}).encode("utf-8"))

    def _settings(self, base=GO_BASE, model="deepseek-v4.1-flash"):
        return provider_llm.model_settings({
            "provider": "custom",
            "base_url": base,
            "model": model,
            "api_key": "fixture-key",
        })

    def _session(self, request):
        session = request[1].get(SESSION_HEADER, "")
        self.assertRegex(session, r"^ses_[0-9a-f]{32}$")
        return session

    def test_official_go_routes_send_rivulet_headers_and_disable_thinking(self):
        bases = (GO_BASE, "https://opencode.ai/inference/go/v1", "https://opencode.ai/zen/go/v1")
        for base in bases:
            with self.subTest(base=base):
                settings = self._settings(base + "/chat/completions/")
                result = provider_llm.complete_message(settings, MESSAGE, session_id="fixture-conversation")
                self.assertEqual(result["content"], "OK")

                url, headers, payload, _ = self.requests[-1]
                self.assertEqual(url, base + "/chat/completions")
                self.assertRegex(headers.get("user-agent", ""), r"^Rivulet(?:/|$)")
                self.assertEqual(headers["authorization"], "Bearer fixture-key")
                self._session(self.requests[-1])
                self.assertEqual(payload["model"], "deepseek-v4.1-flash")
                self.assertEqual(payload["messages"], MESSAGE)
                self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_go_keeps_standard_chat_payload_for_other_models(self):
        provider_llm.complete_message(self._settings(model="minimax-m2.7"), MESSAGE)

        self._session(self.requests[-1])
        _, _, payload, _ = self.requests[-1]
        self.assertEqual(payload["model"], "minimax-m2.7")
        self.assertNotIn("thinking", payload)

    def test_go_session_ids_are_stable_per_scope_and_probes_get_fresh_ids(self):
        settings = self._settings()
        for scope in ("first conversation", "another conversation", "first conversation"):
            provider_llm.complete_message(settings, MESSAGE, session_id=scope)
        provider_llm.probe_model(settings)
        provider_llm.probe_model(settings)

        first, another, resumed, probe, next_probe = map(self._session, self.requests)
        self.assertEqual(first, resumed)
        self.assertEqual(len({first, another, probe, next_probe}), 4)
        for _, _, payload, timeout in self.requests[3:]:
            self.assertEqual(payload["max_tokens"], 128)
            self.assertEqual(timeout, 18)

    def test_go_auth_errors_point_to_opencode_go_configuration(self):
        settings = self._settings()
        for status, hint in ((401, "OpenCode Go"), (403, "Global")):
            with self.subTest(status=status):
                error = HTTPError(GO_BASE, status, "Fixture provider error", None, None)
                with patch.object(provider_llm._opener, "open", side_effect=error):
                    with self.assertRaises(provider_llm.ModelError) as raised:
                        provider_llm.probe_model(settings)
                self.assertIn(hint, str(raised.exception))
                self.assertIn(f"HTTP {status}", str(raised.exception))

    def test_other_endpoints_do_not_get_go_headers_or_thinking_option(self):
        bases = (
            "https://example.invalid/inference/go/openai/v1",
            "https://opencode.ai.example.invalid/inference/go/openai/v1",
            "http://opencode.ai/inference/go/openai/v1",
            "https://opencode.ai:8443/inference/go/openai/v1",
            "https://opencode.ai/inference/go/openai/v10",
        )
        for base in bases:
            with self.subTest(base=base):
                provider_llm.complete_message(self._settings(base), MESSAGE, session_id="fixture-conversation")
                _, headers, payload, _ = self.requests[-1]
                self.assertNotIn(SESSION_HEADER, headers)
                self.assertNotIn("user-agent", headers)
                self.assertNotIn("thinking", payload)

    def test_deepseek_official_endpoint_preserves_chat_parameters(self):
        settings = provider_llm.model_settings({
            "provider": "deepseek",
            "model": "deepseek-flash",
            "api_key": "fixture-key",
        })
        reply = provider_llm.complete_message(settings, MESSAGE, max_tokens=234, temperature=0.4)

        url, headers, payload, _ = self.requests[-1]
        self.assertEqual(reply["content"], "OK")
        self.assertEqual(url, "https://api.deepseek.com/v1/chat/completions")
        self.assertNotIn(SESSION_HEADER, headers)
        self.assertNotIn("user-agent", headers)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["max_tokens"], 234)
        self.assertEqual(payload["temperature"], 0.4)

    def test_workspace_tool_rounds_and_followup_share_conversation_session(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(str(Path(tmp.name) / "workspace.db"))
        self.addCleanup(store.close)
        models = Mock()
        models.active.return_value = self._settings()
        provider = provider_llm.LLMProvider(models)
        with patch.object(provider, "persona_brief", return_value="Fixture persona"):
            chat = ChatService(store, Registry().register(FixtureStatusTool()), provider)
            self.responses.append({"tool_calls": [{
                "id": "call_fixture",
                "type": "function",
                "function": {"name": "fixture_status", "arguments": "{}"},
            }]})
            first = chat.send("Check status.")
            self.assertTrue(first["ok"], first)
            self.assertEqual(first["tool_calls"], [{"name": "fixture.status", "args": {}, "ok": True}])
            followup = chat.send("Continue.", first["conversation_id"])
            other = chat.send("Start another chat.")
            self.assertTrue(followup["ok"], followup)
            self.assertTrue(other["ok"], other)

        initial, tool_round, followup, other = map(self._session, self.requests)
        self.assertEqual(initial, tool_round)
        self.assertEqual(initial, followup)
        self.assertNotEqual(initial, other)
        tool_message = self.requests[1][2]["messages"][-1]
        self.assertEqual(tool_message["tool_call_id"], "call_fixture")
        self.assertEqual(json.loads(tool_message["content"]), {"available": True})


if __name__ == "__main__":
    unittest.main()
