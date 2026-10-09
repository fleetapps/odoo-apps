"""Ask the Ledger: a conversation with the model, one request at a time.

The browser drives the loop: ``ask_start`` stores the question, then the page
calls ``ask_step`` until the answer is in. Each step makes one call to the
model and runs the tools it asked for, so every HTTP request stays well under
Odoo's ``limit_time_real`` and the page can show what is happening
("Reading the P&L for Sep 2026", "Splitting by partner").

The conversation is append-only: the system prompt and the tool list are
stored when it starts and every assistant turn is kept exactly as returned,
thinking blocks included. Claude keeps its reasoning valid only on an
unchanged history, so nothing here is ever rebuilt or edited.
https://platform.claude.com/docs/en/build-with-claude/extended-thinking
"""

import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from . import odin_ai_prompts as prompts
from .odin_ai_llm import echo_content, final_json
from .odin_ai_tools import dumps, tool_definitions

MAX_STEPS = 8
MAX_TOOLS_PER_STEP = 5
MAX_TURNS = 10
MAX_ACCOUNTS_IN_PROMPT = 400


class OdinAiConversation(models.Model):
    _name = "odin.ai.conversation"
    _description = "AI conversation"
    _order = "id desc"

    name = fields.Char(required=True)
    user_id = fields.Many2one("res.users", required=True, default=lambda self: self.env.user, index=True,
                              ondelete="cascade")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    feature = fields.Selection([("ask", "Ask"), ("explain", "Explain")], required=True, default="ask")
    state = fields.Selection(
        [("running", "Thinking"), ("done", "Answered"), ("stopped", "Stopped"),
         ("error", "Failed")], default="running", required=True)
    stop_requested = fields.Boolean(
        readonly=True, copy=False,
        help="Set by Stop. A step holds the row while it waits on the API, so the "
             "state cannot simply be written from outside: the next step reads this "
             "and ends the turn.")
    step_no = fields.Integer(default=0, help="Calls made in the whole conversation.")
    turn_steps = fields.Integer(default=0, help="Calls made for the current question.")
    system_snapshot = fields.Text(readonly=True)
    messages = fields.Text(default="[]", help="The exchange as sent to the API, append-only.")
    evidence = fields.Text(default="{}", help="Handle -> what it stands for.")
    turns = fields.Text(default="[]", help="Each question with its progress and structured answer.")
    error = fields.Text()
    feedback = fields.Selection([("up", "Helpful"), ("down", "Not helpful")])

    # ------------------------------------------------------------------
    # Entry points (called by the Ask page)
    # ------------------------------------------------------------------

    @api.model
    def ask_start(self, question, context=None):
        """Store the question and return the conversation to step through."""
        self._check_reader()
        question = (question or "").strip()
        if not question:
            raise UserError(_("Ask something first."))
        blocker = self.env["odin.ai.llm"].readiness()
        if blocker:
            raise UserError(blocker)
        today = fields.Date.context_today(self)
        lines = [
            "Today is %s." % fields.Date.to_string(today),
            "Question: %s" % question[:2000],
        ]
        if context:
            lines.append("Context from the page (data): %s" % dumps(context)[:4000])
        conversation = self.create({
            "name": question[:120],
            "system_snapshot": self._system_prompt(),
            "messages": json.dumps([{"role": "user", "content": "\n".join(lines)}]),
            "turns": json.dumps([{"question": question[:2000], "progress": [], "answer": None}]),
        })
        return conversation.id

    def ask_followup(self, question):
        """A further question in the same conversation: the model keeps what
        it already read (and its reasoning), so "and the month before?"
        works."""
        self.ensure_one()
        self._check_owner()
        question = (question or "").strip()
        if not question:
            raise UserError(_("Ask something first."))
        if self.state != "done":
            raise UserError(_("Wait for the answer, or start a new conversation."))
        turns = json.loads(self.turns or "[]")
        if len(turns) >= MAX_TURNS:
            raise UserError(_("This conversation is long enough: start a new one for this question."))
        blocker = self.env["odin.ai.llm"].readiness()
        if blocker:
            raise UserError(blocker)
        messages = json.loads(self.messages)
        messages.append({"role": "user", "content": "Question: %s" % question[:2000]})
        turns.append({"question": question[:2000], "progress": [], "answer": None})
        self.write({
            "messages": json.dumps(messages),
            "turns": json.dumps(turns),
            "state": "running",
            "turn_steps": 0,
            "error": False,
            "stop_requested": False,
        })
        return self._thread()

    def ask_retry(self):
        """Run the current question again after the API failed (the
        conversation still ends with the question, so nothing is lost)."""
        self.ensure_one()
        self._check_owner()
        messages = json.loads(self.messages)
        if self.state not in ("error", "stopped") or not messages or messages[-1]["role"] != "user":
            raise UserError(_("This question cannot be retried: ask it again in a new conversation."))
        blocker = self.env["odin.ai.llm"].readiness()
        if blocker:
            raise UserError(blocker)
        self.write({"state": "running", "turn_steps": 0, "error": False,
                    "stop_requested": False})
        return self._thread()

    def ask_stop(self):
        """Give up on the question being worked on.

        A step can hold the row for as long as one API call takes, so writing
        the state from here would be overwritten the moment that step commits.
        Instead: if the row can be locked, nothing is in flight and the turn
        ends immediately; if it cannot, a step is mid-call, so the flag is set
        and the next step reads it. Should that step answer the question first,
        the flag is moot and the next turn clears it.
        """
        self.ensure_one()
        self._check_owner()
        if self.state != "running":
            return self._thread()
        if self.try_lock_for_update():
            self.write({"state": "stopped", "stop_requested": False,
                        "error": _("Stopped before an answer was ready.")})
        else:
            self.write({"stop_requested": True})
        return self._thread()

    def ask_step(self):
        """One call to the model and the tools it asked for. Returns the
        state for the page: progress so far and, when done, the answer."""
        self.ensure_one()
        self._check_owner()
        if self.state != "running":
            return self._thread()
        locked = self.try_lock_for_update()
        if not locked:
            # A step for this conversation is already running (double click,
            # or Odoo retrying a request): let the page poll again.
            return dict(self._thread(), busy=True)
        if self.stop_requested:
            self.write({"state": "stopped", "stop_requested": False,
                        "error": _("Stopped before an answer was ready.")})
            return self._thread()
        if self.turn_steps >= MAX_STEPS:
            self.write({"state": "error", "error": _(
                "This question needed too many steps. Try asking something narrower.")})
            return self._thread()
        messages = json.loads(self.messages)
        try:
            data = self.env["odin.ai.llm"].call(
                "ask",
                system=self.system_snapshot,
                messages=messages,
                tools=tool_definitions(),
                schema=prompts.ASK_ANSWER_SCHEMA,
                step_key=f"ask-{self.id}-{self.step_no}",
            )
        except UserError as error:
            self.write({"state": "error", "error": str(error)})
            return self._thread()
        # Only what is echoed counts: tool calls a declined model made before
        # a fallback are neither sent back nor run.
        content = echo_content(data.get("content") or [])
        messages.append({"role": "assistant", "content": content})
        values = {"step_no": self.step_no + 1, "turn_steps": self.turn_steps + 1}
        turns = json.loads(self.turns or "[]")
        if data.get("stop_reason") == "tool_use":
            progress = turns[-1]["progress"]
            results = []
            for index, block in enumerate(b for b in content if b.get("type") == "tool_use"):
                if index >= MAX_TOOLS_PER_STEP:
                    result, label = {"error": "Too many tools in one step; ask for fewer."}, None
                else:
                    result, label = self.env["odin.ai.tools"].run(self, block.get("name"), block.get("input"))
                if label:
                    progress.append(label)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": dumps(result),
                    **({"is_error": True} if "error" in result else {}),
                })
            messages.append({"role": "user", "content": results})
            values.update(messages=json.dumps(messages), turns=json.dumps(turns))
        else:
            try:
                answer = self._clean_answer(final_json(data))
            except UserError as error:
                values.update(messages=json.dumps(messages), state="error", error=str(error))
                self.write(values)
                return self._thread()
            turns[-1]["answer"] = answer
            values.update(messages=json.dumps(messages), turns=json.dumps(turns), state="done")
        self.write(values)
        return self._thread()

    def open_evidence(self, handle):
        """What an evidence chip opens: the P&L at that row, or the document."""
        self.ensure_one()
        self._check_owner()
        item = json.loads(self.evidence or "{}").get(handle)
        if not item:
            raise UserError(_("This reference is not in the conversation."))
        if item["kind"] == "pnl":
            options = dict(item["options"])
            unfolded = set(options.get("unfolded") or [])
            parts = item["row_key"].split("/")
            unfolded.update("/".join(parts[:index]) for index in range(1, len(parts)))
            options["unfolded"] = sorted(unfolded)
            return {
                "type": "ir.actions.client",
                "tag": "odin_account_pnl.report",
                "name": _("Profit & Loss"),
                "params": {"options": options, "focus": item["row_key"]},
            }
        record = self.env[item["model"]].browse(item["res_id"]).exists()
        if not record:
            raise UserError(_("This document no longer exists."))
        record.check_access("read")
        return {"type": "ir.actions.act_window", "res_model": item["model"], "res_id": record.id,
                "views": [(False, "form")], "target": "current"}

    def set_feedback(self, value):
        self.ensure_one()
        if self.user_id == self.env.user and value in ("up", "down", False):
            self.feedback = value
        return True

    @api.model
    def list_recent(self):
        conversations = self.search([("user_id", "=", self.env.uid), ("feature", "=", "ask")], limit=30)
        return [{"id": c.id, "name": c.name, "state": c.state, "date": fields.Datetime.to_string(c.create_date)}
                for c in conversations]

    @api.model
    def ask_bootstrap(self):
        """What the Ask page needs before the first question."""
        self._check_reader()
        return {
            "ready": self.env["odin.ai.llm"].readiness() or False,
            "recent": self.list_recent(),
            "company": self.env.company.name,
            "starters": [
                {"icon": "fa-line-chart", "title": _("Revenue"),
                 "question": _("How did revenue last month compare with the month before, and who drove the change?")},
                {"icon": "fa-pie-chart", "title": _("Expenses"),
                 "question": _("What were our ten largest expenses this quarter, by account?")},
                {"icon": "fa-percent", "title": _("Margins"),
                 "question": _("How has our gross margin moved over the last six months?")},
                {"icon": "fa-users", "title": _("Customers"),
                 "question": _("Which customers brought in the most revenue this fiscal year?")},
                {"icon": "fa-truck", "title": _("Suppliers"),
                 "question": _("Which suppliers did we spend the most with in the last three months?")},
                {"icon": "fa-search", "title": _("Find"),
                 "question": _("Show the journal items above 100,000 booked to expenses last month.")},
            ],
        }

    def get_thread(self):
        self.ensure_one()
        self._check_owner()
        return self._thread()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _thread(self):
        evidence = json.loads(self.evidence or "{}")
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "step": self.turn_steps,
            "max_steps": MAX_STEPS,
            "turns": json.loads(self.turns or "[]"),
            "error": self.error or "",
            "evidence": {handle: item["label"] for handle, item in evidence.items()},
            "feedback": self.feedback or False,
        }

    def _add_evidence(self, item):
        """Register what a tool row stands for and return its handle."""
        evidence = json.loads(self.evidence or "{}")
        handle = f"E{len(evidence) + 1}"
        evidence[handle] = item
        self.evidence = json.dumps(evidence, default=str)
        return handle

    def _clean_answer(self, answer):
        """Keep only the evidence the tools produced: a handle the model made
        up is dropped, not shown as a link."""
        known = set(json.loads(self.evidence or "{}"))
        blocks = []
        for block in answer.get("blocks") or []:
            block["evidence"] = [handle for handle in block.get("evidence") or [] if handle in known]
            blocks.append(block)
        return {"blocks": blocks, "follow_ups": (answer.get("follow_ups") or [])[:3]}

    def _check_owner(self):
        self._check_reader()
        if self.user_id != self.env.user:
            raise AccessError(_("This is someone else's conversation."))

    @api.model
    def _check_reader(self):
        if not self.env.user.has_group("odin_account_ai.group_ai_user") or \
                not self.env.user.has_group("account.group_account_readonly"):
            raise AccessError(_("Ask the Ledger is for users who can see accounting."))

    @api.model
    def _system_prompt(self):
        """Stable per company, so the API serves it from cache."""
        company = self.env.company
        report = self.env["odin.pnl.report"]
        layout = self.env["odin.pnl.layout"].browse(report._normalize_options({})["layout_id"])
        lines = ", ".join(f"{line.code}: {line.name}" for line in layout.line_ids.sorted("sequence"))
        accounts = self.env["account.account"].search(
            [("account_type", "in", (
                "income", "income_other", "expense", "expense_other", "expense_depreciation",
                "expense_direct_cost")), ("company_ids", "in", company.ids)],
            limit=MAX_ACCOUNTS_IN_PROMPT)
        account_lines = "\n".join(
            f"  {account.code} {account.name} [{account.account_type}]"
            for account in accounts.sorted(lambda acc: acc.code or ""))
        return prompts.ASK_RULES.format(
            company=company.name,
            currency=company.currency_id.name,
            fy_day=company.fiscalyear_last_day,
            fy_month=company.fiscalyear_last_month,
            lines=lines,
            accounts=account_lines,
        )
