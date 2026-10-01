import { registerThreadAction } from "@mail/core/common/thread_actions";
import { _t } from "@web/core/l10n/translation";

/** [D8] Create Lead (or open the linked one) from a WhatsApp conversation (SPEC.md §13). */
registerThreadAction("whatsapp-lead", {
    condition: ({ thread }) => thread?.channel_type === "whatsapp" && thread.wa_can_create_lead,
    icon: "fa fa-fw fa-star-o",
    name: ({ thread }) => (thread.wa_lead_id ? _t("Open Lead") : _t("Create Lead")),
    async open({ store, thread }) {
        const action = await store.env.services.orm.call(
            "discuss.channel",
            "action_wa_create_lead",
            [[thread.id]]
        );
        await store.env.services.action.doAction(action);
    },
    sequence: 5,
    sequenceGroup: 20,
});
