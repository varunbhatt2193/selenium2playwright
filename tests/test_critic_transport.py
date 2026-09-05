"""Exercise native JSON output through the installed SDK using a mock HTTP transport."""

import json
import os
import unittest
from unittest.mock import patch

import anthropic
import httpx2 as httpx
from langchain_anthropic import ChatAnthropic

from selenium2playwright import graph
from selenium2playwright.schemas import ConversionResult, ValidationReport


class CriticTransportTests(unittest.TestCase):
    def test_native_json_schema_and_effort_reach_the_anthropic_request(self):
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "id": "msg_offline_critic", "type": "message", "role": "assistant",
                "model": "claude-sonnet-5", "stop_reason": "end_turn", "stop_sequence": None,
                "content": [{"type": "text", "text": '{"verdict":"pass","fixes":[]}'}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            })

        state = {
            "source_path": "LoginPage.ts", "source": "source evidence", "context": "",
            "result": ConversionResult(code="converted evidence"),
            "validation": [ValidationReport(gate=g, passed=True) for g in ("compile", "residue", "lint", "parity")],
        }
        settings = {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
                    "S2P_MODEL": "anthropic:claude-sonnet-5", "ANTHROPIC_API_KEY": "offline-test-key"}
        with patch.dict(os.environ, settings), httpx.Client(transport=httpx.MockTransport(respond)) as http:
            client = anthropic.Anthropic(api_key="offline-test-key", http_client=http, max_retries=0)
            with patch.object(ChatAnthropic, "_client", client):
                update = graph.critic(state)

        self.assertEqual(update["critique_error"], "")
        self.assertEqual(update["critique"].verdict, "pass")
        self.assertEqual(update["critic_usage"]["total_tokens"], 15)
        self.assertEqual(len(requests), 1)
        payload = requests[0]
        self.assertEqual(payload["output_config"]["effort"], "medium")
        self.assertEqual(payload["output_config"]["format"]["type"], "json_schema")
        self.assertIn("verdict", payload["output_config"]["format"]["schema"]["properties"])
        self.assertNotIn("tool_choice", payload)


if __name__ == "__main__":
    unittest.main()
