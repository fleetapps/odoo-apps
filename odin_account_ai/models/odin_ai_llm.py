"""The one place Odoo talks to Claude.

Every AI feature calls :meth:`call` with its system prompt, messages, tools
and the JSON schema its answer must follow. This method:

* checks the company consented and has budget left this month;
* reuses a stored answer when Odoo retries a request (a serialization failure
  replays the whole HTTP request; the API must not be paid twice);
* calls the Messages API through the official SDK, with structured output
  (``output_config.format``), effort set explicitly, the stable system prompt
  cached, and server-side refusal fallbacks;
* logs the call in ``odin.ai.run`` in its own transaction, so the log and
  the budget survive a rollback of the request that made the call.

Anthropic references (Claude API):
  Messages and tool use   https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
  Structured outputs      https://platform.claude.com/docs/en/build-with-claude/structured-outputs
  Prompt caching          https://platform.claude.com/docs/en/build-with-claude/prompt-caching
  Refusals and fallbacks  https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback

Request time: Odoo stops an HTTP request after ``limit_time_real`` (120 s by
default, odoo/tools/config.py), so interactive calls use a 75 s timeout and no
SDK retry (the SDK retries timeouts, which would multiply the wait); calls
made by crons retry twice. Thinking is always adaptive on Claude Opus 5.5:
there is no ``thinking`` parameter, only ``effort``.
"""

import hashlib
import json
import logging
import os
import threading
import time

from odoo import SUPERUSER_ID, _, api, fields, models, modules
from odoo.exceptions import UserError
from odoo.tools import config

_logger = logging.getLogger(__name__)

INTERACTIVE = {"timeout": 75.0, "max_retries": 0, "max_tokens": 8000}
BACKGROUND = {"timeout": 240.0, "max_retries": 2, "max_tokens": 16000}
EFFORT = {"ask": "medium", "explain": "low", "review": "low", "anomaly": "medium", "draft": "medium", "test": "low"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# One SDK client per worker process and settings: created on first use (never
# at import, which happens before the prefork workers are spawned).
_CLIENTS = {}
_CLIENTS_LOCK = threading.Lock()


class OdinAiLlm(models.AbstractModel):
    _name = "odin.ai.llm"
    _description = "AI model access"

    # ------------------------------------------------------------------
    # Readiness
    # ------------------------------------------------------------------

    @api.model
    def _api_key(self):
        return os.environ.get("ANTHROPIC_API_KEY") or self.env["ir.config_parameter"].sudo().get_param(
            "odin_account_ai.api_key")

    @api.model
    def _model_for(self, feature):
        params = self.env["ir.config_parameter"].sudo()
        if feature == "review":
            return params.get_param("odin_account_ai.bulk_model") or params.get_param(
                "odin_account_ai.model") or "claude-opus-5-5"
        return params.get_param("odin_account_ai.model") or "claude-opus-5-5"

    @api.model
    def readiness(self):
        """What stops the AI from answering here, for the screens to say so
        before the user types anything. ``False`` when ready."""
        company = self.env.company
        if not company.odin_ai_consent:
            return _("An administrator has to allow sending accounting data to Anthropic "
                     "(Accounting > Settings > Accounting AI).")
        if not self._api_key():
            return _("No Anthropic API key is set (Accounting > Settings > Accounting AI).")
        if self._budget_left(company) <= 0:
            return _("This month's AI budget for %(company)s is used up.", company=company.name)
        return False

    @api.model
    def _budget_left(self, company):
        if not company.odin_ai_monthly_tokens:
            return float("inf")
        month_start = fields.Datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        groups = self.env["odin.ai.run"].sudo()._read_group(
            [("company_id", "=", company.id), ("create_date", ">=", month_start)],
            aggregates=["total_tokens:sum"])
        used = (groups[0][0] or 0) if groups else 0
        return company.odin_ai_monthly_tokens - used

    # ------------------------------------------------------------------
    # The call
    # ------------------------------------------------------------------

    @api.model
    def call(self, feature, *, system, messages, schema=None, tools=None, step_key=None, interactive=True):
        """Send one request and return the response as a dict (``content``,
        ``stop_reason``, ``model``, ``usage``).

        :param system: the stable system prompt (cached).
        :param schema: JSON schema the final answer must follow, or None.
        :param tools: tool definitions, already in a fixed order.
        :param step_key: a key unique to this request; a response already
            stored under it is returned instead of calling again.
        :raises UserError: with a message for the user when the AI cannot
            answer (not configured, budget, refusal, timeout, API error).
        """
        if step_key:
            previous = self.env["odin.ai.run"].sudo().search(
                [("step_key", "=", step_key), ("status", "=", "ok"), ("response", "!=", False)], limit=1)
            if previous:
                return json.loads(previous.response)
        blocker = self.readiness()
        if blocker:
            raise UserError(blocker)
        settings = INTERACTIVE if interactive else BACKGROUND
        model = self._model_for(feature)
        params = {
            "model": model,
            "max_tokens": settings["max_tokens"],
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            "output_config": {"effort": EFFORT.get(feature, "medium")},
        }
        if schema:
            params["output_config"]["format"] = {"type": "json_schema", "schema": schema}
        if tools:
            params["tools"] = tools
        if _has_server_fallback(model):
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"

        log = {
            "step_key": step_key,
            "user_id": self.env.uid,
            "company_id": self.env.company.id,
            "feature": feature,
            "model": model,
            "effort": params["output_config"]["effort"],
        }
        started = time.monotonic()
        try:
            response = self._create_message(params, settings)
        except UserError as error:
            log.update(status="error", error=str(error), duration_ms=_ms(started))
            self._log(log)
            raise
        data = _to_dict(response)
        usage = _usage(data)
        log.update(
            status={"refusal": "refusal", "max_tokens": "max_tokens"}.get(data.get("stop_reason"), "ok"),
            served_model=data.get("model"),
            request_id=getattr(response, "_request_id", None) or data.get("id"),
            duration_ms=_ms(started),
            response=json.dumps(data),
            **usage,
        )
        self._log(log)
        if data.get("stop_reason") == "refusal":
            raise UserError(_("The AI declined to answer this one. Try rephrasing the question."))
        if data.get("stop_reason") == "max_tokens":
            raise UserError(_("The answer was too long and got cut off. Ask for something narrower."))
        return data

    @api.model
    def _create_message(self, params, settings):
        """The network call. Tests replace this method; it refuses to run
        under the test runner so a forgotten mock never reaches the API."""
        if modules.module.current_test or config["test_enable"]:
            raise UserError(_("AI calls are disabled while tests run."))
        try:
            import anthropic  # noqa: PLC0415 (imported on use: the package is optional until the AI is used)
        except ImportError as error:
            raise UserError(_("The anthropic Python package is not installed on the server.")) from error
        client = self._client(anthropic, settings)
        try:
            return client.beta.messages.create(**params)
        except anthropic.AuthenticationError as error:
            raise UserError(_("Anthropic refused the API key. Check it in the settings.")) from error
        except anthropic.RateLimitError as error:
            raise UserError(_("The AI is busy right now (rate limit). Try again in a minute.")) from error
        except anthropic.APITimeoutError as error:
            raise UserError(_("The AI took too long to answer. Try again, or ask something narrower.")) from error
        except anthropic.APIConnectionError as error:
            raise UserError(_("Odoo could not reach Anthropic. Check the server's internet access.")) from error
        except anthropic.BadRequestError as error:
            _logger.warning("odin_account_ai: request rejected: %s", error)
            raise UserError(_("The AI request was rejected: %(error)s", error=error.message)) from error
        except anthropic.APIStatusError as error:
            raise UserError(_("Anthropic answered with an error (%(status)s). Try again later.",
                              status=error.status_code)) from error

    @api.model
    def _client(self, anthropic, settings):
        key = self._api_key()
        cache_key = (hashlib.sha256(key.encode()).hexdigest(), settings["timeout"], settings["max_retries"])
        with _CLIENTS_LOCK:
            client = _CLIENTS.get(cache_key)
            if client is None:
                client = anthropic.Anthropic(
                    api_key=key, timeout=settings["timeout"], max_retries=settings["max_retries"])
                _CLIENTS[cache_key] = client
        return client

    @api.model
    def _log(self, values):
        """Write the run in its own transaction (not under the test runner,
        where a second connection could not see the test's data)."""
        values = {key: value for key, value in values.items() if value is not None}
        if modules.module.current_test or config["test_enable"]:
            self.env["odin.ai.run"].sudo().create(values)
            return
        try:
            with self.env.registry.cursor() as cr:
                api.Environment(cr, SUPERUSER_ID, {})["odin.ai.run"].create(values)
        except Exception:  # noqa: BLE001 (never let the log break the answer)
            _logger.exception("odin_account_ai: could not log an AI run")

    @api.model
    def _test_connection(self):
        try:
            import anthropic  # noqa: PLC0415
        except ImportError as error:
            raise UserError(_("The anthropic Python package is not installed on the server.")) from error
        if not self._api_key():
            raise UserError(_("Set the API key first."))
        model = self._model_for("ask")
        try:
            info = self._client(anthropic, INTERACTIVE).models.retrieve(model)
        except anthropic.AuthenticationError as error:
            raise UserError(_("Anthropic refused the API key.")) from error
        except anthropic.NotFoundError as error:
            raise UserError(_("The model %(model)s is not available to this key.", model=model)) from error
        except anthropic.APIError as error:
            raise UserError(_("Could not reach Anthropic: %(error)s", error=error)) from error
        return _("Connected. %(model)s is available.", model=getattr(info, "display_name", model))


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _has_server_fallback(model):
    """Server-side fallbacks exist for the Opus, Sonnet and Fable families on
    the Claude API, not for Haiku (a list of fallbacks is a 400 there)."""
    return model.startswith("claude-") and "haiku" not in model


def _ms(started):
    return int((time.monotonic() - started) * 1000)


def _to_dict(response):
    if isinstance(response, dict):
        return response
    return response.to_dict()


def _usage(data):
    """Tokens of every attempt: with a fallback, ``usage.iterations`` holds
    one entry per model that ran; top-level usage covers only the last."""
    usage = data.get("usage") or {}
    attempts = usage.get("iterations") or [usage]
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    for attempt in attempts:
        totals["input_tokens"] += attempt.get("input_tokens") or 0
        totals["output_tokens"] += attempt.get("output_tokens") or 0
        totals["cache_read_tokens"] += attempt.get("cache_read_input_tokens") or 0
        totals["cache_write_tokens"] += attempt.get("cache_creation_input_tokens") or 0
    return totals


def echo_content(content):
    """The assistant content to send back on the next turn. After a fallback
    in the middle of an answer, the blocks the declined model wrote before the
    switch (thinking, tool calls) are not echoed; text and everything after
    the last ``fallback`` block are."""
    last = max((index for index, block in enumerate(content) if block.get("type") == "fallback"), default=None)
    if last is None:
        return content
    dropped = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
    return [
        block for index, block in enumerate(content)
        if index > last or (block.get("type") not in dropped and block.get("type") != "fallback")
    ]


def final_json(data):
    """The structured answer: the text block of a response made with
    ``output_config.format``."""
    for block in data.get("content") or []:
        if block.get("type") == "text" and block.get("text", "").strip():
            try:
                return json.loads(block["text"])
            except ValueError:
                continue
    raise UserError(_("The AI's answer could not be read. Try again."))
