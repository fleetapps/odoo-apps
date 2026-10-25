// Copyright 2026 Fleet Apps
// License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

import { _t } from "@web/core/l10n/translation";
import { Component, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

const RECEIPT_RE = /^[A-Z0-9]{8,12}$/;

export class MpesaDialog extends Component {
    static template = "x_ke_mpesa_pos.MpesaDialog";
    static components = { Dialog };
    static props = {
        paymentMethod: Object,
        amount: Number,
        phone: { type: String, optional: true },
        offline: { type: Boolean, optional: true },
        manualOnly: { type: Boolean, optional: true },
        terminal: Object,
        getPayload: Function,
        close: Function,
    };

    setup() {
        const pm = this.props.paymentMethod;
        let tab = "manual";
        if (!this.props.offline && !this.props.manualOnly) {
            tab = pm.mpesa_allow_stk ? "stk" : pm.mpesa_allow_claim ? "claim" : "manual";
        }
        this.state = useState({
            tab,
            phone: this.props.phone || "",
            receipt: "",
            receipts: [],
            loading: false,
            error: "",
        });
        onWillStart(async () => {
            if (this.state.tab === "claim") {
                await this.loadReceipts();
            }
        });
    }

    get title() {
        return _t("M-PESA %s", this.props.paymentMethod.mpesa_till_number || "");
    }

    get amountText() {
        return this.env.utils.formatCurrency(this.props.amount);
    }

    get tabs() {
        const pm = this.props.paymentMethod;
        if (this.props.offline || this.props.manualOnly) {
            return [];
        }
        return [
            pm.mpesa_allow_stk && { id: "stk", label: _t("Prompt phone") },
            pm.mpesa_allow_claim && { id: "claim", label: _t("Paid to till") },
            pm.mpesa_allow_manual && { id: "manual", label: _t("Type receipt") },
        ].filter(Boolean);
    }

    async setTab(tab) {
        this.state.tab = tab;
        this.state.error = "";
        if (tab === "claim") {
            await this.loadReceipts();
        }
    }

    async loadReceipts() {
        this.state.loading = true;
        this.state.error = "";
        try {
            this.state.receipts = await this.props.terminal._call("mpesa_unclaimed_receipts", {
                amount: this.props.amount,
            });
        } catch {
            this.state.error = _t("The Odoo server could not be reached.");
        } finally {
            this.state.loading = false;
        }
    }

    formatTime(value) {
        return value ? luxon.DateTime.fromSQL(value, { zone: "utc" }).toLocal().toFormat("HH:mm") : "";
    }

    formatAmount(value) {
        return this.env.utils.formatCurrency(value);
    }

    choose(payload) {
        this.props.getPayload(payload);
        this.props.close();
    }

    sendPrompt() {
        const digits = (this.state.phone || "").replace(/\D/g, "");
        if (digits.length < 9) {
            this.state.error = _t("Enter the customer's M-PESA number.");
            return;
        }
        this.choose({ mode: "stk", phone: this.state.phone });
    }

    pick(transaction) {
        this.choose({ mode: "claim", transaction });
    }

    useReceipt() {
        const receipt = (this.state.receipt || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
        if (!RECEIPT_RE.test(receipt)) {
            this.state.error = _t("An M-PESA receipt is 10 letters and digits, e.g. QAB1CD2EF3.");
            return;
        }
        this.choose({ mode: "manual", receipt });
    }
}
