// Copyright 2026 Fleet Apps
// License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
//
// The M-Pesa payment terminal. Three ways to take the money, chosen by the
// cashier when the line is sent: prompt the customer's phone (STK Push), pick
// a payment the customer already made to the till, or type a receipt number
// (kept flagged until M-PESA confirms it). Offline, only the last.
//
// The server pushes the outcome of an STK request over the bus
// (MPESA_UPDATE); polling every few seconds covers a lost bus message.

import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PaymentInterface } from "@point_of_sale/app/utils/payment/payment_interface";
import { register_payment_method } from "@point_of_sale/app/services/pos_store";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { MpesaDialog } from "./mpesa_dialog";

const POLL_MS = 3000;
const GIVE_UP_MS = 150000;

export class PaymentMpesa extends PaymentInterface {
    setup() {
        super.setup(...arguments);
        this.waiters = {};
    }

    // The cashier types the amount first, then sends.
    get fastPayments() {
        return false;
    }

    _call(method, data) {
        return this.pos.data.call("pos.payment.method", method, [
            [this.payment_method_id.id],
            { ...data, session_id: this.pos.session.id },
        ]);
    }

    _lineData(line) {
        const order = line.pos_order_id;
        return {
            amount: line.amount,
            line_uuid: line.uuid,
            order_uuid: order.uuid,
            order_name: order.pos_reference || order.name || "",
            partner_id: order.partner_id?.id || false,
        };
    }

    _error(message, title) {
        this.env.services.dialog.add(AlertDialog, {
            title: title || _t("M-PESA"),
            body: message,
        });
    }

    _notify(message, type = "warning") {
        this.env.services.notification.add(message, { type });
    }

    async sendPaymentRequest(uuid) {
        super.sendPaymentRequest(uuid);
        const line = this.pos.getOrder().payment_ids.find((l) => l.uuid === uuid);
        if (!line) {
            return false;
        }
        line.mpesa_resolved = false;
        if (line.amount <= 0) {
            this._error(_t("M-PESA cannot pay a refund or a zero amount here. Refund from the back office."));
            return false;
        }
        if (!Number.isInteger(line.amount)) {
            const whole = Math.floor(line.amount);
            line.setAmount(whole);
            this._error(
                _t("M-PESA takes whole shillings. The line is now %s: take the cents in cash.", whole)
            );
            return false;
        }
        const offline = this.pos.data.network.offline;
        const choice = await makeAwaitable(this.env.services.dialog, MpesaDialog, {
            paymentMethod: this.payment_method_id,
            amount: line.amount,
            phone: line.pos_order_id.partner_id?.phone || "",
            offline,
            terminal: this,
        });
        if (!choice) {
            return false;
        }
        if (choice.mode === "stk") {
            return this._stk(line, choice.phone);
        }
        if (choice.mode === "claim") {
            return this._claim(line, choice.transaction);
        }
        if (choice.mode === "manual") {
            return this._manual(line, choice.receipt, offline);
        }
        return false;
    }

    async _stk(line, phone) {
        line.setPaymentStatus("waitingCard");
        let result;
        try {
            result = await this._call("mpesa_stk_request", { ...this._lineData(line), phone });
        } catch {
            this._error(_t("The Odoo server could not be reached. Use Type receipt if the customer has paid."));
            return false;
        }
        if (result.error) {
            this._error(result.error);
            return false;
        }
        line.mpesa_tx_ref = result.id;
        return this._waitFor(line, result);
    }

    async _waitFor(line, first) {
        let current = first;
        const started = Date.now();
        while (true) {
            if (line.mpesa_resolved) {
                return true;
            }
            if (current.state === "confirmed" && !current.confirming) {
                return this._paid(line, current);
            }
            if (current.state === "failed") {
                this._error(current.message);
                return false;
            }
            if (current.state === "unknown") {
                this._error(
                    current.message +
                        " " +
                        _t("If the customer was charged, the payment appears under Paid to till.")
                );
                return false;
            }
            if (line.mpesa_cancelled) {
                return false;
            }
            if (Date.now() - started > GIVE_UP_MS) {
                this._error(
                    _t("No answer from M-PESA yet. If the customer was charged, the payment will appear under Paid to till.")
                );
                return false;
            }
            current = await this._nextUpdate(current.id);
        }
    }

    _nextUpdate(transactionId) {
        return new Promise((resolve) => {
            const timer = setTimeout(async () => {
                delete this.waiters[transactionId];
                try {
                    resolve(await this._call("mpesa_status", { transaction_id: transactionId }));
                } catch {
                    resolve({ id: transactionId, state: "requested" });
                }
            }, POLL_MS);
            this.waiters[transactionId] = (payload) => {
                clearTimeout(timer);
                delete this.waiters[transactionId];
                resolve(payload);
            };
        });
    }

    // Called from the bus.
    onMpesaUpdate(payload) {
        const waiter = this.waiters[payload?.id];
        if (waiter) {
            waiter(payload);
        }
    }

    _paid(line, transaction) {
        line.mpesa_tx_ref = transaction.id || line.mpesa_tx_ref || 0;
        line.mpesa_receipt = transaction.receipt || line.mpesa_receipt || "";
        line.transaction_id = line.mpesa_receipt;
        line.mpesa_manual = Boolean(transaction.manual);
        line.mpesa_resolved = true;
        return true;
    }

    async _claim(line, transaction) {
        let result;
        try {
            result = await this._call("mpesa_claim", {
                ...this._lineData(line),
                transaction_id: transaction.id,
            });
        } catch {
            this._error(_t("The Odoo server could not be reached."));
            return false;
        }
        if (result.error) {
            this._error(result.error);
            return false;
        }
        if (result.amount !== line.amount) {
            line.setAmount(result.amount);
        }
        return this._paid(line, result);
    }

    async _manual(line, receipt, offline) {
        if (offline) {
            // The server hears of it when the order syncs, and keeps it
            // flagged until M-PESA confirms the same receipt and amount.
            this._notify(
                _t("Receipt %s recorded offline. It stays flagged until M-PESA confirms it.", receipt)
            );
            return this._paid(line, { receipt, manual: true });
        }
        let result;
        try {
            result = await this._call("mpesa_manual_receipt", { ...this._lineData(line), receipt });
        } catch {
            this._notify(_t("Odoo is unreachable: receipt %s is recorded and will be checked later.", receipt));
            return this._paid(line, { receipt, manual: true });
        }
        if (result.error) {
            this._error(result.error);
            return false;
        }
        if (result.manual) {
            this._notify(
                _t("Receipt %s accepted. M-PESA has not confirmed it yet: your supervisor is told if it never does.", result.receipt)
            );
        }
        return this._paid(line, result);
    }

    // "Force done" on an M-Pesa line means: the cashier has a receipt number.
    async forceDone(line) {
        const choice = await makeAwaitable(this.env.services.dialog, MpesaDialog, {
            paymentMethod: this.payment_method_id,
            amount: line.amount,
            phone: "",
            offline: this.pos.data.network.offline,
            manualOnly: true,
            terminal: this,
        });
        if (!choice?.receipt) {
            return false;
        }
        const done = await this._manual(line, choice.receipt, this.pos.data.network.offline);
        if (done) {
            this.onMpesaUpdate({ id: line.mpesa_tx_ref });
        }
        return done;
    }

    sendPaymentCancel(order, uuid) {
        super.sendPaymentCancel(order, uuid);
        const line = order.payment_ids.find((l) => l.uuid === uuid);
        if (line) {
            line.mpesa_cancelled = true;
            if (line.mpesa_tx_ref) {
                this.onMpesaUpdate({ id: line.mpesa_tx_ref, state: "requested" });
                this._call("mpesa_release", {
                    transaction_id: line.mpesa_tx_ref,
                    order_uuid: order.uuid,
                    order_name: order.pos_reference || order.name || "",
                }).catch(() => {});
            }
        }
        return Promise.resolve(true);
    }
}

register_payment_method("mpesa", PaymentMpesa);
