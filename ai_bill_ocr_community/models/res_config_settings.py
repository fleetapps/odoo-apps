# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ai_bill_provider = fields.Selection(
        [("anthropic", "Anthropic (Claude)"), ("openai", "OpenAI"),
         ("gemini", "Google Gemini")],
        config_parameter="ai_bill_ocr_community.provider", default="anthropic")
    ai_bill_api_key = fields.Char(
        config_parameter="ai_bill_ocr_community.api_key", string="LLM API Key")
    ai_bill_model = fields.Char(
        config_parameter="ai_bill_ocr_community.model",
        string="Model override (optional)")
    ai_bill_review_threshold = fields.Char(
        config_parameter="ai_bill_ocr_community.review_threshold",
        default="0.85", string="Auto-ready confidence threshold")
