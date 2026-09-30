import { reactive } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { ConnectionLostError, RPCError } from "@web/core/network/rpc";
import { user } from "@web/core/user";

const MODEL = "odin.bar.desk";
const RETRY_DELAY = 15000;

/** Random UUID v4, also on plain http where crypto.randomUUID is missing. */
export function newUuid() {
    const crypto = globalThis.crypto;
    if (crypto.randomUUID) {
        return crypto.randomUUID();
    }
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function readJSON(storage, key, fallback) {
    try {
        const raw = storage.getItem(key);
        return raw ? JSON.parse(raw) : fallback;
    } catch {
        return fallback;
    }
}

function writeJSON(storage, key, value) {
    try {
        if (value === undefined) {
            storage.removeItem(key);
        } else {
            storage.setItem(key, JSON.stringify(value));
        }
    } catch {
        // Private mode or full storage: the Desk still works, just without the local copy.
    }
}

export function errorName(error) {
    return (error instanceof RPCError && error.data?.name) || "";
}

/** Worth retrying later: no connection, or the server asked to try again. */
export function isRetryable(error) {
    return (
        error instanceof ConnectionLostError || errorName(error).endsWith(".ConcurrencyError")
    );
}

export function isSessionError(error) {
    return errorName(error).endsWith("DeskSessionError");
}

export function errorMessage(error) {
    return (error instanceof RPCError && error.data?.message) || error?.message || String(error);
}

let audioContext = null;

/** A short beep and buzz, so staff need not look at the screen to know it worked. */
export function feedback(ok) {
    try {
        browser.navigator.vibrate?.(ok ? 40 : [90, 60, 90]);
        audioContext = audioContext || new (browser.AudioContext || browser.webkitAudioContext)();
        const oscillator = audioContext.createOscillator();
        const gain = audioContext.createGain();
        oscillator.frequency.value = ok ? 880 : 220;
        gain.gain.value = 0.06;
        oscillator.connect(gain);
        gain.connect(audioContext.destination);
        oscillator.start();
        oscillator.stop(audioContext.currentTime + (ok ? 0.08 : 0.3));
    } catch {
        // Sound and vibration are extras.
    }
}

/**
 * State and server calls of the Bar Desk.
 *
 * Every action that posts stock goes through the outbox: it gets a request
 * id, is kept on the device until the server has answered, and is sent again
 * when the connection comes back. The server posts each id once, so resending
 * is always safe.
 */
export class DeskModel {
    constructor(orm, notify) {
        this.orm = orm;
        this.notify = notify;
        this.productsById = new Map();
        this.outboxKey = `odin_bar_desk.outbox.${user.userId}`;
        this.sessionKey = `odin_bar_desk.session.${user.userId}`;
        this.retryTimer = null;
        this.state = reactive({
            booting: true,
            fatal: "",
            userName: "",
            isManager: false,
            bars: [],
            bar: null,
            employees: [],
            employee: null,
            token: null,
            catalog: null,
            home: null,
            outbox: readJSON(browser.localStorage, this.outboxKey, []),
            sending: false,
        });
        this.onOnline = () => this.flushOutbox();
        browser.addEventListener("online", this.onOnline);
    }

    destroy() {
        browser.removeEventListener("online", this.onOnline);
        browser.clearTimeout(this.retryTimer);
    }

    get isStore() {
        return this.state.bar?.kind === "store";
    }

    call(method, kwargs = {}) {
        return this.orm.silent.call(MODEL, method, [], kwargs);
    }

    /** Call a method that needs the signed-in staff member. */
    async fetch(method, kwargs = {}) {
        try {
            return await this.call(method, {
                bar_id: this.state.bar.id,
                token: this.state.token,
                ...kwargs,
            });
        } catch (error) {
            if (isSessionError(error)) {
                this.lock();
            }
            throw error;
        }
    }

    // ------------------------------------------------------------------
    // Start-up and sign-in
    // ------------------------------------------------------------------

    async boot() {
        try {
            const boot = await this.call("desk_boot");
            Object.assign(this.state, {
                userName: boot.user_name,
                isManager: boot.is_manager,
                bars: boot.bars,
            });
            const saved = readJSON(browser.sessionStorage, this.sessionKey, {});
            const barId = boot.bars.some((bar) => bar.id === saved.barId) ? saved.barId : boot.bar_id;
            if (!barId) {
                this.state.fatal = "This login is not linked to a bar yet. Ask a manager to set its Bar Desk bar.";
                return;
            }
            await this.openBar(barId, saved.barId === barId ? saved : {});
        } catch (error) {
            this.state.fatal = errorMessage(error);
        } finally {
            this.state.booting = false;
        }
    }

    async openBar(barId, saved = {}) {
        const data = await this.call("desk_open_bar", { bar_id: barId });
        Object.assign(this.state, {
            bar: data.bar,
            employees: data.employees,
            employee: null,
            token: null,
            catalog: null,
            home: null,
        });
        writeJSON(browser.sessionStorage, this.sessionKey, { barId });
        if (saved.token) {
            // Reloading the page keeps whoever was signed in, until the token expires.
            try {
                await this.signIn(saved.token, saved.employee);
            } catch {
                this.lock();
            }
        }
    }

    async login(employeeId, pin) {
        const result = await this.call("desk_login", {
            bar_id: this.state.bar.id,
            employee_id: employeeId,
            pin: pin || false,
        });
        if (result.error) {
            return result.error;
        }
        await this.signIn(result.token, result.employee);
        this.flushOutbox();
        return false;
    }

    /** Load the bar's day and catalogue first, then switch to the signed-in
     * screens in one go, so nothing the user taps is undone by late data. */
    async signIn(token, employee) {
        const kwargs = { bar_id: this.state.bar.id, token };
        const [home, catalog] = await Promise.all([
            this.call("desk_home", kwargs),
            this.call("desk_catalog", kwargs),
        ]);
        this.productsById = new Map(catalog.products.map((product) => [product.id, product]));
        Object.assign(this.state, { home, catalog, bar: home.bar, token, employee });
        writeJSON(browser.sessionStorage, this.sessionKey, {
            barId: this.state.bar.id,
            token,
            employee,
        });
    }

    lock() {
        this.state.token = null;
        this.state.employee = null;
        writeJSON(browser.sessionStorage, this.sessionKey, { barId: this.state.bar?.id });
    }

    async loadCatalog() {
        const catalog = await this.fetch("desk_catalog");
        this.productsById = new Map(catalog.products.map((product) => [product.id, product]));
        this.state.catalog = catalog;
    }

    async loadHome() {
        this.state.home = await this.fetch("desk_home");
        this.state.bar = this.state.home.bar;
    }

    product(productId) {
        return this.productsById.get(productId);
    }

    // ------------------------------------------------------------------
    // Outbox
    // ------------------------------------------------------------------

    saveOutbox() {
        writeJSON(browser.localStorage, this.outboxKey, this.state.outbox);
    }

    /**
     * Post an action. Resolves to {status: "done", result}, {status: "queued"}
     * when there is no connection (it will be sent later), or {status:
     * "failed", message} when the server refused it.
     */
    async post(method, kwargs, label) {
        const uuid = newUuid();
        const entry = {
            uuid,
            method,
            label,
            created: Date.now(),
            employee: this.state.employee?.name,
            kwargs: { ...kwargs, uuid, bar_id: this.state.bar.id, token: this.state.token },
        };
        this.state.outbox.push(entry);
        this.saveOutbox();
        return this.send(entry);
    }

    async send(entry) {
        try {
            const result = await this.call(entry.method, entry.kwargs);
            this.dropFromOutbox(entry.uuid);
            return { status: "done", result };
        } catch (error) {
            if (isRetryable(error)) {
                this.scheduleRetry();
                return { status: "queued" };
            }
            this.dropFromOutbox(entry.uuid);
            if (isSessionError(error) && entry.kwargs.token === this.state.token) {
                this.lock();
            }
            return { status: "failed", message: errorMessage(error) };
        }
    }

    dropFromOutbox(uuid) {
        const index = this.state.outbox.findIndex((entry) => entry.uuid === uuid);
        if (index >= 0) {
            this.state.outbox.splice(index, 1);
            this.saveOutbox();
        }
    }

    scheduleRetry() {
        browser.clearTimeout(this.retryTimer);
        this.retryTimer = browser.setTimeout(() => this.flushOutbox(), RETRY_DELAY);
    }

    async flushOutbox() {
        if (this.state.sending || !this.state.outbox.length) {
            return;
        }
        this.state.sending = true;
        try {
            for (const entry of [...this.state.outbox]) {
                const outcome = await this.send(entry);
                if (outcome.status === "queued") {
                    return;
                }
                if (outcome.status === "failed") {
                    this.notify(`${entry.label}: ${outcome.message}`, "danger", true);
                } else {
                    this.notify(`${entry.label}: sent`, "success");
                }
            }
            if (this.state.token) {
                this.loadHome().catch(() => {});
            }
        } finally {
            this.state.sending = false;
        }
    }

    // ------------------------------------------------------------------
    // Count drafts kept on the device
    // ------------------------------------------------------------------

    countDraftKey(countId) {
        return `odin_bar_desk.count.${countId}`;
    }

    readCountDraft(countId) {
        return readJSON(browser.localStorage, this.countDraftKey(countId), null);
    }

    writeCountDraft(countId, draft) {
        writeJSON(browser.localStorage, this.countDraftKey(countId), draft);
    }
}
