import { registerThreadAction, threadActionsRegistry } from "@mail/core/common/thread_actions";
import "@mail/discuss/core/common/thread_actions"; // registers "invite-people"
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

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

/** §44: close a routed conversation; the customer's next message reopens it. */
registerThreadAction("whatsapp-close", {
    condition: ({ thread }) => Boolean(thread?.waCanClose),
    icon: "fa fa-fw fa-check-square-o",
    name: _t("Close Conversation"),
    async open({ store, thread }) {
        await store.env.services.orm.call("discuss.channel", "action_wa_close", [[thread.id]]);
    },
    sequence: 6,
    sequenceGroup: 20,
});

/** R30: only WhatsApp managers add people to a conversation assigned by Lead Routing. */
patch(threadActionsRegistry.get("invite-people"), {
    condition({ thread }) {
        if (thread?.channel_type === "whatsapp" && thread.wa_routed && !thread.wa_can_manage) {
            return false;
        }
        return super.condition(...arguments);
    },
});
