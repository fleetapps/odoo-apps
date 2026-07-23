# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Product two-way sync with conflict policy (spec A1 + A3).

Odoo -> Shopify: template ``create()``/``write()`` on watched fields enqueue
an export job; the export goes through GraphQL ``productSet`` (REST product
endpoints are deprecated for new apps), images through staged uploads +
``productCreateMedia``, prices through ``productVariantsBulkUpdate``.

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

PRODUCT_SET = """
mutation productSet($input: ProductSetInput!, $synchronous: Boolean!) {
  productSet(input: $input, synchronous: $synchronous) {
    product {
      id
      updatedAt
      variants(first: 250) {
        nodes { id sku inventoryItem { id } selectedOptions { name value } }
      }
    }
    userErrors { field message }
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

CREATE_MEDIA = """
mutation productCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
  productCreateMedia(productId: $productId, media: $media) {
    media { id }
    mediaUserErrors { field message }
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
      media(first: 1) { nodes { ... on MediaImage { image { url } } } }
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
            first = (sp.get("variants") or [{}])[0]
            vals["list_price"] = float(first.get("price") or 0.0)
            tmpl = self.env["product.template"].with_context(
                **{SKIP_TRIGGER: True}).create(vals)
            binding = Binding.create({
                "instance_id": instance.id, "res_model": "product.template",
                "res_id": tmpl.id, "external_id": str(sp["id"])})
        binding.shopify_status = sp.get("status") or "active"
        self._import_variants(instance, sp, tmpl)
        self._import_image(instance, sp, tmpl, binding)
        # Fingerprints: store the EXPORT checksum of the freshly imported
        # state so the next export is a guaranteed no-op (zero API calls).
        payload, __, image_checksum = self._export_payload(
            instance, tmpl, binding)
        binding.write({
            "external_updated_at": ext_updated or fields.Datetime.now(),
            "odoo_write_date": tmpl.write_date,
            "checksum": self._checksum(payload, image_checksum),
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
        changed = sorted(
            name for name, new in vals.items()
            if (tmpl[name] or False) != (new or False))
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

    @api.model
    def _import_image(self, instance, sp, tmpl, binding):
        src = ((sp.get("images") or [{}])[0]).get("src")
        if not src:
            return
        try:
            resp = requests.get(src, timeout=30)
            resp.raise_for_status()
        except requests.RequestException:
            _logger.warning("shopify_bisync: image download failed for %s",
                            tmpl.name, exc_info=True)
            return
        image_b64 = base64.b64encode(resp.content)
        checksum = hashlib.sha1(image_b64).hexdigest()
        if checksum != (binding.image_checksum or ""):
            tmpl.with_context(**{SKIP_TRIGGER: True}).write(
                {"image_1920": image_b64})
            binding.image_checksum = checksum

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
        image_b64 = tmpl.image_1920 or b""
        image_checksum = (hashlib.sha1(image_b64).hexdigest()
                          if image_b64 else "")
        return payload, image_b64, image_checksum

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
        payload, image_b64, image_checksum = self._export_payload(
            instance, tmpl, binding)
        checksum = self._checksum(payload, image_checksum)
        if binding and binding.checksum == checksum:
            return  # no-op: nothing changed since last sync, zero API calls
        if binding:
            payload["id"] = instance.gid("Product", binding.external_id)
        data = instance.graphql(PRODUCT_SET, {
            "input": payload, "synchronous": True})
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
        if image_b64 and image_checksum != (binding.image_checksum or ""):
            self._export_image(instance, binding, tmpl, image_b64)
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
    def _export_image(self, instance, binding, tmpl, image_b64):
        """Staged upload (no public Odoo URL needed) + productCreateMedia."""
        raw = base64.b64decode(image_b64)
        is_png = raw[:4] == b"\x89PNG"
        mime = "image/png" if is_png else "image/jpeg"
        filename = f"odoo-{tmpl.id}.{'png' if is_png else 'jpg'}"
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
        media = instance.graphql(CREATE_MEDIA, {
            "productId": instance.gid("Product", binding.external_id),
            "media": [{"originalSource": target["resourceUrl"],
                       "mediaContentType": "IMAGE"}]})
        instance.check_user_errors(
            media.get("productCreateMedia") or {}, "productCreateMedia")

    # -------------------------------------------------------------- prices --
    @api.model
    def _export_prices(self, instance, job_payload):
        if instance.sync_prices != "export":
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
                    if instance.sync_prices == "export" and bound:
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
