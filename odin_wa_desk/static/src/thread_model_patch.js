import { fields } from "@mail/core/common/record";
import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";

patch(Thread.prototype, {
    setup() {
        super.setup(...arguments);
        /** Whether the current user has the service desk, sent by _to_store_defaults. */
        this.wa_can_create_ticket = fields.Attr(false);
    },
    /** Routed, open, and not already mine: wa_owner_partner_id is false when nobody owns it. */
    get waCanTake() {
        return (
            this.channel_type === "whatsapp" &&
            this.wa_routed &&
            this.wa_status === "open" &&
            this.wa_owner_partner_id !== this.store.self?.id
        );
    },
});
