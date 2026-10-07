import { registerThreadAction } from "@mail/core/common/thread_actions";
// registers "whatsapp-lead" (sequence 5) and "whatsapp-close" (6), which these
// two are sequenced around
import "@whatsapp_connector/core/common/thread_actions";
import { _t } from "@web/core/l10n/translation";

/** One click for "I will handle it"; the server refuses if someone got there first. */
registerThreadAction("whatsapp-take", {
    condition: ({ thread }) => Boolean(thread?.waCanTake),
    icon: "fa fa-fw fa-hand-paper-o",
    name: _t("Take"),
    async open({ store, thread }) {
        const action = await store.env.services.orm.call(
            "discuss.channel",
            "action_wa_take",
            [[thread.id]]
        );
        if (action) {
            await store.env.services.action.doAction(action);
        }
    },
    sequence: 4.4,
    sequenceGroup: 20,
});

/**
 * Hand it to a colleague. Not manager-gated: whoever reads a conversation is the
 * one who knows who should answer it.
 */
registerThreadAction("whatsapp-assign", {
    condition: ({ thread }) => thread?.channel_type === "whatsapp" && thread.wa_routed,
    icon: "fa fa-fw fa-exchange",
    name: _t("Assign"),
    async open({ store, thread }) {
        const action = await store.env.services.orm.call(
            "discuss.channel",
            "action_wa_assign_user",
            [[thread.id]]
        );
        await store.env.services.action.doAction(action);
    },
    sequence: 4.6,
    sequenceGroup: 20,
});

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

/**
 * "Ring me tomorrow" is most of what arrives, and it needs a person and a date,
 * not a ticket with stages. Sits after the two actions that create a record, so
 * the group reads: fix who -> sell -> track a request -> remind me -> done.
 */
registerThreadAction("whatsapp-call", {
    condition: ({ thread }) => thread?.channel_type === "whatsapp",
    icon: "fa fa-fw fa-phone",
    name: _t("Schedule Call"),
    async open({ store, thread }) {
        const action = await store.env.services.orm.call(
            "discuss.channel",
            "action_wa_schedule_call",
            [[thread.id]]
        );
        await store.env.services.action.doAction(action);
    },
    sequence: 5.75,
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

/**
 * Spam or a wrong number. Loses the lead with a reason, stops the contact opening
 * another one, archives it when WhatsApp was all that ever created it, and closes
 * the conversation. All of it reversible, which is why it is one click.
 */
registerThreadAction("whatsapp-junk", {
    condition: ({ thread }) => thread?.channel_type === "whatsapp",
    icon: "fa fa-fw fa-ban",
    name: _t("Mark as Junk"),
    async open({ store, thread }) {
        await store.env.services.orm.call("discuss.channel", "action_wa_mark_junk", [
            [thread.id],
        ]);
    },
    sequence: 6.5,
    sequenceGroup: 20,
});
