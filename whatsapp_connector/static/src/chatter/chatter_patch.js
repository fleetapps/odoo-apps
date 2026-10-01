import { Chatter } from "@mail/chatter/web_portal/chatter";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";

/** The chatter's WhatsApp button: send an approved template from this record (R10). */
patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.waAction = useService("action");
    },
    get requestList() {
        return [...super.requestList, "wa_can_send"];
    },
    onClickWhatsapp() {
        const thread = this.state.thread;
        return this.waAction.doAction("whatsapp_connector.action_whatsapp_composer", {
            additionalContext: {
                active_model: thread.model,
                active_id: thread.id,
                active_ids: [thread.id],
            },
            onClose: () => this.load(thread, ["messages"]),
        });
    },
});
