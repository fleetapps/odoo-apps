import { registry } from "@web/core/registry";

/**
 * Extension points of the P&L page, filled by other modules (the AI module
 * adds an "Explain" tab and line action):
 *
 * - odin_pnl_side_panels: tabs of the side panel. Each entry is
 *   { title, icon, sequence, Component, isAvailable(row) }. The component
 *   receives props { row, report, close }.
 * - odin_pnl_line_actions: items of a row's ⋯ menu. Each entry is
 *   { label, icon, sequence, isAvailable(row), run(row, report) }.
 *
 * ``report`` is the page component, so an extension can open a panel tab
 * (report.openPanel(tab, row)) or reload the figures (report.load()).
 */
export const sidePanelRegistry = registry.category("odin_pnl_side_panels");
export const lineActionRegistry = registry.category("odin_pnl_line_actions");
