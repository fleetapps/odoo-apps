import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";

patch(Thread.prototype, {
    _computeDiscussAppCategory() {
        if (this.channel_type === "whatsapp") {
            return this.store.discuss.whatsappCategory;
        }
        return super._computeDiscussAppCategory();
    },
    /** R5: the customer's answer opens the chat window of the users taking part. */
    get autoOpenChatWindowOnNewMessage() {
        return (
            (this.channel_type === "whatsapp" &&
                !this.store.chatHub.compact &&
                Boolean(this.self_member_id)) ||
            super.autoOpenChatWindowOnNewMessage
        );
    },
});
