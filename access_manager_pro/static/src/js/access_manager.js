/** @odoo-module **/
/**
 * Access Manager Pro - client companion.
 *
 * The server is authoritative; this service only reflects the *global*
 * (all-model) UI switches that cannot be expressed in a view arch, by toggling
 * `<body>` classes that the stylesheet (access_manager.scss) acts on.
 *
 * It deliberately avoids patching any mail/form OWL component: those module
 * paths move between Odoo 17, 18 and 19, and a failed static import would take
 * down the whole backend bundle. Reading `session.access_manager` and adding a
 * few CSS classes works identically on every supported version.
 *
 * Per-model chatter hiding is handled server-side instead: `get_view` adds the
 * same classes directly onto that form's `<chatter/>` node.
 */
import { registry } from "@web/core/registry";
import { session } from "@web/session";

const BODY_FLAGS = {
    is_readonly_user: "o_am_readonly",
    hide_add_property: "o_am_hide_add_property",
    hide_spreadsheet: "o_am_hide_spreadsheet",
    hide_favourites: "o_am_hide_favourites",
};

const CHATTER_FLAGS = {
    message: "o_am_hide_message",
    note: "o_am_hide_note",
    activity: "o_am_hide_activity",
    followers: "o_am_hide_followers",
};

export const accessManagerService = {
    start() {
        const config = session.access_manager;
        if (!config) {
            return {};
        }
        const { classList } = document.body;
        for (const [flag, className] of Object.entries(BODY_FLAGS)) {
            if (config[flag]) {
                classList.add(className);
            }
        }
        const chatter = config.chatter || {};
        for (const [flag, className] of Object.entries(CHATTER_FLAGS)) {
            if (chatter[flag]) {
                classList.add(className);
            }
        }
        return { config };
    },
};

registry.category("services").add("access_manager_pro", accessManagerService);
