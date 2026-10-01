import { fields } from "@mail/core/common/record";
import { Thread } from "@mail/core/common/thread_model";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

const { DateTime } = luxon;

/** WhatsApp conversations are Discuss channels of type "whatsapp" (SPEC.md §12.1, R8). */
patch(Thread.prototype, {
    setup() {
        super.setup(...arguments);
        /** [D8] whether the current user may create leads (salespeople) */
        this.wa_can_create_lead = fields.Attr(false);
        this.wa_customer_phone = fields.Attr(undefined);
        /** @type {number|false|undefined} the linked crm.lead id */
        this.wa_lead_id = fields.Attr(undefined);
        /** the customer's contact, for its picture */
        this.wa_partner_id = fields.One("res.partner");
        /** Lead Routing (Mode B): the owner, and whether the current user manages routing */
        this.wa_routed = fields.Attr(false);
        this.wa_owner_name = fields.Attr(false);
        this.wa_owner_partner_id = fields.Attr(false);
        this.wa_can_manage = fields.Attr(false);
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
    get avatarUrl() {
        // the customer's picture, in the sidebar, the header and chat windows
        if (this.channel_type === "whatsapp" && this.wa_partner_id) {
            return this.wa_partner_id.avatarUrl;
        }
        return super.avatarUrl;
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
    /** Lead Routing: who owns the conversation, shown in the header (§38). */
    get waOwnerText() {
        if (this.channel_type !== "whatsapp" || !this.wa_routed) {
            return "";
        }
        return this.wa_owner_name
            ? _t("Assigned to %(user)s", { user: this.wa_owner_name })
            : _t("Unassigned");
    },
    /** The owner or a WhatsApp manager may close a routed conversation (§44). */
    get waCanClose() {
        return (
            this.channel_type === "whatsapp" &&
            this.wa_routed &&
            this.wa_status === "open" &&
            (this.wa_can_manage || this.wa_owner_partner_id === this.store.self?.id)
        );
    },
    /** How long free-form replies remain possible, shown in the header. */
    get waWindowText() {
        if (this.channel_type !== "whatsapp" || !this.waWindowOpen) {
            return "";
        }
        return _t("window closes %(when)s", { when: this.wa_window_expires_at.toRelative() });
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
