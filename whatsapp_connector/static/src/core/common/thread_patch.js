import { Thread } from "@mail/core/common/thread";
import { patch } from "@web/core/utils/patch";

patch(Thread.prototype, {
    /**
     * A sent WhatsApp message keeps its own header: its status (and, once
     * failed, its Retry) lives there and must stay visible (SPEC.md §35).
     */
    isSquashed(msg, prevMsg) {
        return !msg.whatsappStatus && super.isSquashed(msg, prevMsg);
    },
});
