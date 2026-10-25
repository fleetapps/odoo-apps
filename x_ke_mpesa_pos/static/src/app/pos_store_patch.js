// Copyright 2026 Fleet Apps
// License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

patch(PosStore.prototype, {
    async setup() {
        await super.setup(...arguments);
        // The server tells the point of sale when an STK request settles.
        this.data.connectWebSocket("MPESA_UPDATE", (payload) => {
            for (const method of this.models["pos.payment.method"].getAll()) {
                if (method.use_payment_terminal === "mpesa") {
                    method.payment_terminal?.onMpesaUpdate(payload);
                }
            }
        });
    },
});
