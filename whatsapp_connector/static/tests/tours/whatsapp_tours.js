import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("whatsapp_connector_discuss_reply", {
    steps: () => [
        { trigger: ".o-whatsapp-DiscussSidebarCategory" },
        { trigger: ".o-mail-DiscussSidebarChannel:contains('Sheena Nelson') img[src*='/web/image/res.partner/']" },
        { trigger: ".o-whatsapp-headerInfo:contains('+16505551234')" },
        // Mode A: anyone may invite (the routed tour checks it is hidden there)
        { trigger: ".o-mail-DiscussContent-header [name='invite-people']" },
        { trigger: ".o-whatsapp-window:contains('window closes in')" },
        { trigger: ".o-mail-Composer-input", run: "edit Hello from Discuss" },
        { trigger: ".o-mail-Composer-input", run: "press Enter" },
        { trigger: ".o-mail-Message:contains('Hello from Discuss') .o-whatsapp-MessageStatus .fa-clock-o" },
    ],
});

registry.category("web_tour.tours").add("whatsapp_connector_discuss_locked", {
    steps: () => [
        { trigger: ".o-whatsapp-ComposerLock" },
        { trigger: "body:not(:has(.o-mail-Composer-input))" },
        { trigger: ".o-whatsapp-ComposerLock button:contains('Send Template')", run: "click" },
        { trigger: ".modal .o_field_widget[name='template_id'] input", run: "edit Reconnect" },
        { trigger: ".o-autocomplete--dropdown-item:contains('Reconnect')", run: "click" },
        { trigger: ".modal .o_field_widget[name='preview_html'] .o-whatsapp-bubble:contains('Hi Sheena Nelson')" },
        { trigger: ".modal button[name='action_send']", run: "click" },
        { trigger: "body:not(:has(.modal))" },
    ],
});

registry.category("web_tour.tours").add("whatsapp_connector_chatter", {
    steps: () => [
        { trigger: ".o-whatsapp-ChatterButton", run: "click" },
        { trigger: ".modal .o_field_widget[name='template_id'] input", run: "edit Quote" },
        { trigger: ".o-autocomplete--dropdown-item:contains('Quote follow-up')", run: "click" },
        { trigger: ".modal .o_field_widget[name='preview_html'] .o-whatsapp-bubble:contains('Hello Sheena Nelson')" },
        { trigger: ".modal button[name='action_send']", run: "click" },
        { trigger: "body:not(:has(.modal))" },
    ],
});

registry.category("web_tour.tours").add("whatsapp_connector_retry", {
    steps: () => [
        {
            trigger: ".o-mail-Message:contains('Did not arrive') .o-whatsapp-MessageStatus .fa-exclamation-circle",
        },
        { trigger: ".o-mail-Message:contains('Did not arrive') .o-whatsapp-retry", run: "click" },
        { trigger: ".o-mail-Message:contains('Did not arrive') .o-whatsapp-MessageStatus .fa-clock-o" },
    ],
});

registry.category("web_tour.tours").add("whatsapp_connector_create_lead", {
    steps: () => [
        { trigger: ".o-mail-DiscussContent-header [name='whatsapp-lead']", run: "click" },
        { trigger: ".o_last_breadcrumb_item:contains('WhatsApp — Sheena Nelson')" },
    ],
});

registry.category("web_tour.tours").add("whatsapp_connector_routed", {
    steps: () => [
        { trigger: ".o-whatsapp-owner:contains('Assigned to Andrew')" },
        // R30: a salesperson cannot invite people into a routed conversation
        { trigger: ".o-mail-DiscussContent-header:not(:has([name='invite-people']))" },
        { trigger: ".o-mail-DiscussContent-header [name='whatsapp-close']", run: "click" },
        { trigger: ".o-mail-NotificationMessage:contains('Conversation closed by Andrew')" },
    ],
});
