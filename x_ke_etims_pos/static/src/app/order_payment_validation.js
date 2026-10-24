import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { patch } from "@web/core/utils/patch";

patch(OrderPaymentValidation.prototype, {
    async afterOrderValidation() {
        // The order is on the server by now; ask for its signature before the
        // receipt prints, for no longer than the till's eTIMS wait.
        await this.pos.keEtimsRefresh(this.order);
        return await super.afterOrderValidation(...arguments);
    },
});
