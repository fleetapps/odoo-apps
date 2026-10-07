import { registerThreadAction } from "@mail/core/common/thread_actions";
// registers "whatsapp-lead" (sequence 5) and "whatsapp-close" (6), which these
// two are sequenced around
import "@whatsapp_connector/core/common/thread_actions";
import { _t } from "@web/core/l10n/translation";

/** Before either record-creating action: both write to the conversation's contact. */
registerThreadAction("whatsapp-link-client", {
    condition: ({ thread }) => thread?.channel_type === "whatsapp",
    icon: "fa fa-fw fa-address-book-o",
    name: _t("Link to Client"),
    async open({ store, thread }) {
        const action = await store.env.services.orm.call(
            "discuss.channel",
            "action_wa_link_client",
            [[thread.id]]
        );
        await store.env.services.action.doAction(action);
    },
    sequence: 4,
    sequenceGroup: 20,
});

/** Directly below Create Lead: the sort is a numeric subtraction, so 5.5 lands there. */
registerThreadAction("whatsapp-ticket", {
    condition: ({ thread }) =>
        thread?.channel_type === "whatsapp" && thread.wa_can_create_ticket,
    icon: "fa fa-fw fa-life-ring",
    name: _t("Create Ticket"),
    async open({ store, thread }) {
        const action = await store.env.services.orm.call(
            "discuss.channel",
            "action_wa_create_ticket",
            [[thread.id]]
        );
        await store.env.services.action.doAction(action);
    },
    sequence: 5.5,
    sequenceGroup: 20,
});
