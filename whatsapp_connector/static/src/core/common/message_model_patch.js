import { Message } from "@mail/core/common/message_model";
import { fields } from "@mail/core/common/record";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

const STATUS = {
    queued: { icon: "fa fa-clock-o", title: _t("Waiting to be sent") },
    sent: { icon: "fa fa-check", title: _t("Sent") },
    delivered: { icon: "o-whatsapp-doubleCheck fa fa-check", title: _t("Delivered") },
    read: { icon: "o-whatsapp-doubleCheck o-whatsapp-read fa fa-check", title: _t("Read") },
    failed: { icon: "fa fa-exclamation-circle text-danger", title: _t("Not delivered") },
    dropped: { icon: "fa fa-exclamation-triangle text-warning", title: _t("Never delivered by WhatsApp") },
};

/** WhatsApp delivery status of sent messages (SPEC.md §35). */
patch(Message.prototype, {
    setup() {
        super.setup(...arguments);
        /** @type {"queued"|"sent"|"delivered"|"read"|"failed"|"dropped"|false|undefined} */
        this.whatsappStatus = fields.Attr(undefined);
        /** @type {string|false|undefined} */
        this.whatsappError = fields.Attr(undefined);
    },
    get waStatusIcon() {
        return STATUS[this.whatsappStatus]?.icon;
    },
    get waStatusTitle() {
        const title = STATUS[this.whatsappStatus]?.title || "";
        return this.whatsappError ? `${title}: ${this.whatsappError}` : title;
    },
    get waCanRetry() {
        return ["failed", "dropped"].includes(this.whatsappStatus);
    },
});
