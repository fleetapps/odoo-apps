# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Product two-way sync with conflict policy (spec A1 + A3).

Odoo -> Shopify: template ``create()``/``write()`` on watched fields enqueue
an export job; the export goes through GraphQL ``productSet`` (REST product
endpoints are deprecated for new apps), images through staged uploads fed
into the same ``productSet`` call as ``files`` (``productCreateMedia`` is
deprecated on 2026-07), prices through ``productVariantsBulkUpdate``.

Duplicates (the category's most reported failure): nothing is ever created
before ``_match_existing_template`` / ``_match_shopify_product`` have looked
for the record that already is this product - SKU, then barcode, then exact
title. An ambiguous match is never resolved by guessing; it goes to the
mismatch log for a human.

Shopify -> Odoo: ``products/create|update`` webhooks and backfill pages feed
the same ``_import_product()``; GraphQL nodes are normalized to the REST
webhook shape first so there is exactly one import path.

Conflict engine (A3): fingerprints on the binding (``external_updated_at`` /
``odoo_write_date``) detect a double-sided edit; ``instance.conflict_policy``
picks the winner and EVERY resolution posts an audit message on the record:
"Conflict on {fields}: kept {side} version."
"""
import base64
import hashlib
import json
import logging

from datetime import datetime, timezone

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

SKIP_TRIGGER = "shopify_bisync_skip_trigger"

#: ``identifier`` is a sibling argument of ``input``; ``ProductSetInput.id``
#: is deprecated on 2026-07. ``media`` comes back so the image map can be
#: rebuilt from what Shopify actually stored.
PRODUCT_SET = """
mutation productSet($identifier: ProductSetIdentifiers,
                    $input: ProductSetInput!, $synchronous: Boolean!) {
  productSet(identifier: $identifier, input: $input,
             synchronous: $synchronous) {
    product {
      id
      updatedAt
      media(first: 250) {
        nodes { id ... on MediaImage { image { url } } }
      }
      variants(first: 250) {
        nodes { id sku inventoryItem { id } selectedOptions { name value } }
      }
    }
    userErrors { field message }
  }
}"""

#: Match-before-create lookups (see ``instance.product_match_policy``). Both
#: run ONLY when no binding exists, i.e. at the exact moment a duplicate
#: would otherwise be created.
VARIANT_MATCH = """
query variantMatch($query: String!) {
  productVariants(first: 3, query: $query) {
    nodes { id sku barcode product { id legacyResourceId } }
  }
}"""

PRODUCT_MATCH = """
query productMatch($query: String!) {
  products(first: 3, query: $query) {
    nodes { id legacyResourceId title }
  }
}"""

#: Current Shopify state of one product, for the dry-run diff.
PRODUCT_ONE = """
query product($id: ID!) {
  product(id: $id) {
    id legacyResourceId title descriptionHtml status updatedAt
    tags productType
    options { name position values }
    media(first: 250) { nodes { id ... on MediaImage { image { url } } } }
    variants(first: 250) {
      nodes { id legacyResourceId title sku barcode price compareAtPrice
              position selectedOptions { name value }
              inventoryItem { id measurement { weight { value unit } } } }
    }
  }
}"""

PRICE_UPDATE = """
mutation productVariantsBulkUpdate(
    $productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    userErrors { field message }
  }
}"""

STAGED_UPLOADS = """
mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets { url resourceUrl parameters { name value } }
    userErrors { field message }
  }
}"""

PUBLISHABLE_PUBLISH = """
mutation publishablePublish($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) {
    userErrors { field message }
  }
}"""

PUBLISHABLE_UNPUBLISH = """
mutation publishableUnpublish($id: ID!, $input: [PublicationInput!]!) {
  publishableUnpublish(id: $id, input: $input) {
    userErrors { field message }
  }
}"""

#: One page of the product backfill (A4) - normalized through _gql_to_dict().
PRODUCTS_PAGE = """
query products($first: Int!, $after: String) {
  products(first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id legacyResourceId title descriptionHtml status updatedAt
      tags productType
      options { name position values }
      media(first: 250) { nodes { ... on MediaImage { image { url } } } }
      variants(first: 250) {
        nodes { id legacyResourceId title sku barcode price compareAtPrice
                position selectedOptions { name value }
                inventoryItem { id measurement { weight { value unit } } } }
      }
    }
  }
}"""

WEIGHT_TO_KG = {"GRAMS": 0.001, "KILOGRAMS": 1.0,
                "POUNDS": 0.45359237, "OUNCES": 0.028349523125}


def parse_shopify_dt(value):
    """ISO-8601 with offset -> naive UTC datetime (Odoo convention)."""
    if not value:
        return False
    return (datetime.fromisoformat(str(value))
            .astimezone(timezone.utc).replace(tzinfo=None))


class ProductSync(models.AbstractModel):
    _name = "shopify.bisync.product.sync"
    _description = "Product Sync Engine"

    #: template fields whose change means "this product must be re-exported".
    WATCHED_FIELDS = ("name", "description_sale", "default_code", "barcode",
                      "active", "weight", "hs_code", "attribute_line_ids",
                      "product_tag_ids", "categ_id")
    #: variant fields with the same effect.
    WATCHED_VARIANT_FIELDS = ("default_code", "barcode", "weight")

    @api.model
    def process_job(self, job):
        payload = json.loads(job.payload_json or "{}")
        if job.kind == "price":
            self._export_prices(job.instance_id, payload)
        elif job.kind == "publish":
            self._publish_product(job.instance_id, payload)
        elif job.direction == "in":
            self._import_product(job.instance_id, payload)
        else:
            self._export_product(job.instance_id, payload)

    # ------------------------------------------------------------ normalize -
    @api.model
    def _gql_to_dict(self, node):
        """GraphQL product node -> REST-webhook-shaped dict so import has a
        single entry format."""
        images = [{"src": m["image"]["url"]}
                  for m in (node.get("media", {}).get("nodes") or [])
                  if m.get("image")]
        variants = []
        for v in node.get("variants", {}).get("nodes") or []:
            weight = ((v.get("inventoryItem") or {}).get("measurement") or {}
                      ).get("weight") or {}
            variants.append({
                "id": int(v["legacyResourceId"]),
                "sku": v.get("sku"), "barcode": v.get("barcode"),
                "price": v.get("price"),
                "compare_at_price": v.get("compareAtPrice"),
                "position": v.get("position"),
                "selected_options": v.get("selectedOptions") or [],
                "inventory_item_id": self.env["shopify.bisync.instance"]
                    .gid_to_id((v.get("inventoryItem") or {}).get("id", "")),
                "weight_kg": (float(weight.get("value", 0.0))
                              * WEIGHT_TO_KG.get(weight.get("unit"), 1.0)),
            })
        return {
            "id": int(node["legacyResourceId"]),
            "title": node.get("title"),
            "body_html": node.get("descriptionHtml"),
            "status": (node.get("status") or "active").lower(),
            "updated_at": node.get("updatedAt"),
            "tags": ", ".join(node.get("tags") or []),
            "product_type": node.get("productType") or "",
            "options": node.get("options") or [],
            "images": images,
            "variants": variants,
        }

    @staticmethod
    def _variant_option_signature(sp, variant):
        """Frozenset of (option name, value) for a Shopify variant, from
        either GraphQL ``selected_options`` or REST ``option1..3``."""
        if variant.get("selected_options"):
            pairs = [(o["name"], o["value"]) for o in variant["selected_options"]]
        else:
            names = [o["name"] for o in
                     sorted(sp.get("options") or [],
                            key=lambda o: o.get("position") or 0)]
            values = [variant.get(f"option{i}") for i in range(1, 4)]
            pairs = [(n, v) for n, v in zip(names, values) if v]
        return frozenset((n.strip().lower(), str(v).strip().lower())
                         for n, v in pairs)

    @staticmethod
    def _odoo_variant_signature(variant):
        return frozenset(
            (ptav.attribute_id.name.strip().lower(), ptav.name.strip().lower())
            for ptav in variant.product_template_attribute_value_ids)

    @staticmethod
    def _is_default_variant_only(sp):
        options = sp.get("options") or []
        return (not options
                or (len(options) == 1
                    and options[0].get("name") == "Title"
                    and (options[0].get("values") or []) == ["Default Title"]))

    @staticmethod
    def _variant_weight_kg(variant):
        if "weight_kg" in variant:
            return variant["weight_kg"]
        return float(variant.get("grams") or 0.0) / 1000.0

    # ------------------------------------------------ match before create ---
    @staticmethod
    def _search_literal(value):
        """Quote a value for a Shopify ``query:`` argument.

        Shopify's search syntax treats ``: \\ ( )`` and whitespace as
        structure, so an unquoted SKU like ``A:B (2)`` parses as something
        else entirely and quietly matches the wrong product - or nothing.
        """
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @api.model
    def _log_ambiguous(self, instance, message, reference):
        self.env["shopify.bisync.mismatch"].log(
            self.env, instance, "match_ambiguous", message, reference=reference)

    @api.model
    def _match_existing_template(self, instance, sp):
        """Find the Odoo product that already IS this Shopify product.

        Runs only when no binding exists - the exact moment the importer
        would otherwise create a duplicate. A first sync against a catalogue
        that already exists in Odoo doubling it is the single most reported
        failure of every connector in this category.

        Ambiguity is never resolved by guessing: two candidates means neither
        is adopted and the mismatch log gets the decision for a human.
        """
        Template = self.env["product.template"]
        policy = instance.product_match_policy
        if policy == "off":
            return Template
        company_domain = ["|", ("company_id", "=", False),
                          ("company_id", "=", instance.company_id.id)]
        variants = sp.get("variants") or []
        candidates = [
            ("default_code", [v["sku"] for v in variants if v.get("sku")]),
            ("barcode", [v["barcode"] for v in variants if v.get("barcode")]),
        ]
        for field_name, values in candidates:
            for value in values:
                found = self.env["product.product"].search(
                    [(field_name, "=", value)] + company_domain, limit=2)
                if len(found) > 1:
                    self._log_ambiguous(
                        instance,
                        _("Shopify product '%(title)s' matches %(n)s Odoo "
                          "products on %(field)s=%(value)s. Imported as a new "
                          "product; merge them by hand if they are the same.",
                          title=sp.get("title"), n=len(found),
                          field=field_name, value=value),
                        reference=str(sp.get("id") or ""))
                    return Template
                if found:
                    return self._adoptable(instance, found.product_tmpl_id)
        if policy == "sku_barcode_title" and sp.get("title"):
            found = Template.search(
                [("name", "=", sp["title"])] + company_domain, limit=2)
            if len(found) > 1:
                self._log_ambiguous(
                    instance,
                    _("Shopify product '%(title)s' matches %(n)s Odoo "
                      "products by title. Imported as a new product.",
                      title=sp["title"], n=len(found)),
                    reference=str(sp.get("id") or ""))
                return Template
            if found:
                return self._adoptable(instance, found)
        return Template

    @api.model
    def _adoptable(self, instance, tmpl):
        """Refuse a candidate that is already bound to another Shopify product
        on this store - adopting it would map two Shopify products onto one
        Odoo record and trip the binding's uniqueness constraint."""
        if self.env["shopify.bisync.binding"].get(
                self.env, instance, "product.template", record=tmpl):
            return self.env["product.template"]
        return tmpl

    @api.model
    def _match_shopify_product(self, instance, tmpl):
        """Mirror image of :meth:`_match_existing_template`: find the Shopify
        product this Odoo product already is, so an export adopts it instead
        of creating a second listing. Returns the numeric product id or None.
        """
        policy = instance.product_match_policy
        if policy == "off":
            return None
        variants = tmpl.product_variant_ids
        lookups = [("sku", [v.default_code for v in variants if v.default_code]),
                   ("barcode", [v.barcode for v in variants if v.barcode])]
        for field_name, values in lookups:
            for value in values:
                data = instance.graphql(VARIANT_MATCH, {
                    "query": f"{field_name}:{self._search_literal(value)}"})
                nodes = (data.get("productVariants") or {}).get("nodes") or []
                # Only trust an exact, case-sensitive hit: Shopify's search is
                # fuzzy and will happily return prefix matches.
                exact = [n for n in nodes if (n.get(field_name) or "") == value]
                products = {(n.get("product") or {}).get("legacyResourceId")
                            for n in exact}
                products.discard(None)
                if len(products) > 1:
                    self._log_ambiguous(
                        instance,
                        _("'%(name)s' matches %(n)s Shopify products on "
                          "%(field)s=%(value)s. Exported as a new product.",
                          name=tmpl.name, n=len(products),
                          field=field_name, value=value),
                        reference=value)
                    return None
                if products:
                    return str(products.pop())
        if policy == "sku_barcode_title" and tmpl.name:
            data = instance.graphql(PRODUCT_MATCH, {
                "query": f"title:{self._search_literal(tmpl.name)}"})
            nodes = (data.get("products") or {}).get("nodes") or []
            exact = [n for n in nodes if (n.get("title") or "") == tmpl.name]
            if len(exact) > 1:
                self._log_ambiguous(
                    instance,
                    _("'%(name)s' matches %(n)s Shopify products by title. "
                      "Exported as a new product.",
                      name=tmpl.name, n=len(exact)),
                    reference=tmpl.name)
                return None
            if exact:
                return str(exact[0]["legacyResourceId"])
        return None

    # -------------------------------------------------------------- import --
    @api.model
    def _import_product(self, instance, sp):
        if instance.sync_products not in ("import", "both"):
            return
        Binding = self.env["shopify.bisync.binding"]
        binding = Binding.get(self.env, instance, "product.template",
                              external_id=sp.get("id"))
        tmpl = binding and binding.resolve()
        ext_updated = parse_shopify_dt(sp.get("updated_at"))
        # Echo/no-op suppression: our own export bumps external_updated_at
        # from the productSet response, so the webhook it triggers is skipped.
        if (binding and ext_updated and binding.external_updated_at
                and ext_updated <= binding.external_updated_at):
            return
        vals = self._import_vals(instance, sp, tmpl)
        if tmpl:
            # Price is added only here, on the update path. The create path
            # sets it once from the first variant, and the adopt path must
            # never reprice a product the merchant already prices - so this
            # cannot live in _import_vals, which all three share.
            vals.update(self._import_price_vals(instance, sp, tmpl))
            winner, changed = self._resolve_conflict(
                instance, binding, tmpl, ext_updated, vals)
            if winner == "odoo":
                # Keep Odoo's version; re-export overwrites Shopify. Fast-
                # forward the external fingerprint: that revision is discarded.
                binding.external_updated_at = ext_updated
                self.env["shopify.bisync.job"].enqueue(
                    instance, "out", "product", {"res_id": tmpl.id},
                    priority=25, lock_key=f"product:{tmpl.id}")
                return
            tmpl.with_context(**{SKIP_TRIGGER: True}).write(vals)
        else:
            tmpl = self._match_existing_template(instance, sp)
            if tmpl:
                # Adopt. The product already exists in Odoo, so link it and
                # apply the Shopify state rather than creating a twin. The
                # sales price is deliberately NOT written here: adopting a
                # product the merchant already prices must not reprice it.
                tmpl.with_context(**{SKIP_TRIGGER: True}).write(vals)
                tmpl.message_post(body=_(
                    "Linked to Shopify product %(sid)s on %(store)s by "
                    "match-before-create - no duplicate was created.",
                    sid=sp.get("id"), store=instance.name))
            else:
                first = (sp.get("variants") or [{}])[0]
                vals["list_price"] = float(first.get("price") or 0.0)
                tmpl = self.env["product.template"].with_context(
                    **{SKIP_TRIGGER: True}).create(vals)
            binding = Binding.create({
                "instance_id": instance.id, "res_model": "product.template",
                "res_id": tmpl.id, "external_id": str(sp["id"])})
        binding.shopify_status = sp.get("status") or "active"
        self._import_variants(instance, sp, tmpl)
        self._import_images(instance, sp, tmpl, binding)
        # Fingerprints: store the EXPORT checksum of the freshly imported
        # state so the next export is a guaranteed no-op (zero API calls).
        payload, __, image_checksum = self._export_payload(
            instance, tmpl, binding)
        binding.write({
            "external_updated_at": ext_updated or fields.Datetime.now(),
            "odoo_write_date": tmpl.write_date,
            "checksum": self._checksum(payload, image_checksum),
            "image_checksum": image_checksum,
        })

    @api.model
    def _import_vals(self, instance, sp, tmpl):
        vals = {
            "name": sp.get("title") or (tmpl and tmpl.name) or "?",
            "description_sale": html2plaintext(sp.get("body_html") or "") or False,
            "active": (sp.get("status") or "active") != "archived",
        }
        if instance.sync_product_tags and "tags" in sp:
            vals["product_tag_ids"] = self._tag_commands(
                "product.tag", sp.get("tags"))
        if instance.sync_product_category and sp.get("product_type"):
            category = self.env["product.category"].search(
                [("name", "=ilike", sp["product_type"])], limit=1)
            if not category:
                category = self.env["product.category"].create(
                    {"name": sp["product_type"]})
            vals["categ_id"] = category.id
        if self._is_default_variant_only(sp) and sp.get("variants"):
            variant = sp["variants"][0]
            vals.update({
                "default_code": variant.get("sku") or False,
                "barcode": variant.get("barcode") or False,
                "weight": self._variant_weight_kg(variant),
            })
        return vals

    @api.model
    def _import_price_vals(self, instance, sp, tmpl):
        """Shopify's price -> Odoo's sales price, when the store imports prices.

        Odoo prices a template once and expresses variant differences as
        ``price_extra`` on the attribute values, so a Shopify product whose
        variants carry genuinely different prices has no faithful single
        value. Rather than silently flatten it, the base price is taken from
        the first variant and the divergence is logged for a human - the same
        contract the order importer honours for unmatched lines.
        """
        if instance.sync_prices not in ("import", "both"):
            return {}
        variants = sp.get("variants") or []
        if not variants:
            return {}
        prices = {float(v.get("price") or 0.0) for v in variants}
        if len(prices) > 1:
            self.env["shopify.bisync.mismatch"].log(
                self.env, instance, "variant_price_spread",
                _("'%(title)s' has variants priced differently on Shopify "
                  "(%(prices)s). Odoo keeps one sales price per product, so "
                  "%(kept)s was imported. Set the difference as an extra "
                  "price on the variant's attribute value in Odoo.",
                  title=sp.get("title") or tmpl.display_name,
                  prices=", ".join(f"{p:.2f}" for p in sorted(prices)),
                  kept=f"{float(variants[0].get('price') or 0.0):.2f}"),
                reference=sp.get("title") or str(sp.get("id") or ""))
        return {"list_price": float(variants[0].get("price") or 0.0)}

    @api.model
    def _tag_commands(self, model, tags_value):
        """Comma-separated Shopify tag string -> Command.set of tag records
        (get-or-create by name), for product.tag / res.partner.category."""
        names = [t.strip() for t in (tags_value or "").split(",") if t.strip()]
        Tag = self.env[model]
        ids = []
        for name in names:
            tag = (Tag.search([("name", "=ilike", name)], limit=1)
                   or Tag.create({"name": name}))
            ids.append(tag.id)
        return [fields.Command.set(ids)]

    @staticmethod
    def _changed_fields(tmpl, vals):
        """Names of fields whose imported value genuinely differs from Odoo's.

        Only feeds the human-readable audit message. Relational values arrive
        as ids (m2o) or Command lists (m2m); comparing those straight to the
        recordset on the record made every conflict claim that the category
        and the tags had changed, which is exactly the kind of noise that
        teaches people to ignore the log.
        """
        changed = []
        for name, new in vals.items():
            current = tmpl[name]
            field_type = tmpl._fields[name].type
            if field_type == "many2one":
                same = current.id == (new or False)
            elif field_type in ("many2many", "one2many"):
                wanted = set()
                for command in new or []:
                    if command[0] == fields.Command.SET:
                        wanted = set(command[2] or [])
                same = set(current.ids) == wanted
            else:
                same = (current or False) == (new or False)
            if not same:
                changed.append(name)
        return sorted(changed)

    @api.model
    def _resolve_conflict(self, instance, binding, tmpl, ext_updated, vals):
        """A3: both sides changed since the last fingerprints? Apply the
        instance policy and post the audit message. Returns (winner, fields)
        where winner is 'shopify' (caller applies vals) or 'odoo' (caller
        skips + re-exports)."""
        odoo_changed = bool(
            binding.odoo_write_date and tmpl.write_date
            and tmpl.write_date > binding.odoo_write_date)
        if not odoo_changed:
            return "shopify", []
        changed = self._changed_fields(tmpl, vals)
        policy = instance.conflict_policy
        if policy == "newest_wins":
            winner = ("shopify" if (ext_updated or datetime.min)
                      >= tmpl.write_date else "odoo")
        else:
            winner = "odoo" if policy == "odoo_wins" else "shopify"
        tmpl.message_post(body=_(
            "Shopify sync conflict on %(fields)s: kept %(side)s version "
            "(policy: %(policy)s, store: %(store)s).",
            fields=", ".join(changed) or "-",
            side=_("Odoo") if winner == "odoo" else _("Shopify"),
            policy=policy, store=instance.name))
        # Chatter is for the person looking at this product; the ledger is for
        # the person asking "what did Shopify overwrite last month?".
        self.env["shopify.bisync.conflict"].log(
            self.env, instance, tmpl, binding, winner, changed, ext_updated)
        return winner, changed

    @api.model
    def _import_variants(self, instance, sp, tmpl):
        """Mirror Shopify options/variants onto Odoo attributes, then bind
        each Shopify variant to its Odoo counterpart (signature match)."""
        Binding = self.env["shopify.bisync.binding"]
        variants = sp.get("variants") or []
        if not self._is_default_variant_only(sp):
            self._sync_attribute_lines(sp, tmpl)
        sig_to_odoo = {self._odoo_variant_signature(v): v
                       for v in tmpl.product_variant_ids}
        for shopify_variant in variants:
            if self._is_default_variant_only(sp):
                odoo_variant = tmpl.product_variant_id
            else:
                signature = self._variant_option_signature(sp, shopify_variant)
                odoo_variant = sig_to_odoo.get(signature)
                if odoo_variant is None:
                    _logger.warning(
                        "shopify_bisync: no Odoo variant for Shopify variant "
                        "%s of %s", shopify_variant.get("id"), tmpl.name)
                    continue
                odoo_variant.with_context(**{SKIP_TRIGGER: True}).write({
                    "default_code": shopify_variant.get("sku") or False,
                    "barcode": shopify_variant.get("barcode") or False,
                    "weight": self._variant_weight_kg(shopify_variant),
                })
            vbinding = Binding.get(self.env, instance, "product.product",
                                   record=odoo_variant)
            payload = {"external_id": str(shopify_variant["id"]),
                       "inventory_item_id":
                           str(shopify_variant.get("inventory_item_id") or "")}
            if vbinding:
                vbinding.write(payload)
            else:
                Binding.create({
                    "instance_id": instance.id,
                    "res_model": "product.product",
                    "res_id": odoo_variant.id, **payload})

    @api.model
    def _sync_attribute_lines(self, sp, tmpl):
        Attribute = self.env["product.attribute"]
        AttributeValue = self.env["product.attribute.value"]
        line_commands = []
        wanted_attribute_ids = []
        for option in sorted(sp.get("options") or [],
                             key=lambda o: o.get("position") or 0):
            attribute = Attribute.search(
                [("name", "=ilike", option["name"])], limit=1)
            if not attribute:
                attribute = Attribute.create({
                    "name": option["name"], "create_variant": "always"})
            value_ids = []
            for value_name in option.get("values") or []:
                value = AttributeValue.search([
                    ("attribute_id", "=", attribute.id),
                    ("name", "=ilike", value_name)], limit=1)
                if not value:
                    value = AttributeValue.create({
                        "attribute_id": attribute.id, "name": value_name})
                value_ids.append(value.id)
            wanted_attribute_ids.append(attribute.id)
            line = tmpl.attribute_line_ids.filtered(
                lambda l: l.attribute_id == attribute)
            if line:
                line_commands.append(
                    fields.Command.update(line.id, {
                        "value_ids": [fields.Command.set(value_ids)]}))
            else:
                line_commands.append(fields.Command.create({
                    "attribute_id": attribute.id,
                    "value_ids": [fields.Command.set(value_ids)]}))
        for line in tmpl.attribute_line_ids:
            if line.attribute_id.id not in wanted_attribute_ids:
                line_commands.append(fields.Command.unlink(line.id))
        if line_commands:
            tmpl.with_context(**{SKIP_TRIGGER: True}).write(
                {"attribute_line_ids": line_commands})

    # -------------------------------------------------------------- images --
    @staticmethod
    def _gallery_supported(tmpl):
        """``product.image`` is defined by ``website_sale``, which a Shopify
        merchant has no reason to install - Shopify IS their storefront. Extra
        images sync when it happens to be there and are skipped (loudly, once)
        when it is not, rather than dragging the whole website stack into this
        module's dependencies."""
        return "product_template_image_ids" in tmpl._fields

    @staticmethod
    def _download_image(src):
        try:
            resp = requests.get(src, timeout=30)
            resp.raise_for_status()
        except requests.RequestException:
            _logger.warning("shopify_bisync: image download failed for %s",
                            src, exc_info=True)
            return None
        return base64.b64encode(resp.content)

    @api.model
    def _odoo_images(self, tmpl):
        """The template's gallery as ordered ``(sha1, base64)`` pairs: main
        image first, then the extra images in their own sequence. Duplicates
        are dropped so the same picture is never uploaded twice."""
        raws = [tmpl.image_1920]
        if self._gallery_supported(tmpl):
            raws += [extra.image_1920
                     for extra in tmpl.product_template_image_ids]
        images, seen = [], set()
        for raw in raws:
            if not raw:
                continue
            digest = hashlib.sha1(raw).hexdigest()
            if digest not in seen:
                seen.add(digest)
                images.append((digest, raw))
        return images

    @staticmethod
    def _gallery_checksum(images):
        return hashlib.sha1(
            "".join(digest for digest, __ in images).encode()).hexdigest()

    @api.model
    def _import_images(self, instance, sp, tmpl, binding):
        """Mirror the Shopify gallery onto Odoo: first image becomes the main
        product image, the rest become ``product.image`` rows."""
        if instance.image_policy == "off":
            return
        sources = [i["src"] for i in (sp.get("images") or []) if i.get("src")]
        if not sources:
            return
        if instance.image_policy == "primary":
            sources = sources[:1]
        # Shopify's CDN URLs carry a version, so an unchanged URL list means
        # an unchanged gallery - and no reason to download anything.
        source_hash = hashlib.sha1("|".join(sources).encode()).hexdigest()
        if source_hash == (binding.image_source_hash or ""):
            return
        downloaded = [raw for raw in
                      (self._download_image(src) for src in sources) if raw]
        if not downloaded:
            return
        tmpl.with_context(**{SKIP_TRIGGER: True}).write(
            {"image_1920": downloaded[0]})
        self._replace_gallery(instance, tmpl, downloaded[1:])
        binding.image_source_hash = source_hash

    @api.model
    def _replace_gallery(self, instance, tmpl, extra_images):
        if not self._gallery_supported(tmpl):
            if extra_images:
                self.env["shopify.bisync.mismatch"].log(
                    self.env, instance, "image_gallery",
                    _("'%(name)s' has %(n)s extra Shopify images that were "
                      "not imported: Odoo's product.image model is missing. "
                      "Install Website/eCommerce (website_sale) to hold "
                      "galleries, or set Images to 'Main image only' on the "
                      "store to stop this notice.",
                      name=tmpl.name, n=len(extra_images)),
                    reference=tmpl.default_code or tmpl.name)
            return
        commands = [fields.Command.clear()]
        for position, raw in enumerate(extra_images, start=1):
            commands.append(fields.Command.create({
                "name": f"{tmpl.name} #{position + 1}",
                "image_1920": raw,
                "sequence": position * 10,
            }))
        tmpl.with_context(**{SKIP_TRIGGER: True}).write(
            {"product_template_image_ids": commands})

    # -------------------------------------------------------------- export --
    @api.model
    def _export_payload(self, instance, tmpl, binding):
        """Build the full ProductSetInput + image payload. The checksum over
        this tuple covers ALL exported fields including the image."""
        has_hs = "hs_code" in tmpl._fields
        if not tmpl.active:
            status = "ARCHIVED"
        elif binding and binding.shopify_status == "draft":
            status = "DRAFT"
        elif binding and binding.shopify_status == "active":
            status = "ACTIVE"
        else:
            status = instance.export_status_default
        option_lines = tmpl.attribute_line_ids
        payload = {
            "title": tmpl.name,
            "descriptionHtml": tmpl.description_sale or "",
            "status": status,
            "productOptions": [
                {"name": line.attribute_id.name, "position": position + 1,
                 "values": [{"name": v.name} for v in line.value_ids]}
                for position, line in enumerate(option_lines)],
            "variants": [],
        }
        if instance.sync_product_tags:
            payload["tags"] = sorted(tmpl.product_tag_ids.mapped("name"))
        if instance.sync_product_category and tmpl.categ_id:
            payload["productType"] = tmpl.categ_id.name
        for variant in tmpl.product_variant_ids:
            price = (instance.pricelist_id._get_product_price(variant, 1.0)
                     if instance.pricelist_id else variant.lst_price)
            entry = {
                "optionValues": [
                    {"optionName": ptav.attribute_id.name, "name": ptav.name}
                    for ptav in variant.product_template_attribute_value_ids
                ] or [{"optionName": "Title", "name": "Default Title"}],
                "price": f"{price:.2f}",
                "sku": variant.default_code or "",
                "barcode": variant.barcode or "",
                "inventoryItem": {
                    "tracked": variant.is_storable,
                    "measurement": {"weight": {
                        "value": variant.weight or 0.0,
                        "unit": "KILOGRAMS"}},
                },
            }
            if (instance.compare_at_policy == "list_price"
                    and price < variant.lst_price):
                entry["compareAtPrice"] = f"{variant.lst_price:.2f}"
            if has_hs and variant.hs_code:
                entry["inventoryItem"]["harmonizedSystemCode"] = variant.hs_code
            payload["variants"].append(entry)
        images = self._odoo_images(tmpl)
        if instance.image_policy == "off":
            images = []
        elif instance.image_policy == "primary":
            images = images[:1]
        return payload, images, self._gallery_checksum(images)

    @staticmethod
    def _checksum(payload, image_checksum):
        return hashlib.sha1(
            (json.dumps(payload, sort_keys=True, default=str)
             + image_checksum).encode()).hexdigest()

    @api.model
    def _export_product(self, instance, job_payload):
        if instance.sync_products not in ("export", "both"):
            return
        tmpl = self.env["product.template"].browse(
            job_payload["res_id"]).exists()
        if not tmpl:
            return
        Binding = self.env["shopify.bisync.binding"]
        binding = Binding.get(self.env, instance, "product.template",
                              record=tmpl)
        if not binding:
            # Match-before-create: adopt the listing this product already is
            # on Shopify instead of publishing a second one next to it.
            external_id = self._match_shopify_product(instance, tmpl)
            if external_id:
                binding = Binding.create({
                    "instance_id": instance.id,
                    "res_model": "product.template",
                    "res_id": tmpl.id, "external_id": external_id})
                tmpl.message_post(body=_(
                    "Linked to existing Shopify product %(sid)s on %(store)s "
                    "by match-before-create - no duplicate listing was "
                    "created.", sid=external_id, store=instance.name))
        payload, images, image_checksum = self._export_payload(
            instance, tmpl, binding)
        checksum = self._checksum(payload, image_checksum)
        if binding and binding.checksum == checksum:
            return  # no-op: nothing changed since last sync, zero API calls
        identifier = ({"id": instance.gid("Product", binding.external_id)}
                      if binding else None)
        payload = self._with_files(instance, tmpl, binding, images, payload)
        data = instance.graphql(PRODUCT_SET, {
            "identifier": identifier, "input": payload, "synchronous": True})
        result = data.get("productSet") or {}
        instance.check_user_errors(result, "productSet")
        product_node = result.get("product") or {}
        external_id = instance.gid_to_id(product_node.get("id", ""))
        if not binding:
            binding = Binding.create({
                "instance_id": instance.id, "res_model": "product.template",
                "res_id": tmpl.id, "external_id": external_id,
                "shopify_status": payload["status"].lower()})
        else:
            binding.shopify_status = payload["status"].lower()
        self._bind_exported_variants(instance, tmpl, product_node)
        if images:
            # Media travelled inside the productSet input, so by this point
            # Shopify has already reconciled the gallery; all that is left is
            # to remember which file is which for the next export.
            self._store_image_map(binding, images, product_node)
        binding.image_checksum = image_checksum
        binding.write({
            "checksum": checksum,
            "odoo_write_date": tmpl.write_date,
            "external_updated_at":
                parse_shopify_dt(product_node.get("updatedAt"))
                or fields.Datetime.now(),
        })
        if instance.publish_policy == "auto":
            self.env["shopify.bisync.job"].enqueue(
                instance, "out", "publish",
                {"res_id": tmpl.id, "action": "publish"},
                priority=26, lock_key=f"publish:{tmpl.id}")

    # ------------------------------------------------------------- publish --
    @api.model
    def _publish_product(self, instance, payload):
        """Publish/unpublish an exported product to the store's mapped sales
        channels (publications)."""
        tmpl = self.env["product.template"].browse(
            payload.get("res_id", 0)).exists()
        binding = self.env["shopify.bisync.binding"].get(
            self.env, instance, "product.template", record=tmpl)
        publications = instance.publication_ids.filtered("publish")
        if not tmpl or not binding or not publications:
            return
        unpublish = payload.get("action") == "unpublish"
        inputs = [{"publicationId": instance.gid(
            "Publication", pub.shopify_publication_id)}
            for pub in publications]
        data = instance.graphql(
            PUBLISHABLE_UNPUBLISH if unpublish else PUBLISHABLE_PUBLISH,
            {"id": instance.gid("Product", binding.external_id),
             "input": inputs})
        key = "publishableUnpublish" if unpublish else "publishablePublish"
        instance.check_user_errors(data.get(key) or {}, key)

    @api.model
    def _bind_exported_variants(self, instance, tmpl, product_node):
        Binding = self.env["shopify.bisync.binding"]
        sig_to_odoo = {self._odoo_variant_signature(v): v
                       for v in tmpl.product_variant_ids}
        sku_to_odoo = {v.default_code: v
                       for v in tmpl.product_variant_ids if v.default_code}
        for node in (product_node.get("variants", {}).get("nodes") or []):
            signature = frozenset(
                (o["name"].strip().lower(), str(o["value"]).strip().lower())
                for o in node.get("selectedOptions") or []
                if o["name"] != "Title")
            odoo_variant = (sig_to_odoo.get(signature)
                            or sku_to_odoo.get(node.get("sku"))
                            or (tmpl.product_variant_id
                                if tmpl.product_variant_count == 1 else None))
            if odoo_variant is None:
                continue
            vals = {
                "external_id": instance.gid_to_id(node["id"]),
                "inventory_item_id": instance.gid_to_id(
                    (node.get("inventoryItem") or {}).get("id", "")),
            }
            vbinding = Binding.get(self.env, instance, "product.product",
                                   record=odoo_variant)
            if vbinding:
                vbinding.write(vals)
            else:
                Binding.create({"instance_id": instance.id,
                                "res_model": "product.product",
                                "res_id": odoo_variant.id, **vals})

    @api.model
    def _staged_upload(self, instance, tmpl, index, image_b64):
        """Push one image to Shopify's staging bucket and return the
        ``resourceUrl`` productSet can consume. Staging is what lets this work
        on an Odoo that is not publicly reachable - Shopify never has to fetch
        a URL from us."""
        raw = base64.b64decode(image_b64)
        is_png = raw[:4] == b"\x89PNG"
        mime = "image/png" if is_png else "image/jpeg"
        filename = f"odoo-{tmpl.id}-{index}.{'png' if is_png else 'jpg'}"
        data = instance.graphql(STAGED_UPLOADS, {"input": [{
            "resource": "IMAGE", "filename": filename, "mimeType": mime,
            "httpMethod": "POST"}]})
        result = data.get("stagedUploadsCreate") or {}
        instance.check_user_errors(result, "stagedUploadsCreate")
        target = (result.get("stagedTargets") or [{}])[0]
        upload = requests.post(
            target["url"],
            data={p["name"]: p["value"] for p in target.get("parameters", [])},
            files={"file": (filename, raw, mime)}, timeout=60)
        upload.raise_for_status()
        return target["resourceUrl"]

    @api.model
    def _with_files(self, instance, tmpl, binding, images, payload):
        """Attach the declarative ``files`` list to a productSet input.

        productSet reconciles list fields - entries absent from the input are
        DELETED - so the list has to be complete every single time. Images
        Shopify already holds therefore go as an ``id`` reference taken from
        the binding's map; only genuinely new ones are staged and uploaded.
        Without that map every export would re-upload the whole gallery.

        ``files`` is omitted entirely when image sync is off. Shopify does not
        document what omitting a list field does (as opposed to omitting an
        entry from a supplied list), so that path is left exactly as it was
        and is flagged for live-store testing rather than guessed at.
        """
        if instance.image_policy == "off" or not images:
            return payload
        image_map = json.loads(binding.image_map_json or "{}") if binding else {}
        files = []
        for index, (digest, raw) in enumerate(images):
            known = image_map.get(digest)
            if known:
                files.append({"id": known})
            else:
                files.append({
                    "originalSource": self._staged_upload(
                        instance, tmpl, index, raw),
                    "contentType": "IMAGE",
                    "alt": tmpl.name or "",
                })
        return dict(payload, files=files)

    @api.model
    def _store_image_map(self, binding, images, product_node):
        """Pair the media Shopify returned with the local images that produced
        them so the next export sends ids, not uploads.

        The pairing is positional, so it is only trusted when the counts line
        up. When they do not the map is cleared: re-uploading a gallery is
        wasteful, attaching the wrong picture to the wrong hash is not
        recoverable."""
        nodes = (product_node.get("media") or {}).get("nodes") or []
        mapping = {}
        if len(nodes) == len(images):
            mapping = {digest: node["id"]
                       for (digest, __), node in zip(images, nodes)
                       if node.get("id")}
        binding.image_map_json = json.dumps(mapping)

    # ------------------------------------------------------------- dry run --
    @api.model
    def diff_against_shopify(self, instance, tmpl):
        """What an export would change, without changing anything.

        Returns a list of ``{field, odoo, shopify}`` dicts. Unbound products
        report a single 'create' row - there is nothing on the other side to
        diff against yet.
        """
        binding = self.env["shopify.bisync.binding"].get(
            self.env, instance, "product.template", record=tmpl)
        payload, images, image_checksum = self._export_payload(
            instance, tmpl, binding)
        if not binding:
            return [{"field": "product",
                     "odoo": tmpl.display_name,
                     "shopify": _("(not on Shopify yet - would be created)")}]
        if binding.checksum == self._checksum(payload, image_checksum):
            return []
        data = instance.graphql(PRODUCT_ONE, {
            "id": instance.gid("Product", binding.external_id)})
        node = data.get("product")
        if not node:
            return [{"field": "product", "odoo": tmpl.display_name,
                     "shopify": _("(missing on Shopify - would be recreated)")}]
        return self._diff_rows(payload, images, node)

    @api.model
    def _diff_rows(self, payload, images, node):
        """Field-by-field comparison of the payload we would send against the
        product Shopify currently holds."""
        rows = []

        def add(label, odoo_value, shopify_value):
            if str(odoo_value or "") != str(shopify_value or ""):
                rows.append({"field": label, "odoo": str(odoo_value or "-"),
                             "shopify": str(shopify_value or "-")})

        add(_("Title"), payload.get("title"), node.get("title"))
        add(_("Description"), html2plaintext(payload.get("descriptionHtml") or ""),
            html2plaintext(node.get("descriptionHtml") or ""))
        add(_("Status"), payload.get("status"),
            (node.get("status") or "").upper())
        if "productType" in payload:
            add(_("Product type"), payload.get("productType"),
                node.get("productType"))
        if "tags" in payload:
            add(_("Tags"), ", ".join(payload.get("tags") or []),
                ", ".join(sorted(node.get("tags") or [])))
        remote_variants = {
            (v.get("sku") or "").strip(): v
            for v in (node.get("variants") or {}).get("nodes") or []}
        for variant in payload.get("variants") or []:
            sku = (variant.get("sku") or "").strip()
            remote = remote_variants.get(sku)
            label = sku or variant.get("optionValues", [{}])[0].get("name", "?")
            if remote is None:
                rows.append({"field": _("Variant %s", label),
                             "odoo": variant.get("price"),
                             "shopify": _("(new variant)")})
                continue
            add(_("Variant %s price", label), variant.get("price"),
                remote.get("price"))
            add(_("Variant %s barcode", label), variant.get("barcode"),
                remote.get("barcode"))
        remote_media = len((node.get("media") or {}).get("nodes") or [])
        if len(images) != remote_media:
            rows.append({"field": _("Images"), "odoo": str(len(images)),
                         "shopify": str(remote_media)})
        return rows

    # -------------------------------------------------------------- prices --
    @api.model
    def _export_prices(self, instance, job_payload):
        if instance.sync_prices not in ("export", "both"):
            return
        tmpl = self.env["product.template"].browse(
            job_payload["res_id"]).exists()
        binding = self.env["shopify.bisync.binding"].get(
            self.env, instance, "product.template", record=tmpl)
        if not tmpl or not binding:
            return
        variants_input = []
        for variant in tmpl.product_variant_ids:
            vbinding = self.env["shopify.bisync.binding"].get(
                self.env, instance, "product.product", record=variant)
            if not vbinding:
                continue
            price = (instance.pricelist_id._get_product_price(variant, 1.0)
                     if instance.pricelist_id else variant.lst_price)
            entry = {"id": instance.gid("ProductVariant", vbinding.external_id),
                     "price": f"{price:.2f}"}
            entry["compareAtPrice"] = (
                f"{variant.lst_price:.2f}"
                if (instance.compare_at_policy == "list_price"
                    and price < variant.lst_price) else None)
            variants_input.append(entry)
        if not variants_input:
            return
        data = instance.graphql(PRICE_UPDATE, {
            "productId": instance.gid("Product", binding.external_id),
            "variants": variants_input})
        instance.check_user_errors(
            data.get("productVariantsBulkUpdate") or {},
            "productVariantsBulkUpdate")


class ProductTemplate(models.Model):
    _inherit = "product.template"

    shopify_binding_count = fields.Integer(
        compute="_compute_shopify_binding_count")

    def _compute_shopify_binding_count(self):
        counts = dict(self.env["shopify.bisync.binding"]._read_group(
            [("res_model", "=", "product.template"),
             ("res_id", "in", self.ids)], ["res_id"], ["__count"]))
        for tmpl in self:
            tmpl.shopify_binding_count = counts.get(tmpl.id, 0)

    def action_view_shopify_bindings(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "shopify_bisync.binding_action")
        action["domain"] = [("res_model", "=", "product.template"),
                            ("res_id", "=", self.id)]
        return action

    def action_shopify_open(self):
        """Quick-jump: open the first bound product in the Shopify admin."""
        self.ensure_one()
        binding = self.env["shopify.bisync.binding"].search([
            ("res_model", "=", "product.template"),
            ("res_id", "=", self.id)], limit=1)
        if not binding:
            raise UserError(_("This product is not linked to Shopify yet."))
        return {"type": "ir.actions.act_url", "target": "new",
                "url": binding.instance_id.admin_url(
                    "products", binding.external_id)}

    def _action_shopify_publish(self, action):
        Job = self.env["shopify.bisync.job"]
        Binding = self.env["shopify.bisync.binding"]
        for tmpl in self:
            for binding in Binding.search([
                    ("res_model", "=", "product.template"),
                    ("res_id", "=", tmpl.id)]):
                Job.enqueue(binding.instance_id, "out", "publish",
                            {"res_id": tmpl.id, "action": action},
                            priority=26, lock_key=f"publish:{tmpl.id}")
        return True

    def action_shopify_preview(self):
        """Dry run: what would an export change on Shopify right now?"""
        self.ensure_one()
        return self.env["shopify.bisync.preview"].open_for(self)

    def action_shopify_publish(self):
        return self._action_shopify_publish("publish")

    def action_shopify_unpublish(self):
        return self._action_shopify_publish("unpublish")

    def _shopify_enqueue_export(self, price_only=False):
        if self.env.context.get(SKIP_TRIGGER):
            return
        Job = self.env["shopify.bisync.job"]
        Binding = self.env["shopify.bisync.binding"]
        for instance in self.env["shopify.bisync.instance"].sudo().search([]):
            for tmpl in self:
                if tmpl.company_id and tmpl.company_id != instance.company_id:
                    continue  # company-specific product, other company's store
                bound = Binding.get(self.env, instance, "product.template",
                                    record=tmpl)
                if price_only:
                    if instance.sync_prices in ("export", "both") and bound:
                        Job.enqueue(instance, "out", "price",
                                    {"res_id": tmpl.id}, priority=30,
                                    lock_key=f"price:{tmpl.id}")
                    continue
                if instance.sync_products not in ("export", "both"):
                    continue
                if bound or instance.auto_export_new_products:
                    Job.enqueue(instance, "out", "product",
                                {"res_id": tmpl.id}, priority=25,
                                lock_key=f"product:{tmpl.id}")

    @api.model_create_multi
    def create(self, vals_list):
        templates = super().create(vals_list)
        templates._shopify_enqueue_export()
        return templates

    def write(self, vals):
        res = super().write(vals)
        watched = set(vals) & set(
            self.env["shopify.bisync.product.sync"].WATCHED_FIELDS)
        if watched:
            self._shopify_enqueue_export()
        if "list_price" in vals:
            self._shopify_enqueue_export(price_only=True)
        return res


class ProductProduct(models.Model):
    _inherit = "product.product"

    def write(self, vals):
        res = super().write(vals)
        if set(vals) & set(
                self.env["shopify.bisync.product.sync"].WATCHED_VARIANT_FIELDS):
            self.product_tmpl_id._shopify_enqueue_export()
        return res
