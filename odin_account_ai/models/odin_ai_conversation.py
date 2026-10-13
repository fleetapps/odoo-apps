"""Ask the Ledger: a conversation with the model, one request at a time.

The browser drives the loop: ``ask_start`` stores the question, then the page
calls ``ask_step`` until the answer is in. Each step makes one call to the
model and runs the tools it asked for, so every HTTP request stays well under
Odoo's ``limit_time_real`` and the page can show what is happening
("Reading the P&L for Sep 2026", "Splitting by partner").

The conversation is append-only: the system prompt, the tool list and the
thinking settings are stored when it starts, and every assistant turn is kept
exactly as returned, thinking blocks included. Claude keeps its reasoning
valid only on an unchanged history (the system prompt, the tools and every
earlier message), so nothing here is ever rebuilt or edited.
https://platform.claude.com/docs/en/build-with-claude/preserved-thinking

Users read and delete their own conversations but never write them directly:
every change goes through the methods below, after the owner check. A direct
write could rewrite an answer, or hand a conversation to someone else.
"""

import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import formatLang

from . import odin_ai_prompts as prompts
from .odin_ai_llm import ContextWindowExceeded, echo_content, final_json, supports_progress_updates
from .odin_ai_tools import dumps, legacy_tool_definitions, tool_definitions

MAX_STEPS = 8
MAX_TOOLS_PER_STEP = 5
MAX_TURNS = 10
MAX_ACCOUNTS_IN_PROMPT = 400
NOTE_LIMIT = 400
BUDGET_WARNING_PCT = 80
# Appended after the tool results of the step before the last one, and never
# removed: a per-turn reminder must stay in the history, or the cached prefix
# and the model's earlier reasoning would be invalidated ("Per-turn reminders
# in a tool loop", prompt caching).
LAST_STEP_NOTE = (
    "This is the last step this question may take: answer now from what you have read, "
    "and say what is missing.")


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
    step_no = fields.Integer(default=0, help="Calls made in the whole conversation.")
    turn_steps = fields.Integer(default=0, help="Calls made for the current question.")
    system_snapshot = fields.Text(readonly=True)
    tools_snapshot = fields.Text(
        readonly=True,
        help="The tool definitions sent with every request of this conversation, stored when it "
             "starts: a different tool list would invalidate the model's earlier reasoning. Empty "
             "for conversations started before it was stored, which get the tools of that time.")
    progress_updates = fields.Boolean(
        readonly=True,
        help="Whether the model's notes between tool calls are asked for, decided when the "
             "conversation starts: changing the thinking settings invalidates the cached messages.")
    messages = fields.Text(default="[]", help="The exchange as sent to the API, append-only.")
    evidence = fields.Text(default="{}", help="Handle -> what it stands for.")
    turns = fields.Text(default="[]", help="Each question with its progress, scope, answer and feedback.")
    error = fields.Text()
    feedback = fields.Selection(
        [("up", "Helpful"), ("down", "Not helpful")],
        help="The latest rating given to an answer of this conversation.")

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
        llm = self.env["odin.ai.llm"]
        blocker = llm.readiness()
        if blocker:
            raise UserError(blocker)
        today = fields.Date.context_today(self)
        lines = [
            "Today is %s." % fields.Date.to_string(today),
            *self._companies_note(),
            "Question: %s" % question[:2000],
        ]
        if context:
            lines.append("Context from the page (data): %s" % dumps(context)[:4000])
        conversation = self.sudo().create({
            "name": question[:120],
            "user_id": self.env.uid,
            "company_id": self.env.company.id,
            "system_snapshot": self._system_prompt(),
            "tools_snapshot": json.dumps(tool_definitions()),
            "progress_updates": supports_progress_updates(llm._model_for("ask")),
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
        messages.append({"role": "user", "content": "\n".join([*self._companies_note(), "Question: %s" % question[:2000]])})
        turns.append({"question": question[:2000], "progress": [], "answer": None})
        self.sudo().write({
            "messages": json.dumps(messages),
            "turns": json.dumps(turns),
            "state": "running",
            "turn_steps": 0,
            "error": False,
        })
        return self._thread()

    def ask_retry(self):
        """Run the current question again after the API failed or the user
        stopped it (the conversation still ends with the question or the
        last tool results, so nothing is lost)."""
        self.ensure_one()
        self._check_owner()
        if not self._can_retry():
            raise UserError(_("This question cannot be retried: ask it again in a new conversation."))
        blocker = self.env["odin.ai.llm"].readiness()
        if blocker:
            raise UserError(blocker)
        self.sudo().write({"state": "running", "turn_steps": 0, "error": False})
        return self._thread()

    def ask_stop(self):
        """End the question being worked on.

        The page sends this only once the step in flight has returned, so the
        row is free and the turn ends at once. Should a step still hold it
        (another tab, or Odoo replaying a request), nothing is written: an
        UPDATE of a row another transaction holds waits until that step's API
        call is over and then fails on commit ("could not serialize access due
        to concurrent update" under REPEATABLE READ). The page asks again a
        moment later instead.
        https://www.postgresql.org/docs/current/transaction-iso.html
        """
        self.ensure_one()
        self._check_owner()
        if self.state != "running":
            return self._thread()
        if not self.try_lock_for_update():
            return dict(self._thread(), busy=True)
        self.sudo().write({"state": "stopped", "error": _("Stopped before an answer was ready.")})
        return self._thread()

    def ask_step(self):
        """One call to the model and the tools it asked for. Returns the
        state for the page: progress so far and, when done, the answer."""
        self.ensure_one()
        self._check_owner()
        if self.state != "running":
            return self._thread()
        if not self.try_lock_for_update():
            # A step for this conversation is already running (double click,
            # or Odoo retrying a request): let the page poll again.
            return dict(self._thread(), busy=True)
        if self.turn_steps >= MAX_STEPS:
            self.sudo().write({"state": "error", "error": _(
                "This question needed too many steps. Try asking something narrower.")})
            return self._thread()
        messages = json.loads(self.messages)
        tools = json.loads(self.tools_snapshot) if self.tools_snapshot else legacy_tool_definitions()
        try:
            data = self.env["odin.ai.llm"].call(
                "ask",
                system=self.system_snapshot,
                messages=messages,
                tools=tools,
                schema=prompts.ASK_ANSWER_SCHEMA,
                step_key=f"ask-{self.id}-{self.step_no}",
                cache_conversation=True,
                progress_updates=self.progress_updates,
            )
        except UserError as error:
            values = {"state": "error", "error": str(error)}
            if isinstance(error, ContextWindowExceeded):
                turns = json.loads(self.turns or "[]")
                turns[-1]["final"] = True
                values["turns"] = json.dumps(turns)
            self.sudo().write(values)
            return self._thread()
        # Only what is echoed counts: tool calls a declined model made before
        # a fallback are neither sent back nor run.
        content = echo_content(data.get("content") or [])
        messages.append({"role": "assistant", "content": content})
        values = {"step_no": self.step_no + 1, "turn_steps": self.turn_steps + 1}
        turns = json.loads(self.turns or "[]")
        turn = turns[-1]
        progress = turn["progress"]
        wants_tools = data.get("stop_reason") == "tool_use"
        if wants_tools:
            # The tools read the companies selected on the page, as the P&L
            # does: the answer's "Based on" line names them.
            turn["companies"] = sorted(set(turn.get("companies") or []) | set(self.env.companies.mapped("name")))
        results = []
        for block in content:
            if block.get("type") == "thinking":
                # Under display "updates" a thinking block with text is a
                # progress update; reasoning blocks come back empty.
                # https://platform.claude.com/docs/en/build-with-claude/thinking#progress-updates
                note = (block.get("thinking") or "").strip()
                if note and self.progress_updates:
                    progress.append({"note": note[:NOTE_LIMIT]})
            elif block.get("type") == "tool_use" and wants_tools:
                if len(results) >= MAX_TOOLS_PER_STEP:
                    result, label = {"error": "Too many tools in one step; ask for fewer."}, None
                else:
                    result, label = self.env["odin.ai.tools"].run(self, block.get("name"), block.get("input"))
                scope = result.pop("_scope", None)
                if scope:
                    self._add_scope(turn, scope)
                if label:
                    progress.append(label)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": dumps(result),
                    **({"is_error": True} if "error" in result else {}),
                })
        if wants_tools:
            if values["turn_steps"] == MAX_STEPS - 1:
                results.append({"type": "text", "text": LAST_STEP_NOTE})
            messages.append({"role": "user", "content": results})
            values.update(messages=json.dumps(messages), turns=json.dumps(turns))
        else:
            try:
                answer = self._clean_answer(final_json(data))
            except UserError as error:
                values.update(messages=json.dumps(messages), turns=json.dumps(turns), state="error",
                              error=str(error))
                self.sudo().write(values)
                return self._thread()
            turn["answer"] = answer
            values.update(messages=json.dumps(messages), turns=json.dumps(turns), state="done")
        self.sudo().write(values)
        return self._thread()

    def open_evidence(self, handle):
        """What an evidence chip opens: the P&L at that row (or on that period
        and those filters), a list of journal items, or the document."""
        self.ensure_one()
        self._check_owner()
        item = json.loads(self.evidence or "{}").get(handle)
        if not item:
            raise UserError(_("This reference is not in the conversation."))
        if item["kind"] == "pnl":
            options = dict(item["options"])
            row_key = item.get("row_key") or ""
            params = {"options": options}
            if row_key:
                unfolded = set(options.get("unfolded") or [])
                parts = row_key.split("/")
                unfolded.update("/".join(parts[:index]) for index in range(1, len(parts)))
                options["unfolded"] = sorted(unfolded)
                params["focus"] = row_key
            return {
                "type": "ir.actions.client",
                "tag": "odin_account_pnl.report",
                "name": _("Profit & Loss"),
                "params": params,
            }
        if item["kind"] == "list":
            self.env[item["model"]].check_access("read")
            return {
                "type": "ir.actions.act_window",
                "name": item.get("title") or item["label"],
                "res_model": item["model"],
                "domain": item["domain"],
                "views": [(False, "list"), (False, "form")],
                "context": {"create": False},
                "target": "current",
            }
        record = self.env[item["model"]].browse(item["res_id"]).exists()
        if not record:
            raise UserError(_("This document no longer exists."))
        record.check_access("read")
        return {"type": "ir.actions.act_window", "res_model": item["model"], "res_id": record.id,
                "views": [(False, "form")], "target": "current"}

    def set_feedback(self, value, turn_index=None):
        """Rate one answer (the last one when no index is given)."""
        self.ensure_one()
        self._check_owner()
        if value not in ("up", "down", False):
            raise UserError(_("Unknown rating."))
        turns = json.loads(self.turns or "[]")
        index = len(turns) - 1 if turn_index is None else int(turn_index)
        if not 0 <= index < len(turns) or not turns[index].get("answer"):
            raise UserError(_("There is no answer to rate here."))
        if not self.try_lock_for_update():
            raise UserError(_("The next answer is still being written: rate this one in a moment."))
        turns[index]["feedback"] = value
        latest = next((turn.get("feedback") for turn in reversed(turns) if turn.get("feedback")), False)
        self.sudo().write({"turns": json.dumps(turns), "feedback": latest})
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
        llm = self.env["odin.ai.llm"]
        company = self.env.company
        code, message = llm._readiness()
        budget = llm._budget_status(company)
        warning = False
        if not code and budget and budget["pct"] >= BUDGET_WARNING_PCT:
            warning = _(
                "%(pct)s%% of this month's AI budget for %(company)s is used (%(used)s of %(limit)s tokens).",
                pct=int(budget["pct"]), company=company.name,
                used=formatLang(self.env, budget["used"], digits=0),
                limit=formatLang(self.env, budget["limit"], digits=0))
        return {
            "ready": message or False,
            "setup": code or False,
            "can_configure": self.env.user.has_group("base.group_system"),
            "budget_warning": warning,
            "recent": self.list_recent(),
            "company": company.name,
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
        turns = json.loads(self.turns or "[]")
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "step": self.turn_steps,
            "max_steps": MAX_STEPS,
            "turns": turns,
            "error": self.error or "",
            "can_retry": self._can_retry(turns=turns),
            "evidence": {
                handle: {"label": item.get("label") or handle, "kind": item.get("kind")}
                for handle, item in evidence.items()
            },
            "company": self.company_id.name,
            "currency": self.company_id.currency_id.name,
        }

    @api.model
    def _companies_note(self):
        """For the model, when several companies are selected: the tools add
        up their figures, as the P&L does."""
        companies = self.env.companies
        if len(companies) < 2:
            return []
        return ["Companies selected, whose figures the tools add up: %s." % ", ".join(companies.mapped("name"))]

    def _can_retry(self, turns=None):
        """Whether the current question can be asked again in place: only
        after a failure or a stop, while the exchange still ends with the
        question or tool results (an answer the page could not read leaves
        the model's turn last), and not when the conversation is too long."""
        if self.state not in ("error", "stopped"):
            return False
        messages = json.loads(self.messages or "[]")
        if not messages or messages[-1]["role"] != "user":
            return False
        turns = json.loads(self.turns or "[]") if turns is None else turns
        return not (turns and turns[-1].get("final"))

    def _add_evidence(self, item):
        """Register what a tool row stands for and return its handle."""
        evidence = json.loads(self.evidence or "{}")
        handle = f"E{len(evidence) + 1}"
        evidence[handle] = item
        self.sudo().evidence = json.dumps(evidence, default=str)
        return handle

    def _add_scope(self, turn, scope):
        """Record what a tool read (period, comparison, filters) for the
        answer's "Based on" line, once per distinct scope, with a handle
        opening the P&L on it when the tool gave one."""
        scopes = turn.setdefault("scope", [])
        if scope["key"] in {item["key"] for item in scopes}:
            return
        item = {"key": scope["key"], "label": scope["label"]}
        if scope.get("evidence"):
            item["ref"] = self._add_evidence(scope["evidence"])
        scopes.append(item)

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
