import { Message } from "@mail/core/common/message";
import { rpc } from "@web/core/network/rpc";
import { patch } from "@web/core/utils/patch";

patch(Message.prototype, {
    async onClickWhatsappRetry() {
        const message = this.props.message;
        await rpc("/whatsapp_connector/message/retry", { message_id: message.id });
        // queued again; Meta's statuses then arrive through the bus
        Object.assign(message, { whatsappStatus: "queued", whatsappError: false });
    },
});
