/** @odoo-module **/

import { Component, markup, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

// Polling interval for the live status card. The whole point of this screen is
// that a user pastes the URL, switches to Claude, authorises, and sees Odoo
// flip to "Connected" without touching anything.
const POLL_MS = 5000;

export class MCPConnect extends Component {
    static template = "mcp_governance_suite.Connect";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            data: null,
            loading: true,
            failed: false,
            testing: false,
            client: "claude",
        });

        onWillStart(async () => {
            await this.load();
            // Fire and forget: the network test must never block first paint.
            this.testReachability();
        });
        onMounted(() => {
            this.timer = setInterval(() => this.load(), POLL_MS);
        });
        onWillUnmount(() => clearInterval(this.timer));
    }

    async load() {
        try {
            this.state.data = await this.orm.call("mcp.connect", "get_state", []);
            this.state.failed = false;
        } catch {
            // A failed poll should not blank a page that is already useful.
            this.state.failed = !this.state.data;
        } finally {
            this.state.loading = false;
        }
    }

    get guide() {
        const data = this.state.data;
        if (!data || !data.clients) {
            return null;
        }
        const found = data.clients.find((c) => c.key === this.state.client);
        const guide = found || data.clients[0];
        return {
            ...guide,
            // Steps are module constants containing light markup, never user input.
            steps: guide.steps.map((s) => markup(s)),
        };
    }

    get blockingChecks() {
        const checks = (this.state.data && this.state.data.checks) || [];
        return checks.filter((c) => c.state === "fail");
    }

    async copy(text, label) {
        try {
            await navigator.clipboard.writeText(text);
            this.notification.add(label || _t("Copied to clipboard"), {
                type: "success",
            });
        } catch {
            this.notification.add(
                _t("Could not copy automatically — select the text and copy it."),
                { type: "warning" }
            );
        }
    }

    async testReachability() {
        this.state.testing = true;
        try {
            this.state.data = await this.orm.call("mcp.connect", "test_reachability", []);
        } catch {
            // Keep whatever verdict we had; the card shows "not tested".
        } finally {
            this.state.testing = false;
        }
    }

    async openFix(xmlid) {
        if (xmlid) {
            await this.action.doAction(xmlid);
        }
    }

    async revoke(tokenId) {
        this.state.data = await this.orm.call("mcp.connect", "revoke", [tokenId]);
        this.notification.add(_t("Assistant disconnected."), { type: "success" });
    }

    async revokeAll() {
        this.state.data = await this.orm.call("mcp.connect", "revoke_all", []);
        this.notification.add(_t("All assistants disconnected."), { type: "success" });
    }
}

registry.category("actions").add("mcp_governance_suite.connect", MCPConnect);
