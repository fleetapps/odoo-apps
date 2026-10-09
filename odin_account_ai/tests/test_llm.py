"""The Claude client: the request it sends, what it logs, and when it
refuses to call (no consent, budget spent, a retried request)."""

from odoo.exceptions import UserError
from odoo.tests import tagged

from ..models.odin_ai_llm import echo_content, final_json
from .common import AiCase, message


@tagged("post_install", "-at_install")
class TestLlm(AiCase):

    def call(self, feature="explain", **kwargs):
        return self.env["odin.ai.llm"].call(
            feature, system="Rules.", messages=[{"role": "user", "content": "Hello"}],
            schema={"type": "object", "additionalProperties": False, "required": ["a"],
                    "properties": {"a": {"type": "string"}}}, **kwargs)

    def test_the_request_and_its_log(self):
        requests = self.mock_api(message({"a": "b"}))
        data = self.call()
        self.assertEqual(final_json(data), {"a": "b"})
        params = requests[0]
        self.assertEqual(params["model"], "claude-opus-5-5")
        self.assertEqual(params["system"], [{"type": "text", "text": "Rules.", "cache_control": {"type": "ephemeral"}}])
        self.assertEqual(params["output_config"]["effort"], "low")
        self.assertEqual(params["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(params["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(params["fallbacks"], "default")
        self.assertNotIn("thinking", params, "Opus 5.5 thinks adaptively: no thinking parameter")
        self.assertNotIn("tool_choice", params)
        run = self.env["odin.ai.run"].search([], limit=1)
        self.assertEqual((run.feature, run.status, run.total_tokens), ("explain", "ok", 120))

    def test_haiku_gets_no_server_fallback(self):
        self.env["ir.config_parameter"].sudo().set_param("odin_account_ai.model", "claude-haiku-5-5")
        requests = self.mock_api(message({"a": "b"}))
        self.call()
        self.assertNotIn("fallbacks", requests[0])
        self.assertNotIn("betas", requests[0])

    def test_a_refusal_is_reported_and_logged(self):
        self.mock_api(message(content=[], stop="refusal"))
        with self.assertRaisesRegex(UserError, "declined"):
            self.call()
        self.assertEqual(self.env["odin.ai.run"].search([], limit=1).status, "refusal")

    def test_an_answer_cut_short_is_not_used(self):
        self.mock_api(message(content=[{"type": "text", "text": '{"a": "b'}], stop="max_tokens"))
        with self.assertRaisesRegex(UserError, "too long"):
            self.call()

    def test_nothing_is_sent_without_consent(self):
        self.env.company.odin_ai_consent = False
        requests = self.mock_api()
        self.assertTrue(self.env["odin.ai.llm"].readiness())
        with self.assertRaisesRegex(UserError, "allow sending"):
            self.call()
        self.assertFalse(requests)

    def test_nothing_is_sent_once_the_budget_is_spent(self):
        self.env.company.odin_ai_monthly_tokens = 1000
        self.env["odin.ai.run"].sudo().create({"company_id": self.env.company.id, "input_tokens": 1000})
        requests = self.mock_api()
        with self.assertRaisesRegex(UserError, "budget"):
            self.call()
        self.assertFalse(requests)

    def test_a_retried_request_reuses_the_answer(self):
        requests = self.mock_api(message({"a": "first"}))
        first = self.call(step_key="ask-1-0")
        second = self.call(step_key="ask-1-0")
        self.assertEqual(len(requests), 1)
        self.assertEqual(final_json(first), final_json(second))

    def test_tokens_of_every_attempt_count(self):
        self.mock_api(message({"a": "b"}, usage={"input_tokens": 50, "output_tokens": 5, "iterations": [
            {"type": "message", "input_tokens": 100, "output_tokens": 10},
            {"type": "fallback_message", "input_tokens": 50, "output_tokens": 5}]}))
        self.call()
        run = self.env["odin.ai.run"].search([], limit=1)
        self.assertEqual((run.input_tokens, run.output_tokens), (150, 15))

    def test_the_real_call_never_runs_under_tests(self):
        with self.assertRaisesRegex(UserError, "disabled while tests run"):
            self.env["odin.ai.llm"]._create_message({}, {})

    def test_echo_after_a_fallback(self):
        content = [
            {"type": "thinking", "thinking": "declined", "signature": "x"},
            {"type": "text", "text": "partial"},
            {"type": "tool_use", "id": "t1", "name": "pnl", "input": {}},
            {"type": "fallback", "model": "claude-opus-5-5"},
            {"type": "thinking", "thinking": "kept", "signature": "y"},
            {"type": "text", "text": "answer"},
        ]
        self.assertEqual([b["type"] for b in echo_content(content)], ["text", "fallback", "thinking", "text"])
        self.assertEqual(echo_content(content[4:]), content[4:], "no fallback: echoed unchanged")
        self.assertEqual(final_json(message(content=[
            {"type": "text", "text": '{"a": "partial'}, {"type": "fallback"}, {"type": "text", "text": '{"a": "b"}'}])),
            {"a": "b"})
