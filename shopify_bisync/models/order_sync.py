# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Order import + customer upsert (spec A2).

Idempotency: ``client_order_ref = SHOPIFY/<order_number>`` unique per
company; webhook redeliveries and backfill overlap are no-ops.

Line resolution ladder: binding by variant id -> SKU -> barcode ->
configurable fallback product + mismatch log entry. A line is NEVER dropped.

Tax policy per instance:
- ``odoo``   : fiscal positions decide; Shopify tax lines are archived in a
               chatter note;
- ``shopify``: lines carry no tax; a tax line and a rounding line (both on
               the adjustment product) force ``amount_total`` to equal
               Shopify's ``total_price`` to the cent.

Order edits (orders/updated): quantity diffs on un-delivered lines are
applied; anything touching a delivered line raises an activity for a human -
delivered orders are never silently mutated. Cancellations cancel the SO
only while nothing shipped.
"""
import json
import logging

from odoo import _, api, fields, models
from odoo.tools import float_compare, float_round

from .product_sync import parse_shopify_dt

_logger = logging.getLogger(__name__)

CONFIRMABLE = ("paid", "partially_paid")


class OrderSync(models.AbstractModel):
    _name = "shopify.bisync.order.sync"
    _description = "Order/Customer Sync Engine"

    @api.model
    def process_job(self, job):
        payload = json.loads(job.payload_json or "{}")
        if job.kind == "customer":
            self._update_customer(job.instance_id, payload)
        else:
            self._import_order(job.instance_id, payload)

    # ----------------------------------------------------------- customers --
    @api.model
    def _country_state(self, addr):
        country = self.env["res.country"].search(
            [("code", "=", (addr.get("country_code") or "").upper())], limit=1)
        state = self.env["res.country.state"]
        if country and addr.get("province_code"):
            state = state.search(
                [("country_id", "=", country.id),
                 ("code", "=", addr["province_code"])], limit=1)
        if country and not state and addr.get("province"):
            state = state.search(
                [("country_id", "=", country.id),
                 ("name", "=ilike", addr["province"])], limit=1)
        return country, state

    @api.model
    def _address_vals(self, addr):
        country, state = self._country_state(addr or {})
        addr = addr or {}
        return {
            "street": addr.get("address1") or False,
            "street2": addr.get("address2") or False,
            "city": addr.get("city") or False,
            "zip": addr.get("zip") or False,
            "country_id": country.id or False,
            "state_id": state.id or False,
            "phone": addr.get("phone") or False,
        }

    @api.model
    def _upsert_customer(self, instance, customer, fallback_name=None):
        """Email-deduplicated partner upsert. Marketing consent fields are
        deliberately NOT synced (privacy: they never leave Shopify)."""
        Partner = self.env["res.partner"]
        Binding = self.env["shopify.bisync.binding"]
        email = (customer.get("email") or "").strip().lower() or False
        partner = Partner
        if customer.get("id"):
            binding = Binding.get(self.env, instance, "res.partner",
                                  external_id=customer["id"])
            partner = binding and binding.resolve() or Partner
        if not partner and email:
            partner = Partner.search([("email", "=ilike", email)], limit=1)
        name = (" ".join(filter(None, (customer.get("first_name"),
                                       customer.get("last_name")))).strip()
                or email or fallback_name or _("Shopify Guest"))
        vals = {"name": name, "email": email,
                "phone": customer.get("phone") or partner.phone or False}
        vals.update(self._address_vals(customer.get("default_address")))
        if partner:
            # Only fill blanks on existing partners; Shopify never truncates
            # data a user enriched in Odoo.
            partner.write({k: v for k, v in vals.items()
                           if v and not partner[k]})
        else:
            partner = Partner.create(vals)
        if customer.get("id") and not Binding.get(
                self.env, instance, "res.partner", record=partner):
            Binding.create({
                "instance_id": instance.id, "res_model": "res.partner",
                "res_id": partner.id, "external_id": str(customer["id"])})
        self._apply_partner_tags(partner, customer)
        return partner

    @api.model
    def _apply_partner_tags(self, partner, customer):
        """Map Shopify customer tags onto res.partner categories (link, never
        remove tags an Odoo user added)."""
        names = self._split_tags(customer.get("tags"))
        if not names:
            return
        Category = self.env["res.partner.category"]
        commands = []
        for name in names:
            category = (Category.search([("name", "=ilike", name)], limit=1)
                        or Category.create({"name": name}))
            commands.append(fields.Command.link(category.id))
        partner.category_id = commands

    @api.model
    def _update_customer(self, instance, customer):
        """customers/update webhook: only touches partners we already know
        (imported with an order); it never mass-imports the customer base."""
        Binding = self.env["shopify.bisync.binding"]
        known = Binding.get(self.env, instance, "res.partner",
                            external_id=customer.get("id"))
        email = (customer.get("email") or "").strip().lower()
        if not known and email and not self.env["res.partner"].search_count(
                [("email", "=ilike", email)], limit=1):
            return
        self._upsert_customer(instance, customer)

    @api.model
    def _order_address_partner(self, main_partner, addr, addr_type):
        """Child partner (invoice/delivery) under the commercial hierarchy,
        deduplicated on street+zip+city."""
        if not addr:
            return main_partner
        vals = self._address_vals(addr)
        parent = main_partner.commercial_partner_id
        for candidate in (main_partner | parent | parent.child_ids):
            if ((candidate.street or False) == vals["street"]
                    and (candidate.zip or False) == vals["zip"]
                    and (candidate.city or False) == vals["city"]):
                return candidate
        return self.env["res.partner"].create({
            "name": addr.get("name") or main_partner.name,
            "parent_id": parent.id, "type": addr_type, **vals})

    # ------------------------------------------------------ line resolution --
    @api.model
    def _resolve_line_product(self, instance, li, order_ref):
        """The ladder: variant binding -> SKU -> barcode -> fallback product.
        Returns (product, matched_flag). Never returns an empty product when
        the instance is configured correctly; logs a mismatch otherwise."""
        Binding = self.env["shopify.bisync.binding"]
        Product = self.env["product.product"]
        company_domain = ["|", ("company_id", "=", False),
                          ("company_id", "=", instance.company_id.id)]
        product = Product
        if li.get("variant_id"):
            binding = Binding.get(self.env, instance, "product.product",
                                  external_id=li["variant_id"])
            product = binding and binding.resolve() or Product
        if not product and li.get("sku"):
            product = Product.search(
                [("default_code", "=", li["sku"])] + company_domain, limit=1)
        if not product and li.get("barcode"):
            product = Product.search(
                [("barcode", "=", li["barcode"])] + company_domain, limit=1)
        if not product and li.get("sku"):
            product = Product.search(
                [("barcode", "=", li["sku"])] + company_domain, limit=1)
        if product:
            return product, True
        self.env["shopify.bisync.mismatch"].log(
            self.env, instance, "line_unmatched",
            _("No product matched line '%(title)s' (sku=%(sku)s, "
              "variant=%(variant)s) of %(ref)s; fallback product used.",
              title=li.get("title"), sku=li.get("sku"),
              variant=li.get("variant_id"), ref=order_ref),
            reference=order_ref)
        return instance.fallback_product_id, False

    # --------------------------------------------------------------- orders --
    @api.model
    def _import_order(self, instance, o):
        if instance.sync_orders != "import":
            return
        topic = o.get("_topic") or "orders/create"
        ref = f"SHOPIFY/{o.get('order_number') or o.get('id')}"
        existing = self.env["sale.order"].search(
            [("client_order_ref", "=", ref),
             ("company_id", "=", instance.company_id.id)], limit=1)
        if topic == "orders/cancelled":
            return self._cancel_order(instance, existing, o)
        if existing:
            if topic == "orders/updated":
                return self._apply_order_edits(instance, existing, o)
            return  # duplicate create webhook / backfill overlap: no-op
        self._create_order(instance, o, ref)
        instance.last_import_orders = fields.Datetime.now()

    @api.model
    def _create_order(self, instance, o, ref):
        shopify_mode = instance.tax_policy == "shopify"
        partner = self._upsert_customer(
            instance, o.get("customer") or {},
            fallback_name=(o.get("billing_address") or {}).get("name")
            or _("Shopify Guest %s", o.get("order_number", "")))
        invoice_partner = self._order_address_partner(
            partner, o.get("billing_address"), "invoice")
        shipping_partner = self._order_address_partner(
            partner, o.get("shipping_address"), "delivery")
        order_vals = {
            "partner_id": partner.id,
            "partner_invoice_id": invoice_partner.id,
            "partner_shipping_id": shipping_partner.id,
            "client_order_ref": ref,
            "company_id": instance.company_id.id,
            "warehouse_id": instance.warehouse_id.id,
            "origin": _("Shopify %s", instance.name),
            "note": o.get("note") or False,
            "shopify_bisync_instance_id": instance.id,
            "shopify_bisync_order_id": str(o.get("id") or ""),
        }
        pricelist = self._match_pricelist(instance, o, ref)
        if pricelist:
            order_vals["pricelist_id"] = pricelist.id
        if o.get("created_at"):
            order_vals["date_order"] = parse_shopify_dt(o["created_at"])
        lines, separate_discount_total = self._build_lines(instance, o, ref)
        order_vals["order_line"] = lines
        so = self.env["sale.order"].create(order_vals)
        self.env["shopify.bisync.binding"].create({
            "instance_id": instance.id, "res_model": "sale.order",
            "res_id": so.id, "external_id": str(o.get("id") or ref)})
        if separate_discount_total:
            self._add_extra_line(
                so, instance.discount_product_id, _("Shopify Discount"),
                -separate_discount_total, shopify_mode)
        self._add_shipping(instance, so, o, shopify_mode)
        self._add_tips_and_duties(instance, so, o, shopify_mode)
        if shopify_mode:
            self._reconcile_totals(instance, so, o)
        else:
            self._itemize_taxes(instance, so, o)
        self._post_tax_note(so, o)
        self._apply_order_tags(so, o)
        self._flag_risk(instance, so, o)
        self._apply_financial_gating(instance, so, o)
        self._apply_embedded_fulfillments(instance, so, o)
        return so

    # ------------------------------------------------------ tags / tips ------
    @staticmethod
    def _split_tags(value):
        """Shopify tags are a single comma-separated string."""
        return [t.strip() for t in (value or "").split(",") if t.strip()]

    @api.model
    def _apply_order_tags(self, so, o):
        names = self._split_tags(o.get("tags"))
        if not names:
            return
        Tag = self.env["crm.tag"]
        commands = []
        for name in names:
            tag = (Tag.search([("name", "=ilike", name)], limit=1)
                   or Tag.create({"name": name}))
            commands.append(fields.Command.link(tag.id))
        so.tag_ids = commands

    @api.model
    def _add_tips_and_duties(self, instance, so, o, shopify_mode):
        tip = float(o.get("total_tip_received") or 0)
        if tip and instance.tip_product_id:
            self._add_extra_line(so, instance.tip_product_id, _("Tip"),
                                 tip, shopify_mode)
        duties = self._order_duties(o)
        if duties and instance.duties_product_id:
            self._add_extra_line(so, instance.duties_product_id, _("Duties"),
                                 duties, shopify_mode)

    @staticmethod
    def _order_duties(o):
        dset = (o.get("current_total_duties_set")
                or o.get("total_duties_set") or {})
        money = (dset.get("shop_money") or {}) if isinstance(dset, dict) else {}
        return float(money.get("amount") or 0)

    @api.model
    def _itemize_taxes(self, instance, so, o):
        """'Odoo computes' + itemize on: add each Shopify tax line as its own
        tax-free line (destination fees like the Colorado Retail Delivery Fee
        that Odoo fiscal positions do not model)."""
        if not instance.itemize_taxes:
            return
        for tax_line in o.get("tax_lines") or []:
            amount = float(tax_line.get("price") or 0)
            if amount:
                line = self._add_extra_line(
                    so, instance.adjustment_product_id,
                    tax_line.get("title") or _("Tax"), amount,
                    shopify_mode=False)
                line.tax_ids = [fields.Command.clear()]

    @api.model
    def _apply_embedded_fulfillments(self, instance, so, o):
        """Reflect fulfillments carried on the order payload (already-shipped
        orders imported via create webhook or backfill)."""
        if not instance.import_fulfillment_status:
            return
        engine = self.env["shopify.bisync.fulfillment.sync"]
        for fulfillment in o.get("fulfillments") or []:
            engine._apply_fulfillment(instance, so, fulfillment)

    @api.model
    def _match_pricelist(self, instance, o, ref):
        code = o.get("currency")
        default = instance.pricelist_id
        if not code or (default and default.currency_id.name == code):
            return default
        pricelist = self.env["product.pricelist"].search(
            [("currency_id.name", "=", code),
             "|", ("company_id", "=", False),
             ("company_id", "=", instance.company_id.id)], limit=1)
        if not pricelist:
            self.env["shopify.bisync.mismatch"].log(
                self.env, instance, "currency_unmapped",
                _("No pricelist in currency %(code)s for %(ref)s; company "
                  "default used - totals will differ.", code=code, ref=ref),
                reference=ref)
        return pricelist or default

    @api.model
    def _build_lines(self, instance, o, ref):
        """Sale order line commands per tax/discount policy. Returns
        (commands, separate_discount_total)."""
        shopify_mode = instance.tax_policy == "shopify"
        per_line_discount = instance.discount_policy == "line"
        commands, separate_total = [], 0.0
        for li in o.get("line_items") or []:
            product, matched = self._resolve_line_product(instance, li, ref)
            if not product:
                raise ValueError(
                    "No fallback product configured on instance "
                    f"{instance.name} - cannot import unmatched line.")
            qty = float(li.get("quantity") or 1)
            unit_price = float(li.get("price") or 0)
            discount_total = sum(
                float(a.get("amount") or 0)
                for a in li.get("discount_allocations") or [])
            vals = {
                "product_id": product.id,
                "product_uom_qty": qty,
                "price_unit": unit_price,
                "name": product.display_name,
                "shopify_bisync_line_id": str(li.get("id") or ""),
            }
            if not matched:
                # Keep the Shopify title visible so the human can fix it.
                vals["name"] = _("[UNMATCHED] %(title)s (SKU %(sku)s)",
                                 title=li.get("title") or "?",
                                 sku=li.get("sku") or "-")
            if discount_total:
                if per_line_discount and unit_price and qty:
                    vals["discount"] = float_round(
                        100.0 * discount_total / (unit_price * qty),
                        precision_digits=2)
                else:
                    separate_total += discount_total
            if shopify_mode:
                vals["tax_ids"] = [fields.Command.clear()]
            commands.append(fields.Command.create(vals))
        return commands, separate_total

    @api.model
    def _add_extra_line(self, so, product, name, amount, shopify_mode):
        vals = {"order_id": so.id, "product_id": product.id, "name": name,
                "product_uom_qty": 1, "price_unit": amount}
        if shopify_mode:
            vals["tax_ids"] = [fields.Command.clear()]
        return self.env["sale.order.line"].create(vals)

    @api.model
    def _add_shipping(self, instance, so, o, shopify_mode):
        shipping_lines = o.get("shipping_lines") or []
        if not shipping_lines:
            return
        carrier = self.env["shopify.bisync.carrier.map"].match(
            instance, shipping_lines[0])
        amount = sum(float(s.get("discounted_price") or s.get("price") or 0)
                     for s in shipping_lines)
        if carrier:
            so.set_delivery_line(carrier, amount)
            if shopify_mode:
                so.order_line.filtered("is_delivery").write(
                    {"tax_ids": [fields.Command.clear()]})
        elif amount:
            self._add_extra_line(
                so, instance.adjustment_product_id,
                shipping_lines[0].get("title") or _("Shipping"),
                amount, shopify_mode)

    @api.model
    def _reconcile_totals(self, instance, so, o):
        """'Shopify amounts win': add the tax total as a 0%-tax line, then a
        rounding line so amount_total == total_price to the cent."""
        tax_total = float(o.get("total_tax") or 0)
        if not o.get("taxes_included") and tax_total:
            self._add_extra_line(so, instance.adjustment_product_id,
                                 _("Shopify Taxes"), tax_total, True)
        target = float(o.get("total_price") or 0)
        diff = target - so.amount_total
        if float_compare(diff, 0.0,
                         precision_rounding=so.currency_id.rounding) != 0:
            self._add_extra_line(so, instance.adjustment_product_id,
                                 _("Shopify Rounding"), diff, True)

    @api.model
    def _post_tax_note(self, so, o):
        tax_lines = o.get("tax_lines") or []
        if tax_lines:
            so.message_post(body=_(
                "Shopify tax lines (reference): %s",
                json.dumps(tax_lines)[:1500]))

    @api.model
    def _flag_risk(self, instance, so, o):
        recommendation = o.get("risk_recommendation")
        if recommendation != "cancel":
            return
        tag = self.env["crm.tag"].search(
            [("name", "=", "Shopify: Risky")], limit=1)
        if not tag:
            tag = self.env["crm.tag"].create({"name": "Shopify: Risky"})
        so.tag_ids = [fields.Command.link(tag.id)]
        so.activity_schedule(
            "mail.mail_activity_data_todo",
            user_id=(instance.admin_user_id or self.env.user).id,
            summary=_("Shopify flagged this order for cancellation"),
            note=_("Shopify risk recommendation is 'cancel'. Review before "
                   "shipping."))

    @api.model
    def _apply_financial_gating(self, instance, so, o):
        """Per-instance policy; failures never lose the imported order."""
        if (instance.confirm_policy == "draft"
                or o.get("financial_status") not in CONFIRMABLE):
            return
        try:
            so.action_confirm()
        except Exception:  # noqa: BLE001 - keep the order, tell the human
            _logger.exception("shopify_bisync: auto-confirm failed for %s",
                              so.name)
            so.message_post(body=_("Automatic confirmation failed - order "
                                   "left as quotation."))
            return
        if (instance.confirm_policy != "confirm_invoice"
                or o.get("financial_status") != "paid"):
            return
        try:
            invoices = so._create_invoices()
            invoices.action_post()
            if instance.payment_journal_id:
                # skip_paid_push: this payment came FROM Shopify (already
                # paid there); do not push a mark-as-paid straight back.
                self.env["account.payment.register"].with_context(
                    active_model="account.move", active_ids=invoices.ids,
                    shopify_bisync_skip_paid_push=True,
                ).create({
                    "journal_id": instance.payment_journal_id.id,
                }).action_create_payments()
        except Exception:  # noqa: BLE001
            _logger.exception("shopify_bisync: auto-invoice failed for %s",
                              so.name)
            so.message_post(body=_("Automatic invoicing/payment failed - "
                                   "confirm manually."))

    # ---------------------------------------------------------- order edits --
    @api.model
    def _apply_order_edits(self, instance, so, o):
        if so.state == "cancel":
            return
        if o.get("cancelled_at"):
            return self._cancel_order(instance, so, o)
        editable = so.state in ("draft", "sent", "sale") and not so.locked
        by_shopify_id = {line.shopify_bisync_line_id: line
                         for line in so.order_line
                         if line.shopify_bisync_line_id}
        payload_ids, applied, blocked = set(), [], []
        for li in o.get("line_items") or []:
            li_id = str(li.get("id") or "")
            payload_ids.add(li_id)
            line = by_shopify_id.get(li_id)
            qty = float(li.get("quantity") or 0)
            if line is None:
                if editable:
                    ref = so.client_order_ref
                    product, matched = self._resolve_line_product(
                        instance, li, ref)
                    vals = {
                        "order_id": so.id, "product_id": product.id,
                        "product_uom_qty": qty,
                        "price_unit": float(li.get("price") or 0),
                        "shopify_bisync_line_id": li_id,
                    }
                    if instance.tax_policy == "shopify":
                        vals["tax_ids"] = [fields.Command.clear()]
                    self.env["sale.order.line"].create(vals)
                    applied.append(_("added line %s", li.get("title")))
                else:
                    blocked.append(_("new line %s", li.get("title")))
                continue
            if float_compare(line.product_uom_qty, qty,
                             precision_rounding=0.001) == 0:
                continue
            if editable and not line.qty_delivered:
                applied.append(_("%(product)s: qty %(old)s → %(new)s",
                                 product=line.product_id.display_name,
                                 old=line.product_uom_qty, new=qty))
                line.product_uom_qty = qty
            else:
                blocked.append(_("%(product)s: qty %(old)s → %(new)s",
                                 product=line.product_id.display_name,
                                 old=line.product_uom_qty, new=qty))
        for line in so.order_line:
            if (line.shopify_bisync_line_id
                    and line.shopify_bisync_line_id not in payload_ids):
                if editable and not line.qty_delivered:
                    applied.append(_("removed %s",
                                     line.product_id.display_name))
                    line.product_uom_qty = 0
                else:
                    blocked.append(_("removal of %s",
                                     line.product_id.display_name))
        if applied:
            so.message_post(body=_(
                "Shopify order edit applied: %s", "; ".join(applied)))
        if blocked:
            # Delivered lines are never silently mutated: human decides.
            self.env["shopify.bisync.mismatch"].log(
                self.env, instance, "edit_delivered",
                _("Order edit touches delivered/locked lines on %(order)s: "
                  "%(changes)s", order=so.name, changes="; ".join(blocked)),
                reference=so.client_order_ref, sale_order=so)
            so.activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=(instance.admin_user_id or self.env.user).id,
                summary=_("Shopify edit needs review"),
                note=_("Shopify changed lines that are already delivered or "
                       "locked: %s", "; ".join(blocked)))

    @api.model
    def _cancel_order(self, instance, so, o):
        if not so or so.state == "cancel":
            return
        shipped = any(p.state == "done" for p in so.picking_ids)
        if shipped:
            so.activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=(instance.admin_user_id or self.env.user).id,
                summary=_("Shopify cancelled a shipped order"),
                note=_("Order %s was cancelled on Shopify but stock already "
                       "moved. Handle the return / credit manually.", so.name))
            return
        so._action_cancel()
        so.message_post(body=_("Cancelled from Shopify (%s).",
                               o.get("cancel_reason") or "no reason given"))
