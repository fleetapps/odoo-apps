# -*- coding: utf-8 -*-
"""LLM extraction with the customer's OWN API key (BYO-key: no per-page fees,
the app's core pitch vs token-resale competitors).

Providers: OpenAI / Anthropic / Google Gemini - all support PDF or image input.
API references (VERIFY-ON-BUILD - endpoints/models move fast):
* Anthropic Messages API + PDF support: https://docs.claude.com
* OpenAI Responses/ChatCompletions vision: https://platform.openai.com/docs
* Gemini generateContent: https://ai.google.dev/api

The prompt requests STRICT JSON with per-field confidence:
{"fields": {"vendor_name": {"value": ..., "confidence": 0.97}, ...},
 "lines": [{"description":..., "quantity":..., "price_unit":..., "confidence":...}]}
"""
import base64
import json
import requests
from odoo import api, models
from odoo.exceptions import UserError

PROMPT = (
    "You are an accounts-payable clerk. Extract this vendor bill into strict "
    "JSON only, no prose. Schema: {\"fields\": {vendor_name, vendor_vat, "
    "invoice_ref, invoice_date (YYYY-MM-DD), due_date, currency (ISO code), "
    "amount_untaxed, amount_tax, amount_total - each as {value, confidence "
    "0..1}}, \"lines\": [{description, quantity, price_unit, confidence}]}. "
    "Use null when unreadable; never guess silently - lower the confidence."
)


class AiBillLLM(models.AbstractModel):
    _name = "ai.bill.llm"
    _description = "LLM Extraction Provider"

    def _params(self):
        icp = self.env["ir.config_parameter"].sudo()
        return (icp.get_param("ai_bill_ocr_community.provider", "anthropic"),
                icp.get_param("ai_bill_ocr_community.api_key", ""),
                icp.get_param("ai_bill_ocr_community.model", ""))

    @api.model
    def extract_bill(self, attachment):
        provider, key, model = self._params()
        if not key:
            raise UserError("Configure your LLM API key in Settings first.")
        b64 = attachment.datas.decode()
        handler = getattr(self, f"_call_{provider}")
        text = handler(key, model, b64, attachment.mimetype)
        cleaned = text.strip().removeprefix("```json").removesuffix("```").strip()
        return json.loads(cleaned)

    def _call_anthropic(self, key, model, b64, mimetype):
        block_type = "document" if mimetype == "application/pdf" else "image"
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": model or "claude-sonnet-4-6", "max_tokens": 4000,
                  "messages": [{"role": "user", "content": [
                      {"type": block_type, "source": {
                          "type": "base64", "media_type": mimetype,
                          "data": b64}},
                      {"type": "text", "text": PROMPT}]}]},
            timeout=120)
        resp.raise_for_status()
        return "".join(b.get("text", "") for b in resp.json()["content"])

    def _call_openai(self, key, model, b64, mimetype):
        content = [{"type": "text", "text": PROMPT}]
        if mimetype == "application/pdf":
            content.append({"type": "file", "file": {
                "filename": "bill.pdf", "file_data":
                f"data:application/pdf;base64,{b64}"}})
        else:
            content.append({"type": "image_url", "image_url": {
                "url": f"data:{mimetype};base64,{b64}"}})
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model or "gpt-4o-mini",
                  "messages": [{"role": "user", "content": content}]},
            timeout=120)
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
            timeout=120)
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
