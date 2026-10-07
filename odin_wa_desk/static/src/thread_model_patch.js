import { fields } from "@mail/core/common/record";
import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";

patch(Thread.prototype, {
    setup() {
        super.setup(...arguments);
        /** Whether the current user has the service desk, sent by _to_store_defaults. */
        this.wa_can_create_ticket = fields.Attr(false);
    },
});
