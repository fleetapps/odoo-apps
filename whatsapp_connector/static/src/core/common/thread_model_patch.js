import { fields } from "@mail/core/common/record";
import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";

const { DateTime } = luxon;

/** WhatsApp conversations are Discuss channels of type "whatsapp" (SPEC.md §12.1, R8). */
patch(Thread.prototype, {
    setup() {
        super.setup(...arguments);
        this.wa_customer_phone = fields.Attr(undefined);
        this.wa_status = fields.Attr(undefined);
        this.wa_username = fields.Attr(undefined);
        this.wa_window_expires_at = fields.Datetime();
        /** Set on records by the chatter: approved templates exist for this user (R10). */
        this.wa_can_send = fields.Attr(false);
    },
    get isChatChannel() {
        // one-to-one with the customer: unread counters and chat windows like a chat
        return this.channel_type === "whatsapp" || super.isChatChannel;
    },
    get supportsCustomChannelName() {
        return this.channel_type !== "whatsapp" && super.supportsCustomChannelName;
    },
    get allowedToLeaveChannelTypes() {
        return [...super.allowedToLeaveChannelTypes, "whatsapp"];
    },
    /** Meta's 24-hour customer service window (SPEC.md §28, R34). */
    get waWindowOpen() {
        return Boolean(this.wa_window_expires_at && this.wa_window_expires_at > DateTime.now());
    },
    /** Free-form messages are refused by Meta: only a template can be sent. */
    get waComposerLocked() {
        return this.channel_type === "whatsapp" && !this.waWindowOpen;
    },
    /** The customer's number or username, shown next to the conversation's name. */
    get waHeaderText() {
        if (this.channel_type !== "whatsapp") {
            return "";
        }
        return this.wa_customer_phone || (this.wa_username ? `@${this.wa_username}` : "");
    },
    waOpenTemplateComposer() {
        return this.store.env.services.action.doAction(
            "whatsapp_connector.action_whatsapp_composer",
            {
                additionalContext: {
                    active_model: "discuss.channel",
                    active_id: this.id,
                    active_ids: [this.id],
                },
            }
        );
    },
});
