// Copyright 2026 Fleet Apps
// License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

import { patch } from "@web/core/utils/patch";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";

patch(PaymentScreen.prototype, {
    // "Force done" would let any line be marked paid without M-PESA ever
    // having seen the money. On an M-Pesa line it asks for the receipt
    // instead, which stays flagged until M-PESA confirms it.
    async sendForceDone(line) {
        const terminal = line.payment_method_id.payment_terminal;
        if (line.payment_method_id.use_payment_terminal !== "mpesa" || !terminal) {
            return super.sendForceDone(...arguments);
        }
        const done = await terminal.forceDone(line);
        if (done && line.getPaymentStatus() !== "done") {
            return super.sendForceDone(...arguments);
        }
    },
});
