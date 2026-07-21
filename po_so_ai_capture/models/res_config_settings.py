# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    po_capture_provider = fields.Selection(
        [("anthropic", "Anthropic (Claude)"), ("openai", "OpenAI"),
         ("gemini", "Google Gemini")],
        config_parameter="po_so_ai_capture.provider", default="anthropic")
    po_capture_api_key = fields.Char(
        config_parameter="po_so_ai_capture.api_key", string="LLM API Key")
    po_capture_model = fields.Char(
        config_parameter="po_so_ai_capture.model", string="Model override")
    po_capture_review_threshold = fields.Char(
        config_parameter="po_so_ai_capture.review_threshold", default="0.85",
        string="Auto-ready confidence threshold")
