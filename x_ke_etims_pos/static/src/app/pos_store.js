import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";

/** How recent a check must be for a print to skip asking again (ms). */
const RECENT_CHECK = 10000;

patch(PosStore.prototype, {
    /**
     * One short attempt to have the server sign an order, then whatever it
     * has. Offline, slow or failing, nothing breaks: the order stays queued on
     * the server and the receipt prints "eTIMS: pending".
     */
    async keEtimsRefresh(order) {
        if (!this.config.ke_etims_enabled || !order || typeof order.id !== "number") {
            return;
        }
        order.uiState.keEtimsCheckedAt = Date.now();
        const wait = Math.max(1, this.config.ke_etims_sign_timeout || 5) * 1000;
        let timer;
        try {
            const result = await Promise.race([
                this.data.call("pos.order", "ke_etims_sign_from_ui", [[order.id]]),
                new Promise((resolve) => {
                    timer = setTimeout(() => resolve(null), wait);
                }),
            ]);
            const values = result && result[0];
            if (values) {
                const { id: _id, ...fields } = values;
                // Server-computed: never something the till should sync back.
                const wasDirty = order.isDirty();
                order.update(fields);
                if (!wasDirty) {
                    order._dirty = false;
                }
            }
        } catch {
            // Offline, or the server refused: the receipt says pending.
        } finally {
            clearTimeout(timer);
        }
    },

    async printReceipt(options = {}) {
        const order = options.order || this.getOrder();
        const checkedAt = order?.uiState?.keEtimsCheckedAt || 0;
        if (
            order?.ke_etims_applicable &&
            order.ke_etims_state !== "sent" &&
            Date.now() - checkedAt > RECENT_CHECK
        ) {
            // A reprint of a pending order: perhaps it is signed by now.
            await this.keEtimsRefresh(order);
        }
        return await super.printReceipt(...arguments);
    },
});
