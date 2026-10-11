import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { lineActionRegistry } from "@odin_account_pnl/pnl/pnl_registries";

/**
 * Ways into Ask from where questions come up: "Ask about this line" in the
 * P&L (key A, beside "Explain this change") and "Ask the Ledger: …" in
 * Odoo's command palette (Ctrl+K). Both open Ask with a draft to finish, not
 * a question sent on the user's behalf.
 */

function openAsk(env, params) {
    return env.services.action.doAction({
        type: "ir.actions.client",
        tag: "odin_account_ai.ask",
        name: _t("Ask"),
        params,
    });
}

async function names(report, model, ids) {
    if (!ids?.length) {
        return [];
    }
    const records = await report.orm.read(model, ids, ["display_name"]);
    return records.map((record) => record.display_name);
}

/** What the P&L shows for a row, sent with the question as its context:
 * the line and the lines above it, the periods, its figures and the filters
 * (by name, as the tools take them). */
export async function lineContext(row, report) {
    const options = report.state.options;
    const columns = report.columns;
    const path = [];
    for (let key = row.parent; key; key = report.rowByKey(key)?.parent) {
        const parent = report.rowByKey(key);
        if (parent) {
            path.unshift(parent.name);
        }
    }
    const context = {
        line: row.name,
        code: row.code || undefined,
        under: path.length ? path : undefined,
        period: columns.filter((c) => c.group !== "comparison").map((c) => c.label).join(", "),
        comparison: columns.filter((c) => c.group === "comparison").map((c) => c.label).join(", ") || undefined,
        figures: Object.fromEntries(columns.map((c) => [c.label, row.values?.[c.key] ?? null])),
        journals: (options.journal_ids || [])
            .map((id) => report.journals.find((journal) => journal.id === id)?.name)
            .filter(Boolean),
        partners: await names(report, "res.partner", options.partner_ids),
        analytic_accounts: await names(report, "account.analytic.account", options.analytic_account_ids),
        budget: report.state.data?.budgets?.find((b) => b.id === options.budget_id)?.name,
        draft_entries_included: options.include_draft || undefined,
    };
    for (const key of ["journals", "partners", "analytic_accounts"]) {
        if (!context[key].length) {
            delete context[key];
        }
    }
    return context;
}

lineActionRegistry.add("ask", {
    label: _t("Ask about this line"),
    icon: "fa-comments-o",
    sequence: 35,
    hotkey: "a",
    isAvailable: (row) => ["line", "formula", "account", "group", "breakdown"].includes(row.type),
    async run(row, report) {
        const context = await lineContext(row, report);
        const draft = context.comparison
            ? _t("Why did %(line)s change between %(previous)s and %(period)s?", {
                  line: row.name,
                  previous: context.comparison,
                  period: context.period,
              })
            : _t("What makes up %(line)s in %(period)s?", { line: row.name, period: context.period });
        return openAsk(report.env, { draft, context });
    },
});

registry.category("command_provider").add("odin_account_ai.ask", {
    async provide(env, options = {}) {
        const text = (options.searchValue || "").trim();
        if (!text) {
            return [];
        }
        const [aiUser, reader] = await Promise.all([
            user.hasGroup("odin_account_ai.group_ai_user"),
            user.hasGroup("account.group_account_readonly"),
        ]);
        if (!aiUser || !reader) {
            return [];
        }
        return [
            {
                name: _t("Ask the Ledger: %s", text),
                action: () => openAsk(env, { draft: text }),
            },
        ];
    },
});
