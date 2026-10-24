# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Keep callback tokens out of the access log.

A callback URL is ``/pay/ke/<token>/...``. Callbacks are not signed, so the
token is what stands between a stranger and a fake "paid" notification, and
werkzeug logs every request path at INFO. The filter rewrites the token to its
last four characters before the record is formatted, wherever werkzeug or
Odoo's request logging prints it.
"""

import logging
import re

_TOKEN_RE = re.compile(r"(/pay/ke/)([0-9a-fA-F]{8,})(?=[/\s?\"]|$)")


def mask_tokens(text):
    return _TOKEN_RE.sub(lambda m: f"{m.group(1)}…{m.group(2)[-4:]}", text)


class CallbackTokenFilter(logging.Filter):
    def filter(self, record):
        try:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    mask_tokens(arg) if isinstance(arg, str) else arg
                    for arg in record.args
                )
            if isinstance(record.msg, str) and "/pay/ke/" in record.msg:
                record.msg = mask_tokens(record.msg)
        except Exception:  # noqa: BLE001 - a log filter must never break logging
            pass
        return True


_FILTER = CallbackTokenFilter()


def install():
    for name in ("werkzeug", "odoo.http", "odoo.http.rpc.request"):
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)


install()
