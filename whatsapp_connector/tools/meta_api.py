"""The one place that talks to Meta's WhatsApp Cloud API (SPEC.md §55, R23).

Every call goes through :class:`WhatsAppApi`. The access token is sent in the
Authorization header only and is never logged or put in an error message.
"""
import logging

import requests

_logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com"
TIMEOUT = 20  # seconds, per request
MEDIA_TIMEOUT = 60

# Graph API error code for an invalid or expired access token (Odoo's WhatsApp
# documentation: "User Error 190: Error validating access token").
TOKEN_ERROR_CODES = {190}


class MetaApiError(Exception):
    """An error returned by Meta, or a failure to reach it."""

    def __init__(self, message, code=None, title=None, details=None, http_status=None,
                 error_type=None, subcode=None, trace_id=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.title = title
        self.details = details
        self.http_status = http_status
        self.error_type = error_type
        self.subcode = subcode
        self.trace_id = trace_id

    @property
    def is_token_error(self):
        return self.code in TOKEN_ERROR_CODES

    def as_error_list(self):
        """The shape Meta uses for ``errors`` in status webhooks."""
        return [{
            "code": self.code,
            "title": self.title or self.message,
            "message": self.message,
            "error_data": {"details": self.details} if self.details else {},
        }]

    def __str__(self):
        prefix = f"({self.code}) " if self.code is not None else ""
        return f"{prefix}{self.title or self.message}"


class WhatsAppApi:
    """Meta calls for one WhatsApp Business Account."""

    def __init__(self, account):
        account = account.sudo()
        account.ensure_one()
        self.version = (account.api_version or "").strip()
        self.token = account.access_token or ""
        self.phone_number_id = account.phone_number_id
        self.waba_id = account.waba_id
        if not self.version or not self.token:
            message = "The account needs an API version and an access token."
            raise MetaApiError(message)

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------

    def _url(self, path):
        return f"{GRAPH_URL}/{self.version}/{path.lstrip('/')}"

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _request(self, method, path, *, params=None, json=None, data=None, files=None,
                 timeout=TIMEOUT):
        url = self._url(path)
        try:
            response = requests.request(
                method, url, params=params, json=json, data=data, files=files,
                headers=self._headers(), timeout=timeout,
            )
        except requests.RequestException as e:
            # the exception text never contains the token (it is in a header)
            raise MetaApiError(f"Could not reach Meta: {e.__class__.__name__}") from None
        return self._parse(response)

    @staticmethod
    def _parse(response):
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("error"):
            error = payload["error"]
            raise MetaApiError(
                error.get("message") or "Meta returned an error",
                code=error.get("code"),
                title=error.get("error_user_title") or error.get("message"),
                details=(error.get("error_data") or {}).get("details") or error.get("error_user_msg"),
                http_status=response.status_code,
                error_type=error.get("type"),
                subcode=error.get("error_subcode"),
                trace_id=error.get("fbtrace_id"),
            )
        if not response.ok:
            raise MetaApiError(
                f"Meta answered HTTP {response.status_code}", http_status=response.status_code,
            )
        return payload if payload is not None else {}

    # ------------------------------------------------------------------
    # messages
    # ------------------------------------------------------------------

    def send_message(self, payload):
        """POST /{Phone-Number-ID}/messages. ``payload`` is Meta's message object."""
        body = {"messaging_product": "whatsapp", "recipient_type": "individual", **payload}
        return self._request("POST", f"{self.phone_number_id}/messages", json=body)

    def mark_read(self, message_id, typing_indicator=False):
        body = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
        if typing_indicator:
            body["typing_indicator"] = {"type": "text"}
        return self._request("POST", f"{self.phone_number_id}/messages", json=body)

    # ------------------------------------------------------------------
    # media (R22)
    # ------------------------------------------------------------------

    def upload_media(self, filename, mimetype, content):
        files = {"file": (filename, content, mimetype)}
        data = {"messaging_product": "whatsapp", "type": mimetype}
        result = self._request(
            "POST", f"{self.phone_number_id}/media", data=data, files=files, timeout=MEDIA_TIMEOUT,
        )
        return result.get("id")

    def get_media_url(self, media_id):
        """Return Meta's media info: url (valid 5 minutes), mime_type, sha256, file_size."""
        return self._request("GET", media_id, params={"phone_number_id": self.phone_number_id})

    def download_media(self, url):
        try:
            response = requests.get(url, headers=self._headers(), timeout=MEDIA_TIMEOUT)
        except requests.RequestException as e:
            raise MetaApiError(f"Could not download the media: {e.__class__.__name__}") from None
        if response.status_code == 404:
            message = "Media URL expired or not found"
            raise MetaApiError(message, http_status=404)
        if not response.ok:
            self._parse(response)
        return response.content, response.headers.get("Content-Type")

    # ------------------------------------------------------------------
    # templates (R25)
    # ------------------------------------------------------------------

    def get_templates(self, limit=100):
        """All templates of the WhatsApp Business Account, following pagination."""
        templates = []
        params = {"limit": limit}
        path = f"{self.waba_id}/message_templates"
        while True:
            result = self._request("GET", path, params=params)
            templates.extend(result.get("data") or [])
            after = ((result.get("paging") or {}).get("cursors") or {}).get("after")
            if not after or not (result.get("paging") or {}).get("next"):
                return templates
            params = {"limit": limit, "after": after}

    def get_template(self, template_id):
        """GET /{TEMPLATE_ID}: the same object as one item of :meth:`get_templates`."""
        return self._request("GET", str(template_id))

    def submit_template(self, payload):
        """POST /{WABA-ID}/message_templates; Meta answers ``{id, status, category}``."""
        return self._request("POST", f"{self.waba_id}/message_templates", json=payload)

    def edit_template(self, template_id, payload):
        """POST /{TEMPLATE_ID} (Meta's "Edit template")."""
        return self._request("POST", str(template_id), json=payload)

    # ------------------------------------------------------------------
    # account health (R24)
    # ------------------------------------------------------------------

    def get_phone_number(self):
        return self._request("GET", self.phone_number_id, params={
            "fields": "status,display_phone_number,verified_name,quality_rating",
        })

    def get_subscribed_apps(self):
        result = self._request("GET", f"{self.waba_id}/subscribed_apps")
        # Meta's reference shows the app nested in whatsapp_business_api_data;
        # its response schema also allows {id, name} directly: accept both.
        return [
            (item.get("whatsapp_business_api_data") or item).get("id")
            for item in (result.get("data") or [])
        ]

    def subscribe_app(self):
        return self._request("POST", f"{self.waba_id}/subscribed_apps")
