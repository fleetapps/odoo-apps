/** @odoo-module **/
/**
 * Field widgets for the list of dashboards.
 *
 * ai_dashboard_title — a dashboard's name with its description under it and a
 * Draft badge beside it, behind a small icon in the dashboard's own colour.
 * The colour follows the dashboard (its kanban colour, else its id), never
 * its position in the list, so it stays the same however the list is sorted.
 *
 * ai_relative_time — "3 hours ago", "Yesterday", "12 days ago", then the date
 * itself; the exact time is in the tooltip.
 *
 * Field widget reference: https://www.odoo.com/documentation/19.0/developer/reference/frontend/javascript_reference.html
 */
import { Component } from "@odoo/owl";
import { formatDate, formatDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const { DateTime } = luxon;

export class DashboardTitleField extends Component {
    static template = "ai_dashboards.DashboardTitleField";
    static props = { ...standardFieldProps };

    get data() {
        return this.props.record.data;
    }

    get name() {
        return this.data[this.props.name] || "";
    }

    get slot() {
        return (((this.data.color || this.props.record.resId || 0) % 8) + 8) % 8 + 1;
    }

    get isDraft() {
        return this.data.state === "draft";
    }
}

registry.category("fields").add("ai_dashboard_title", {
    component: DashboardTitleField,
    displayName: _t("Dashboard name"),
    supportedTypes: ["char"],
});

function capitalise(text) {
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

export class RelativeTimeField extends Component {
    static template = "ai_dashboards.RelativeTimeField";
    static props = { ...standardFieldProps };

    get value() {
        return this.props.record.data[this.props.name];
    }

    get relative() {
        const value = this.value;
        if (!value) {
            return "";
        }
        const days = DateTime.now().startOf("day").diff(value.startOf("day"), "days").days;
        if (days < 1) {
            return capitalise(value.toRelative());
        }
        if (days < 30) {
            return capitalise(value.toRelativeCalendar({ unit: "days" }));
        }
        return formatDate(value);
    }

    get full() {
        return this.value ? formatDateTime(this.value) : "";
    }
}

registry.category("fields").add("ai_relative_time", {
    component: RelativeTimeField,
    displayName: _t("Relative time"),
    supportedTypes: ["datetime", "date"],
});
