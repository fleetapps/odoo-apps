// Copyright 2026 Fleet Apps
// License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
//
// "Pay now" with M-Pesa: the customer types their number, Odoo prompts that
// phone, and the payment status page waits for M-PESA's answer.

import { _t } from "@web/core/l10n/translation";
import { rpc, RPCError } from "@web/core/network/rpc";
import { patch } from "@web/core/utils/patch";
import { PaymentForm } from "@payment/interactions/payment_form";

patch(PaymentForm.prototype, {
    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== "mpesa") {
            await super._prepareInlineForm(...arguments);
            return;
        }
        this._setPaymentFlow("direct");
    },

    _mpesaPhone() {
        const input = document.querySelector(`#o_mpesa_phone_${this.paymentContext.providerId}`);
        const value = (input?.value || "").trim();
        return value.replace(/\D/g, "").length >= 9 ? value : "";
    },

    async _initiatePaymentFlow(providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode === "mpesa" && !this._mpesaPhone()) {
            this._displayErrorDialog(
                _t("M-PESA"),
                _t("Enter the M-PESA number to send the payment prompt to.")
            );
            this._enableButton();
            return;
        }
        await super._initiatePaymentFlow(...arguments);
    },

    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== "mpesa") {
            await super._processDirectFlow(...arguments);
            return;
        }
        try {
            await this.waitFor(
                rpc("/payment/mpesa/stk", {
                    reference: processingValues.reference,
                    access_token: processingValues.mpesa_access_token,
                    phone: this._mpesaPhone(),
                })
            );
            window.location = "/payment/status";
        } catch (error) {
            if (error instanceof RPCError) {
                this._displayErrorDialog(_t("Payment processing failed"), error.data.message);
                this._enableButton();
            } else {
                throw error;
            }
        }
    },
});
