from odoo import _, api, models
from odoo.tools.misc import formatLang


class SaleOrder(models.Model):
    _inherit = "sale.order"

    @api.depends("name", "sale_order_template_id", "amount_total")
    @api.depends_context("odin_plan_label")
    def _compute_display_name(self):
        """``APA Jamii Plus – Family Cover (KES 500,000) · KES 47,862.24 · S00022``
        where a plan is being picked: an order number alone does not tell a
        salesperson which plan the client said yes to."""
        if not self.env.context.get("odin_plan_label"):
            return super()._compute_display_name()
        for order in self:
            order.display_name = " · ".join(filter(None, [
                order._odin_plan_label(),
                formatLang(self.env, order.amount_total, currency_obj=order.currency_id),
                order.name,
            ]))

    # -- how a quoted plan is named; motor quotes name theirs by rate ------

    def _odin_plan_label(self):
        """``APA Jamii Plus – Family Cover (KES 500,000)``: the plan quoted."""
        self.ensure_one()
        template = self.sale_order_template_id
        return template._odin_client_title() if template else ""

    def _odin_insurer_label(self):
        self.ensure_one()
        template = self.sale_order_template_id
        return template._odin_insurer_name() if template else ""

    def _odin_plan_name(self):
        """``J Care · Premier (KES 1,000,000)``: the product and plan, as a
        column of the quotation heads them under the insurer's name."""
        self.ensure_one()
        template = self.sale_order_template_id
        if not template:
            return ""
        _insurer, product, plan = template._odin_display_names()
        return " · ".join(part for part in (product, plan) if part) or template.name

    def _odin_cover_limit(self):
        """The plan's hospital cover: the limit of its inpatient cover, 0 for
        a plan without one."""
        self.ensure_one()
        inpatient = next((section for section in self._client_sections()
                          if section["key"] == "inpatient" and section["included"]), None)
        if not inpatient:
            return 0.0
        return inpatient["limit"] or max(
            (entry["line"].product_id.odin_rate_limit for entry in inpatient["included"]),
            default=0.0)

    def _odin_client_heading(self):
        """``(insurer, cover, product)``: the plan as a client reads it, the
        insurer and the hospital cover first, the insurer's own names for the
        product and plan under them -- ``Jubilee Health Insurance``,
        ``1000000.0``, ``J Care · Premier``. A cover of 0 for anything but a
        medical plan, which its name describes."""
        self.ensure_one()
        template = self.sale_order_template_id
        if not template or not template.odin_medical:
            return self._odin_insurer_label(), 0.0, self._odin_plan_name()
        cover = self._odin_cover_limit()
        insurer, product = template._odin_display_parts(cover)
        return insurer, cover, product

    def _odin_plan_text(self):
        """The plan in a message to the client: ``Jubilee Health Insurance –
        KES 1,000,000 hospital cover (J Care · Premier)``."""
        self.ensure_one()
        insurer, cover, product = self._odin_client_heading()
        if not cover:
            return self._odin_plan_label() or self.name
        text = _("%(insurer)s – %(currency)s %(cover)s hospital cover",
                 insurer=insurer, currency=self.currency_id.name,
                 cover=formatLang(self.env, cover, digits=0))
        return _("%(plan)s (%(product)s)", plan=text, product=product) if product else text

    # -- what a renewal needs to know about what was sold ----------------------

    def _odin_policy_months(self):
        """How long the cover sold runs: medical plans are annual."""
        self.ensure_one()
        return 12

    def _odin_insurer_partner(self):
        self.ensure_one()
        return self.sale_order_template_id.odin_insurer_id

    def _odin_cover_product(self):
        self.ensure_one()
        template = self.sale_order_template_id
        if not template:
            return self.env["odin.cover.product"]
        return self.env["odin.cover.product"].with_context(active_test=False).search(
            [("template_id", "=", template.id)], limit=1)

    def _action_cancel(self):
        """A cancelled order renews nothing."""
        result = super()._action_cancel()
        self.env["odin.renewal"].sudo().search([
            ("order_id", "in", self.ids), ("state", "in", ("upcoming", "in_progress")),
        ]).write({"state": "cancelled"})
        return result

    def _action_confirm(self):
        """A plan of a medical quote confirmed is the client's choice, however
        it was confirmed -- Client accepted, Confirm here, or signed on the
        portal -- so the quote, its other plans and the opportunity follow.

        In sudo: the portal confirms as the public user, and a salesperson who
        may confirm the order may also close out the quote it came from."""
        result = super()._action_confirm()
        comparisons = self.env["odin.quote.comparison"].sudo().search([
            ("plan_ids.order_id", "in", self.ids),
            ("state", "!=", "accepted"),
        ])
        for comparison in comparisons.filtered("odin_from_builder"):
            order = (comparison.plan_ids.order_id & self)[:1]
            comparison._odin_accept(order.sudo())
        return result
