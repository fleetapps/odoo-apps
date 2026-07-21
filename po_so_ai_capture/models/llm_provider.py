# -*- coding: utf-8 -*-
"""BYO-key LLM extraction (no per-page resale fees - the anti-competitor pitch).
Providers: Anthropic / OpenAI / Gemini. All accept PDFs or images.
VERIFY-ON-BUILD: endpoints & model names against current provider docs:
  https://docs.claude.com | https://platform.openai.com/docs | https://ai.google.dev/api
"""
import json
import requests
from odoo import api, models
from odoo.exceptions import UserError

PROMPT = (
    "You are an order-entry clerk. Extract this CUSTOMER PURCHASE ORDER into "
    "strict JSON only, no prose, no markdown fences. Schema: "
    "{\"fields\": {customer_name, po_number, po_date (YYYY-MM-DD), "
    "requested_delivery (YYYY-MM-DD or null), currency (ISO), notes "
    "- each as {value, confidence 0..1}}, "
    "\"lines\": [{customer_sku, description, quantity, price_unit, "
    "confidence}]}. customer_sku is the buyer's part number as printed; use "
    "null when absent. Include EVERY line item. If a value is unreadable use "
    "null and a low confidence - never guess silently."
)


class PoCaptureLLM(models.AbstractModel):
    _name = "po.capture.llm"
    _description = "PO Extraction LLM"

    def _params(self):
        icp = self.env["ir.config_parameter"].sudo()
        return (icp.get_param("po_so_ai_capture.provider", "anthropic"),
                icp.get_param("po_so_ai_capture.api_key", ""),
                icp.get_param("po_so_ai_capture.model", ""))

    @api.model
    def extract_po(self, attachment):
        provider, key, model = self._params()
        if not key:
            raise UserError("Configure your LLM API key in Settings > PO Capture.")
        b64 = attachment.datas.decode()
        text = getattr(self, f"_call_{provider}")(key, model, b64,
                                                  attachment.mimetype)
        cleaned = text.strip().removeprefix("```json").removesuffix("```").strip()
        return json.loads(cleaned)

    def _call_anthropic(self, key, model, b64, mimetype):
        block_type = "document" if mimetype == "application/pdf" else "image"
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": model or "claude-sonnet-4-6", "max_tokens": 8000,
                  "messages": [{"role": "user", "content": [
                      {"type": block_type, "source": {"type": "base64",
                       "media_type": mimetype, "data": b64}},
                      {"type": "text", "text": PROMPT}]}]},
            timeout=180)
        resp.raise_for_status()
        return "".join(b.get("text", "") for b in resp.json()["content"])

    def _call_openai(self, key, model, b64, mimetype):
        content = [{"type": "text", "text": PROMPT}]
        if mimetype == "application/pdf":
            content.append({"type": "file", "file": {"filename": "po.pdf",
                "file_data": f"data:application/pdf;base64,{b64}"}})
        else:
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{mimetype};base64,{b64}"}})
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model or "gpt-4o-mini",
                  "messages": [{"role": "user", "content": content}]},
            timeout=180)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _call_gemini(self, key, model, b64, mimetype):
        m = model or "gemini-2.0-flash"
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
            params={"key": key},
            json={"contents": [{"parts": [
                {"inline_data": {"mime_type": mimetype, "data": b64}},
                {"text": PROMPT}]}]},
            timeout=180)
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
