"""Ask the Ledger: one model call per step, tools run as the asking user,
answers keep only the evidence the tools produced."""

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from ..models.odin_ai_conversation import MAX_STEPS
from .common import AiCase, message, tool_call

PNL_SEPTEMBER = {"date_from": "2026-09-01", "date_to": "2026-09-30", "comparison": "none", "line_code": None}


def answer(text, evidence, follow_ups=()):
    return message({
        "blocks": [{"type": "text", "text": text, "items": None, "columns": None, "rows": None,
                    "value": None, "evidence": list(evidence)}],
        "follow_ups": list(follow_ups),
    })


@tagged("post_install", "-at_install")
class TestAsk(AiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.post("2026-09-10", [(cls.revenue, -1000.0), (cls.opex, 300.0)])
        cls.Conversation = cls.env["odin.ai.conversation"].with_user(cls.accountant)

    def start(self, question="How much revenue in September?"):
        return self.Conversation.browse(self.Conversation.ask_start(question))

    def test_a_tool_step_then_the_answer(self):
        requests = self.mock_api(
            tool_call("pnl", PNL_SEPTEMBER),
            answer("Revenue was 1,000 [E1], more than ever [E99].", ["E1", "E99"], ["a", "b", "c", "d"]),
        )
        conversation = self.start()
        thread = conversation.ask_step()
        self.assertEqual(thread["state"], "running")
        self.assertEqual(thread["turns"][0]["progress"], ["Reading the P&L for Sep 2026"])
        thread = conversation.ask_step()
        self.assertEqual(thread["state"], "done")
        result = thread["turns"][0]["answer"]
        self.assertEqual(result["blocks"][0]["evidence"], ["E1"], "a handle the tools did not produce is dropped")
        self.assertEqual(len(result["follow_ups"]), 3)
        # The second request continues the same exchange, append-only.
        first, second = requests
        self.assertEqual(second["system"], first["system"])
        self.assertEqual(second["tools"], first["tools"])
        self.assertEqual([tool["name"] for tool in first["tools"]], sorted(tool["name"] for tool in first["tools"]))
        self.assertTrue(all(tool["strict"] for tool in first["tools"]))
        self.assertEqual(second["messages"][:1], first["messages"])
        self.assertEqual([m["role"] for m in second["messages"]], ["user", "assistant", "user"])
        self.assertEqual(second["messages"][2]["content"][0]["type"], "tool_result")
        self.assertEqual(second["messages"][2]["content"][0]["tool_use_id"], "toolu_1")
        # The reference opens the P&L on that row.
        action = conversation.open_evidence("E1")
        self.assertEqual(action["tag"], "odin_account_pnl.report")
        self.assertEqual(action["params"]["focus"], "L:REV")

    def test_a_follow_up_continues_the_conversation(self):
        requests = self.mock_api(
            answer("Hello.", []),
            answer("Still hello.", []),
        )
        conversation = self.start()
        conversation.ask_step()
        thread = conversation.ask_followup("And in August?")
        self.assertEqual(thread["state"], "running")
        thread = conversation.ask_step()
        self.assertEqual(thread["state"], "done")
        self.assertEqual([turn["question"] for turn in thread["turns"]], ["How much revenue in September?", "And in August?"])
        self.assertEqual([m["role"] for m in requests[1]["messages"]], ["user", "assistant", "user"])
        self.assertEqual(requests[1]["messages"][:2], requests[0]["messages"] + [
            {"role": "assistant", "content": requests[1]["messages"][1]["content"]}])

    def test_too_many_steps_stop_the_question(self):
        self.mock_api(*[tool_call("pnl", PNL_SEPTEMBER, call_id=f"toolu_{i}") for i in range(MAX_STEPS)])
        conversation = self.start()
        for _step in range(MAX_STEPS):
            conversation.ask_step()
        thread = conversation.ask_step()
        self.assertEqual(thread["state"], "error")

    def test_a_failed_call_can_be_retried(self):
        requests = self.mock_api(UserError("The AI is busy right now"), answer("Done.", []))
        conversation = self.start()
        self.assertEqual(conversation.ask_step()["state"], "error")
        conversation.ask_retry()
        self.assertEqual(conversation.ask_step()["state"], "done")
        self.assertEqual(requests[0]["messages"], requests[1]["messages"])

    def test_tool_calls_of_a_declined_model_are_not_run(self):
        requests = self.mock_api(
            message(content=[
                {"type": "tool_use", "id": "toolu_declined", "name": "entry", "input": {"number": "X"}},
                {"type": "fallback", "model": "claude-opus-5-5"},
                {"type": "tool_use", "id": "toolu_2", "name": "pnl", "input": PNL_SEPTEMBER},
            ], stop="tool_use"),
            answer("Done.", []),
        )
        conversation = self.start()
        thread = conversation.ask_step()
        self.assertEqual(thread["turns"][0]["progress"], ["Reading the P&L for Sep 2026"])
        conversation.ask_step()
        echoed = requests[1]["messages"][1]["content"]
        self.assertEqual([block["type"] for block in echoed], ["fallback", "tool_use"])
        self.assertEqual([r["tool_use_id"] for r in requests[1]["messages"][2]["content"]], ["toolu_2"])

    def test_only_the_owner_and_readers_of_the_books(self):
        self.mock_api()
        conversation = self.start()
        other = new_test_user(self.env, login="ai_other", groups="base.group_user,account.group_account_user",
                              company_id=self.env.company.id, company_ids=[self.env.company.id])
        with self.assertRaises(AccessError):
            conversation.with_user(other).get_thread()
        with self.assertRaises(AccessError):
            self.env["odin.ai.conversation"].with_user(self.billing).ask_start("Revenue?")

    def test_tools_read_with_the_users_rights(self):
        other = self.setup_other_company()
        journal = self.env["account.journal"].create(
            {"name": "Foreign misc", "code": "FRGN", "type": "general", "company_id": other["company"].id})
        foreign = self.env["account.move"].create({
            "move_type": "entry", "company_id": other["company"].id, "journal_id": journal.id,
            "date": "2026-09-10",
            "line_ids": [
                Command.create({"account_id": other["default_account_revenue"].id, "credit": 50.0, "name": "x"}),
                Command.create({"account_id": other["default_account_expense"].id, "debit": 50.0, "name": "x"}),
            ],
        })
        foreign.action_post()
        self.mock_api()
        conversation = self.start()
        tools = self.env["odin.ai.tools"].with_user(self.accountant)
        result, _label = tools.run(conversation, "entry", {"number": foreign.name})
        self.assertIn("error", result)
        result, _label = tools.run(conversation, "journal_items", {
            "date_from": "2026-09-01", "date_to": "2026-09-30", "account_code": None, "line_code": None,
            "partner": None, "text": None, "min_amount": None, "order": "largest", "limit": 50})
        self.assertNotIn(foreign.name, {item["entry"] for item in result["items"]})
        self.assertTrue(result["items"])
