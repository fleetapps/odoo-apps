/** @odoo-module **/
/* Cosmetic frontend companion (server side is authoritative).
 * Owl components: https://www.odoo.com/documentation/19.0/developer/reference/frontend/owl_components.html
 * Registries:     https://www.odoo.com/documentation/19.0/developer/reference/frontend/registries.html
 * TODO(build): patch list/form cog menus to drop Export when block_export
 * applies; load rules once via a light RPC and cache in this service. */
import { registry } from "@web/core/registry";

const accessManagerService = {
    start() {
        return { rules: null };
    },
};
registry.category("services").add("access_manager_pro", accessManagerService);
