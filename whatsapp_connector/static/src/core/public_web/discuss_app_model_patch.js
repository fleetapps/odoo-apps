import { fields } from "@mail/core/common/record";
import { DiscussApp } from "@mail/core/public_web/discuss_app_model";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

/** A "WhatsApp" category in the Discuss sidebar (SPEC.md §19, R8). */
patch(DiscussApp.prototype, {
    setup(env) {
        super.setup(...arguments);
        this.whatsappCategory = fields.One("DiscussAppCategory", {
            compute() {
                return {
                    extraClass: "o-whatsapp-DiscussSidebarCategory",
                    hideWhenEmpty: true,
                    icon: "fa fa-whatsapp",
                    id: "whatsapp_connector.category",
                    name: _t("WhatsApp"),
                    sequence: 22,
                };
            },
            eager: true,
        });
    },
});
