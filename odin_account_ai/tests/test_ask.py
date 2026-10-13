"""Ask the Ledger: one model call per step, tools run as the asking user,
answers keep only the evidence the tools produced."""

import json
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from ..models import odin_ai_conversation
from ..models.odin_ai_conversation import LAST_STEP_NOTE, MAX_STEPS
from ..models.odin_ai_tools import legacy_tool_definitions, tool_definitions
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

    # ------------------------------------------------------------------
    # Stopping, retrying, rating
    # ------------------------------------------------------------------

    def test_stop_between_two_steps(self):
        requests = self.mock_api(tool_call("pnl", PNL_SEPTEMBER), answer("Done.", []))
        conversation = self.start()
        self.assertEqual(conversation.ask_step()["state"], "running")
        thread = conversation.ask_stop()
        self.assertEqual(thread["state"], "stopped")
        self.assertTrue(thread["can_retry"], "the exchange still ends with the tool results")
        conversation.ask_retry()
        self.assertEqual(conversation.ask_step()["state"], "done")
        self.assertEqual(len(requests), 2)

    def test_stop_never_writes_a_row_a_step_holds(self):
        """A step holds the row while it waits on the API: writing it from
        Stop would wait for that call, then fail on commit."""
        self.mock_api()
        conversation = self.start()
        Conversation = self.registry["odin.ai.conversation"]
        with patch.object(Conversation, "try_lock_for_update", lambda records, **kwargs: records.browse()), \
                patch.object(Conversation, "write", side_effect=AssertionError("Stop wrote a locked row")):
            thread = conversation.ask_stop()
        self.assertTrue(thread["busy"])
        self.assertEqual(conversation.state, "running")

    def test_conversations_are_written_only_by_their_methods(self):
        self.mock_api()
        conversation = self.start()
        other = new_test_user(self.env, login="ai_victim", groups="base.group_user,account.group_account_user",
                              company_id=self.env.company.id, company_ids=[self.env.company.id])
        for values in ({"user_id": other.id}, {"turns": "[]"}, {"evidence": "{}"}):
            with self.assertRaises(AccessError):
                conversation.write(values)
        with self.assertRaises(AccessError):
            self.Conversation.create({"name": "Forged", "user_id": other.id})
        self.assertEqual(conversation.user_id, self.accountant)

    def test_the_tool_list_is_the_one_the_conversation_started_with(self):
        """Thinking blocks are bound to the tools they were written with: a
        later release changing a tool must not change an open conversation."""
        requests = self.mock_api(answer("Hello.", []), answer("Again.", []))
        conversation = self.start()
        conversation.ask_step()
        changed = [dict(tool, description="Changed in a later release.") for tool in tool_definitions()]
        with patch.object(odin_ai_conversation, "tool_definitions", return_value=changed):
            conversation.ask_followup("And?")
            conversation.ask_step()
            self.assertEqual(self.start("Another?").tools_snapshot, json.dumps(changed))
        self.assertEqual(requests[1]["tools"], requests[0]["tools"])

    def test_a_conversation_from_before_keeps_the_tools_of_its_time(self):
        """Conversations started before the tool list was stored get the
        tools as they were then, not this release's."""
        requests = self.mock_api(answer("Hello.", []))
        conversation = self.start()
        conversation.sudo().tools_snapshot = False
        conversation.ask_step()
        self.assertEqual(requests[0]["tools"], legacy_tool_definitions())
        self.assertNotEqual(requests[0]["tools"], tool_definitions())

    def test_feedback_belongs_to_one_answer(self):
        self.mock_api(answer("One.", []), answer("Two.", []))
        conversation = self.start()
        conversation.ask_step()
        conversation.set_feedback("up", 0)
        conversation.ask_followup("And?")
        thread = conversation.ask_step()
        self.assertEqual(thread["turns"][0]["feedback"], "up")
        self.assertFalse(thread["turns"][1].get("feedback"), "a new answer starts unrated")
        conversation.set_feedback("down", 1)
        self.assertEqual(conversation.feedback, "down")
        self.assertEqual(json.loads(conversation.turns)[0]["feedback"], "up")

    def test_an_unreadable_answer_is_not_offered_again_in_place(self):
        self.mock_api(message(content=[{"type": "text", "text": "not JSON"}]))
        conversation = self.start()
        thread = conversation.ask_step()
        self.assertEqual(thread["state"], "error")
        self.assertFalse(thread["can_retry"])
        with self.assertRaises(UserError):
            conversation.ask_retry()

    def test_a_conversation_too_long_for_the_model(self):
        self.mock_api(message(content=[], stop="model_context_window_exceeded"))
        conversation = self.start()
        thread = conversation.ask_step()
        self.assertEqual(thread["state"], "error")
        self.assertIn("too long", thread["error"])
        self.assertFalse(thread["can_retry"], "asking the same thing again cannot fit either")
        self.assertEqual(self.env["odin.ai.run"].search([], limit=1).status, "context")

    # ------------------------------------------------------------------
    # The request: caching, last step, progress notes
    # ------------------------------------------------------------------

    def test_the_growing_conversation_is_cached(self):
        requests = self.mock_api(answer("Hello.", []))
        self.start().ask_step()
        self.assertEqual(requests[0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(requests[0]["system"][0]["cache_control"], {"type": "ephemeral"})

    def test_the_last_step_asks_for_an_answer(self):
        requests = self.mock_api(*[tool_call("pnl", PNL_SEPTEMBER, call_id=f"toolu_{i}") for i in range(MAX_STEPS)])
        conversation = self.start()
        for _step in range(MAX_STEPS):
            conversation.ask_step()
        last = requests[-1]["messages"][-1]["content"]
        self.assertEqual(last[0]["type"], "tool_result")
        self.assertEqual(last[-1], {"type": "text", "text": LAST_STEP_NOTE})
        earlier = requests[-2]["messages"][-1]["content"]
        self.assertEqual([block["type"] for block in earlier], ["tool_result"])
        self.assertEqual(requests[-1]["messages"][:-1], requests[-2]["messages"] + [requests[-1]["messages"][-2]],
                         "append-only: the reminder is added, nothing earlier changes")

    def test_progress_notes_between_tool_calls(self):
        requests = self.mock_api(
            message(content=[
                {"type": "thinking", "thinking": "", "signature": "reasoning"},
                {"type": "thinking", "thinking": "Revenue is up; checking who drove it.", "signature": "note"},
                {"type": "tool_use", "id": "toolu_1", "name": "pnl", "input": PNL_SEPTEMBER},
            ], stop="tool_use"),
            answer("Done.", []),
        )
        conversation = self.start()
        self.assertTrue(conversation.progress_updates)
        thread = conversation.ask_step()
        self.assertEqual(thread["turns"][0]["progress"],
                         [{"note": "Revenue is up; checking who drove it."}, "Reading the P&L for Sep 2026"])
        self.assertEqual(requests[0]["thinking"], {"type": "adaptive", "display": "updates"})
        self.assertIn("thinking-display-updates-2026-08-18", requests[0]["betas"])
        conversation.ask_step()
        self.assertEqual([block.get("signature") for block in requests[1]["messages"][1]["content"][:2]],
                         ["reasoning", "note"], "thinking blocks go back unchanged")

    def test_no_progress_notes_on_a_model_without_them(self):
        self.env["ir.config_parameter"].sudo().set_param("odin_account_ai.model", "claude-haiku-5-5")
        requests = self.mock_api(answer("Hello.", []))
        conversation = self.start()
        conversation.ask_step()
        self.assertFalse(conversation.progress_updates)
        self.assertNotIn("thinking", requests[0])

    # ------------------------------------------------------------------
    # What an answer says it read
    # ------------------------------------------------------------------

    def test_two_companies_selected(self):
        """The tools add up the companies selected on the page, as the P&L
        does: the model is told so, and the answer's "Based on" line names
        them."""
        other = self.setup_other_company(name="Second company")["company"]
        self.accountant.write({"company_ids": [Command.link(other.id)]})
        requests = self.mock_api(tool_call("pnl", PNL_SEPTEMBER), answer("Done.", []))
        Conversation = self.Conversation.with_context(allowed_company_ids=[self.env.company.id, other.id])
        conversation = Conversation.browse(Conversation.ask_start("Revenue?"))
        conversation.ask_step()
        thread = conversation.ask_step()
        self.assertIn(f"Companies selected, whose figures the tools add up: {self.env.company.name}, {other.name}.",
                      requests[0]["messages"][0]["content"])
        self.assertEqual(thread["turns"][0]["companies"], sorted([self.env.company.name, other.name]))
        self.assertEqual(thread["company"], self.env.company.name, "the conversation's own company")
        # One company selected: nothing to add.
        single = self.Conversation.with_context(allowed_company_ids=[self.env.company.id])
        self.assertNotIn("Companies selected", single.browse(single.ask_start("And?")).messages)

    def test_an_answer_says_what_it_read(self):
        self.mock_api(tool_call("pnl", dict(PNL_SEPTEMBER, comparison="previous_period")), answer("Done.", []))
        conversation = self.start()
        conversation.ask_step()
        thread = conversation.ask_step()
        scope = thread["turns"][0]["scope"]
        self.assertEqual([item["label"] for item in scope], ["Sep 2026 vs Aug 2026"])
        action = conversation.open_evidence(scope[0]["ref"])
        self.assertEqual(action["tag"], "odin_account_pnl.report")
        self.assertNotIn("focus", action["params"], "a period, not a row")
        self.assertEqual(action["params"]["options"]["comparison"]["mode"], "previous_period")
        self.assertEqual(thread["evidence"]["E1"], {"label": "Revenue · Sep 2026", "kind": "pnl"})
        self.assertEqual(thread["company"], self.env.company.name)

