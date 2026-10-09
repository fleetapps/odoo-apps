"""Fixtures for the AI tests. The Claude API is never called: tests replace
``odin.ai.llm._create_message`` (the one network call) with canned
responses shaped like the Messages API's, so everything around it (request
building, logging, budget, validation of the answers) runs for real."""

import copy
import json
from unittest.mock import patch

from odoo.addons.odin_account_pnl.tests.common import PnlCase


def message(answer=None, *, content=None, stop="end_turn", usage=None, model="claude-opus-5-5"):
    """A Messages API response as a dict; ``answer`` becomes the JSON text
    block of a structured output."""
    if content is None:
        content = [{"type": "text", "text": json.dumps(answer)}]
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": usage or {"input_tokens": 100, "output_tokens": 20},
    }


def tool_call(name, arguments, call_id="toolu_1"):
    return message(content=[{"type": "tool_use", "id": call_id, "name": name, "input": arguments}], stop="tool_use")


class AiCase(PnlCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.write({"odin_ai_consent": True, "odin_ai_monthly_tokens": 3000000})
        cls.env["ir.config_parameter"].sudo().set_param("odin_account_ai.api_key", "sk-ant-test")
        cls.env["ir.config_parameter"].sudo().set_param("odin_account_ai.model", "claude-opus-5-5")

    def mock_api(self, *responses):
        """Answer the next calls with ``responses`` (dicts, exceptions, or
        functions of the request). Returns the list of requests sent."""
        requests = []
        queue = list(responses)

        def create_message(params, settings):
            # A copy: the caller goes on appending to its message list.
            requests.append(copy.deepcopy(params))
            if not queue:
                raise AssertionError("Unexpected call to the AI")
            response = queue.pop(0)
            if isinstance(response, Exception):
                raise response
            return response(params) if callable(response) else response

        patcher = patch.object(self.registry["odin.ai.llm"], "_create_message", side_effect=create_message)
        patcher.start()
        self.addCleanup(patcher.stop)
        return requests
