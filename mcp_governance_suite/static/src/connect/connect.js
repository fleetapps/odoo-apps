/** @odoo-module **/

import { Component, markup, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

// Polling cadence for the live status card. The whole point of this screen is
// that a user pastes the URL, switches to Claude, authorises, and sees Odoo
// flip to "Connected" without touching anything — so while we are waiting for
// that first connection, poll briskly.
const POLL_FAST = 5000;
// Once connected there is nothing left to watch for at five-second resolution,
// and a screen left open in a tab would otherwise poll the server twelve times
// a minute, forever, per user.
const POLL_SLOW = 30000;

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
            fixing: false,
            savingWrites: false,
            selfTesting: false,
            selfTest: null,
            client: "claude",
        });

        onWillStart(async () => {
            // Only the first load needs the QR: it is rendered server-side and
            // the URL it encodes cannot change between two polls.
            await this.load({ withQr: true });
            // Fire and forget: the network test must never block first paint.
            this.testReachability();
        });
        onMounted(() => {
            // A backgrounded tab has nobody watching it. Stop entirely while
            // hidden, and refresh immediately on return so the first thing the
            // user sees is current rather than however stale we left it.
            this.onVisibilityChange = () => {
                if (document.hidden) {
                    this.stopPolling();
                } else {
                    this.load();
                    this.startPolling();
                }
            };
            document.addEventListener("visibilitychange", this.onVisibilityChange);
            this.startPolling();
        });
        onWillUnmount(() => {
            this.stopPolling();
            document.removeEventListener("visibilitychange", this.onVisibilityChange);
        });
    }

    // ------------------------------------------------------------- polling
    get pollDelay() {
        const status = this.state.data && this.state.data.status;
        return status && status.state === "waiting" ? POLL_FAST : POLL_SLOW;
    }

    /**
     * Chained timeouts rather than one interval, so the cadence can change the
     * moment the connection lands without tearing anything down.
     */
    startPolling() {
        this.stopPolling();
        if (document.hidden) {
            return;
        }
        this.timer = setTimeout(async () => {
            await this.load();
            this.startPolling();
        }, this.pollDelay);
    }

    stopPolling() {
        if (this.timer) {
            clearTimeout(this.timer);
            this.timer = null;
        }
    }

    async load({ withQr = false } = {}) {
        try {
            const previous = this.state.data;
            const data = await this.orm.call("mcp.connect", "get_state", [], {
                with_qr: withQr,
            });
            // A poll omits the QR key entirely; keep showing the one we have.
            if (data.qr === undefined && previous) {
                data.qr = previous.qr;
            }
            this.state.data = data;
            this.state.failed = false;
        } catch {
            // A failed poll should not blank a page that is already useful.
            this.state.failed = !this.state.data;
        } finally {
            this.state.loading = false;
        }
    }

    // --------------------------------------------------------------- views
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

    /**
     * @param {boolean} force re-probe even if a recent verdict is cached. Only
     *   an explicit re-test, or a fix that changed the address, can have
     *   changed the answer — everything else reuses the cached one.
     */
    async testReachability(force = false) {
        this.state.testing = true;
        try {
            this.state.data = await this.orm.call("mcp.connect", "test_reachability", [], {
                force,
            });
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

    /**
     * Apply a check's fix in place. Only checks that carry a `fix_method`
     * offer this, and the method re-runs the checks server-side and returns
     * the fresh state, so the row updates itself.
     */
    async runFix(check) {
        this.state.fixing = true;
        try {
            this.state.data = await this.orm.call("mcp.connect", check.fix_method, []);
            if (check.fix_retest) {
                this.notification.add(_t("Fixed. Re-testing the connection…"), {
                    type: "success",
                });
                await this.testReachability(true);
            } else {
                this.notification.add(_t("Done — the checklist below is up to date."), {
                    type: "success",
                });
            }
        } catch {
            this.notification.add(
                _t("Could not apply that fix — you may not have permission."),
                { type: "danger" }
            );
        } finally {
            this.state.fixing = false;
        }
    }

    // -------------------------------------------------- what it may do
    async setWrites(enabled) {
        this.state.savingWrites = true;
        try {
            this.state.data = await this.orm.call("mcp.connect", "set_writes", [enabled]);
            this.notification.add(
                enabled
                    ? _t("Assistants can now make changes. Reconnect each one for it to take effect.")
                    : _t("Assistants are now read-only."),
                { type: "success" }
            );
        } catch {
            this.notification.add(
                _t("Could not change that — only an AI MCP administrator can."),
                { type: "danger" }
            );
        } finally {
            this.state.savingWrites = false;
        }
    }

    // ------------------------------------------------------------ self test
    async runSelfTest() {
        this.state.selfTesting = true;
        this.state.selfTest = null;
        try {
            this.state.selfTest = await this.orm.call("mcp.connect", "run_self_test", []);
        } catch {
            this.state.selfTest = {
                ok: false,
                message: _t("The test could not be run. Reload the page and try again."),
            };
        } finally {
            this.state.selfTesting = false;
        }
    }

    // ----------------------------------------------------------- connections
    async revoke(tokenId) {
        const result = await this.orm.call("mcp.connect", "revoke", [tokenId]);
        this.state.data = result.state;
        this.notification.add(result.message, {
            type: result.ok ? "success" : "warning",
        });
    }

    async revokeAll() {
        this.state.data = await this.orm.call("mcp.connect", "revoke_all", []);
        this.notification.add(_t("All assistants disconnected."), { type: "success" });
    }
}

registry.category("actions").add("mcp_governance_suite.connect", MCPConnect);
