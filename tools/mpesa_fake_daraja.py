#!/usr/bin/env python3
# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""A pretend Daraja, for training cashiers and rehearsing a go-live.

It is the fake the tests use (x_ke_mpesa/tests/fake_daraja.py), run as a
server: every STK prompt is "paid" a few seconds later, and a customer paying a
till is one curl away. No Safaricom account, no tunnel, no money.

    python3 tools/mpesa_fake_daraja.py --port 18080 --control-port 18081

In Odoo, on a test database only:

* M-Pesa app: API URL http://<this host>:18080, consumer key ``ck_test``,
  consumer secret ``cs_test``;
* store 174379 with the public sandbox passkey, its tills under it;
* Register URLs on each till (Odoo warns that the URLs are not HTTPS: that is
  expected here, Safaricom would refuse them).

Then, on the control port:

    # a customer pays till 600100 (a paybill account number with &account=)
    curl -X POST 'http://localhost:18081/pay?till=600100&amount=2500&name=WANJIKU'
    # the next prompt fails: 1032 cancelled, 1037 no answer, 2001 wrong PIN, 1 no funds
    curl -X POST 'http://localhost:18081/next-stk?code=1032'
    # what Odoo has asked so far (passwords left out)
    curl http://localhost:18081/requests
"""

import argparse
import importlib.util
import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FAKE = Path(__file__).resolve().parent.parent / "x_ke_mpesa" / "tests" / "fake_daraja.py"


def load_fake():
    spec = importlib.util.spec_from_file_location("fake_daraja", FAKE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def deliver_forever(fake, delay):
    """Post what Safaricom would post, ``delay`` seconds after it was due."""
    due = []
    while True:
        time.sleep(0.5)
        now = time.monotonic()
        due += [(now + delay, item) for item in fake.take()]
        ready = [item for at, item in due if at <= now]
        due = [(at, item) for at, item in due if at > now]
        for item in ready:
            request = urllib.request.Request(
                item["url"], data=json.dumps(item["body"]).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=10) as answer:
                    print("posted", urlsplit(item["url"]).path.rsplit("/", 2)[-2:],
                          answer.status, flush=True)
            except Exception as exc:  # noqa: BLE001 - keep delivering the rest
                print("could not post", item["url"], exc, flush=True)


def control_handler(fake):
    class Control(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _answer(self, status, body):
            data = json.dumps(body, indent=1).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if urlsplit(self.path).path != "/requests":
                return self._answer(404, {"error": "GET /requests"})
            with fake.lock:
                seen = [[path, {k: v for k, v in body.items()
                                if k not in ("Password", "SecurityCredential")}]
                        for path, body in fake.requests]
            return self._answer(200, seen)

        def do_POST(self):
            parts = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            try:
                if parts.path == "/pay":
                    amount = float(query.get("amount", "100"))
                    receipt = fake.c2b_payment(
                        query.get("till") or query.get("shortcode") or "600100",
                        int(amount) if amount.is_integer() else amount,
                        bill_ref=query.get("account", ""),
                        first_name=query.get("name", "JOHN").upper(),
                        receipt=query.get("receipt"))
                    return self._answer(200, {"receipt": receipt})
                if parts.path == "/next-stk":
                    fake.script_stk(result_code=int(query.get("code", "0")))
                    return self._answer(200, {"next_stk": query.get("code", "0")})
                if parts.path == "/passkey":
                    fake.passkeys[query["shortcode"]] = query["passkey"]
                    return self._answer(200, {"shortcodes": sorted(fake.passkeys)})
            except (AssertionError, KeyError, ValueError) as exc:
                return self._answer(400, {"error": str(exc)})
            return self._answer(404, {"error": "POST /pay, /next-stk or /passkey"})

    return Control


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--control-port", type=int, default=18081)
    parser.add_argument("--delay", type=float, default=3.0,
                        help="seconds before a callback is posted (the customer's PIN)")
    parser.add_argument("--key", default="ck_test")
    parser.add_argument("--secret", default="cs_test")
    args = parser.parse_args(argv)

    fake = load_fake().FakeDaraja()
    fake.consumer = (args.key, args.secret)
    fake.start(args.host, args.port)
    threading.Thread(target=deliver_forever, args=(fake, args.delay), daemon=True).start()
    print(f"Daraja (fake) on {args.host}:{args.port}, control on {args.control_port}", flush=True)
    try:
        ThreadingHTTPServer((args.host, args.control_port), control_handler(fake)).serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        fake.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
