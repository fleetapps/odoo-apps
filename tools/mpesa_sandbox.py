#!/usr/bin/env python3
# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Exercise Safaricom's real Daraja sandbox with the client x_ke_mpesa uses.

No Odoo needed: it imports x_ke_mpesa/lib/daraja.py directly. Run it from a
machine that can reach sandbox.safaricom.co.ke, with the consumer key and
secret of a Daraja sandbox app (developer.safaricom.co.ke > My Apps):

    DARAJA_KEY=... DARAJA_SECRET=... python3 tools/mpesa_sandbox.py stk 0708374149
    DARAJA_KEY=... DARAJA_SECRET=... python3 tools/mpesa_sandbox.py query ws_CO_...
    DARAJA_KEY=... DARAJA_SECRET=... python3 tools/mpesa_sandbox.py c2b 600984 \\
        https://your-host/pay/ke/<token>/c2b/confirm

``stk`` prompts the phone on shortcode 174379 with Safaricom's public sandbox
passkey; ``c2b`` registers the confirmation URL on a sandbox test shortcode
(the simulator's "Party B" of your test credentials) and simulates a payment
to it, so the confirmation arrives at your Odoo.
"""

import argparse
import importlib.util
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "daraja", HERE.parent / "x_ke_mpesa" / "lib" / "daraja.py")
daraja = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(daraja)

SANDBOX_SHORTCODE = "174379"
# Published on the Daraja portal for shortcode 174379; not a secret.
SANDBOX_PASSKEY = "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"


def client():
    key, secret = os.environ.get("DARAJA_KEY"), os.environ.get("DARAJA_SECRET")
    if not key or not secret:
        sys.exit("Set DARAJA_KEY and DARAJA_SECRET (a Daraja sandbox app).")
    return daraja.DarajaClient(daraja.SANDBOX_URL, key, secret)


def show(data):
    print(json.dumps(daraja.redact(data), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    stk = sub.add_parser("stk", help="STK Push KES 1 to a phone on 174379")
    stk.add_argument("phone")
    stk.add_argument("--amount", type=int, default=1)
    stk.add_argument("--callback", default="https://example.com/pay/ke/0/stk")
    query = sub.add_parser("query", help="STK Query a CheckoutRequestID")
    query.add_argument("checkout_request_id")
    c2b = sub.add_parser("c2b", help="Register a confirmation URL and simulate a payment")
    c2b.add_argument("shortcode")
    c2b.add_argument("confirmation_url")
    c2b.add_argument("--amount", type=int, default=1)
    c2b.add_argument("--bill-ref", default="")
    c2b.add_argument("--msisdn", default="254708374149")
    args = parser.parse_args()

    api = client()
    try:
        if args.command == "stk":
            show(api.stk_push(
                business_shortcode=SANDBOX_SHORTCODE, passkey=SANDBOX_PASSKEY,
                amount=args.amount, phone=args.phone, party_b=SANDBOX_SHORTCODE,
                transaction_type="CustomerPayBillOnline", callback_url=args.callback,
                reference="SANDBOX", description="Sandbox test"))
        elif args.command == "query":
            show(api.stk_query(business_shortcode=SANDBOX_SHORTCODE, passkey=SANDBOX_PASSKEY,
                               checkout_request_id=args.checkout_request_id))
        elif args.command == "c2b":
            validation = args.confirmation_url.replace("/c2b/confirm", "/c2b/validate")
            show(api.c2b_register(shortcode=args.shortcode,
                                  confirmation_url=args.confirmation_url,
                                  validation_url=validation, response_type="Completed"))
            command = "CustomerPayBillOnline" if args.bill_ref else "CustomerBuyGoodsOnline"
            show(api.c2b_simulate(shortcode=args.shortcode, command_id=command,
                                  amount=args.amount, msisdn=args.msisdn,
                                  bill_ref=args.bill_ref or None))
    except (daraja.DarajaError, daraja.DarajaTransportError) as exc:
        sys.exit(f"Daraja: {exc}")


if __name__ == "__main__":
    main()
