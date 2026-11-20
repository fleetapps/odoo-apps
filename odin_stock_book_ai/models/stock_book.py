"""Add sale from a photo of the invoice.

The window sends the photos; :meth:`book_sale_photo_read` has them read
(prompts.py) and turns what was read into the window's own fields, matching
the customer and every line's item here, in code (matching.py), and checking
what can be checked: the customer's pricelist price, the VAT, quantity times
rate against the amount printed, the pages, the total, the terms against
the due date. When the rules leave a line between close items,
:meth:`book_sale_photo_choose` asks the model to choose among those, in a
second request. Nothing is saved: the person checks the window and saves
it as any other sale, and the photos go with it.

Every call goes through Accounting AI (``odin.ai.llm.call``): its consent,
key, monthly budget and Activity Log, as feature "Stock Book photo".
"""

import hashlib
import json
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round

from odoo.addons.odin_account_ai.models.odin_ai_llm import final_json

from . import matching, prompts
from .stock_photo import PHOTO_MAX_MB, PHOTO_TYPES, PHOTOS_MAX

FEATURE = "stock_photo"
# Reading is perception, and the checks below catch a misread number; the
# choice between close items is where thinking pays.
READ_EFFORT = "low"
CHOOSE_EFFORT = "medium"
# Room for a long invoice (some 60 lines) and the thinking before it.
READ_MAX_TOKENS = 16000
CHOOSE_MAX_LINES = 40
CATALOGUE_LIMIT = 5000
LETTERS = "ABCDEF"
# Within this, a rate is the pricelist price (rounding of VAT both ways).
PRICE_TOLERANCE = 0.015
# Within this, the lines add up to the total printed (rounding on the paper).
TOTAL_TOLERANCE = 1.0


def _flag(level, text):
    return {"level": level, "text": text}


def _net(price, tax):
    """The price before VAT, as this tax reads it."""
    if tax and tax.amount_type == "percent" and tax.price_include and tax.amount:
        return price / (1 + tax.amount / 100)
    return price


def _as_tax_reads(net, tax):
    """A price before VAT, written as this tax reads a price."""
    if tax and tax.amount_type == "percent" and tax.price_include and tax.amount:
        return net * (1 + tax.amount / 100)
    return net


def _money(value):
    return f"{value:,.2f}"


class StockBook(models.AbstractModel):
    _inherit = "odin.stock.book"

    # ------------------------------------------------------------ the window
    @api.model
    def book_sale_options(self):
        options = super().book_sale_options()
        options["photo"] = {
            # What stops the AI (consent, key, budget), said on the button.
            "blocker": self.env["odin.ai.llm"].readiness() or False,
            "types": list(PHOTO_TYPES), "max_files": PHOTOS_MAX, "max_mb": PHOTO_MAX_MB,
        }
        return options

    @api.model
    def book_sale_photo_files(self, line_id):
        """The photos of a saved sale, for the window opened on it again."""
        line = self.env["odin.stock.line"].browse(line_id).exists()
        photo = line.stock_sale_id.photo_ids[:1]
        return {"id": photo.id, "files": photo._files_info()} if photo else False

    @api.model
    def _photo_for_window(self, photo_id):
        Photo = self.env["odin.stock.photo"]
        if not photo_id:
            return Photo
        photo = Photo.sudo().browse(photo_id).exists()
        if not photo or photo.company_id != self.env.company:
            raise UserError(_("These photos are no longer here. Upload them again."))
        sale = photo.sale_id
        if sale and sale.order_id and not sale.undone_on:
            raise UserError(_("These photos are of sale %s, which is posted. Upload the photos again.",
                              sale.sale_ref))
        if not sale and photo.user_id != self.env.user:
            raise UserError(_("These photos were uploaded by someone else. Upload them again."))
        return photo

    @api.model
    def book_sale_photo_remove(self, photo_id, attachment_id):
        """A page taken off before the photos are read again."""
        self._require_book_user()
        photo = self._photo_for_window(photo_id)
        photo.attachment_ids.filtered(lambda a: a.id == attachment_id).unlink()
        return {"id": photo.id, "files": photo._files_info()}

    # ------------------------------------------------------------ reading
    @api.model
    def book_sale_photo_read(self, files=None, photo_id=False, attempt=0, warehouse_id=False):
        """Read the invoice in the photos: those sent now, added to the pages
        of ``photo_id`` already uploaded. ``attempt`` reads the same pages
        afresh (Read again). Returns the photos and what fills the window;
        ``prefill.pending`` lists the lines :meth:`book_sale_photo_choose`
        is to choose an item for."""
        self._require_book_user()
        Photo = self.env["odin.stock.photo"]
        photo = self._photo_for_window(photo_id)
        new = Photo._read_files(files)
        if not photo:
            if not new:
                raise UserError(_("Add a photo of the invoice."))
            photo = Photo.sudo().create({"company_id": self.env.company.id, "user_id": self.env.uid})
        if new:
            photo._add_files(new)
        if not photo.attachment_ids:
            raise UserError(_("Add a photo of the invoice."))
        company = self.env.company
        pin = f" (KRA PIN {company.vat})" if company.vat else ""
        count = len(photo.attachment_ids)
        # Plain text for the model, in English whatever the user's language.
        ask = (f"The seller is {company.name}{pin}. Today is {fields.Date.context_today(self).isoformat()}. "
               f"Read the invoice in the {count} photo{'s' if count > 1 else ''} above.")
        data = self.env["odin.ai.llm"].call(
            FEATURE, system=prompts.READ_SYSTEM, schema=prompts.READ_SCHEMA,
            messages=[{"role": "user", "content": photo._content_blocks() + [{"type": "text", "text": ask}]}],
            step_key=f"stock-photo-read-{self.env.uid}-{photo._fingerprint(attempt)}",
            effort=READ_EFFORT, max_tokens=READ_MAX_TOKENS)
        reading = final_json(data)
        result = self._photo_prefill(reading, warehouse_id, count)
        photo._store(reading=reading, result=result)
        return {"photo": {"id": photo.id, "files": photo._files_info()}, "prefill": result}

    @api.model
    def book_sale_photo_choose(self, photo_id, warehouse_id=False):
        """For the lines the rules left between close items: the model's
        choice among those items, given the line as read (no photo)."""
        self._require_book_user()
        photo = self._photo_for_window(photo_id)
        result = json.loads(photo.result or "{}")
        pending = [i for i in result.get("pending") or [] if 0 <= i < len(result.get("lines") or [])]
        if not pending:
            return {"prefill": result}
        ctx = self._photo_context_from(result, warehouse_id)
        lines, letters = [], {}
        for index in pending[:CHOOSE_MAX_LINES]:
            line = result["lines"][index]
            row, match = line["read"], line["match"]
            products = self.env["product.product"].browse(match["candidate_ids"]).exists()
            items = []
            for letter, product in zip(LETTERS, products):
                letters[(index, letter)] = product.id
                price = self._photo_pricelist(ctx, product, row)[0]
                items.append({
                    "letter": letter, "code": product.default_code or "", "name": self._photo_name(product),
                    "category": product.categ_id.complete_name or "", "unit": product.uom_id.name,
                    "customer_price": price, "bought_before": product.id in ctx["history"],
                })
            lines.append({"line": index + 1, "description": row.get("description") or "", "code": row.get("code") or "",
                          "unit": row.get("unit") or "", "quantity": row.get("quantity"), "rate": row.get("rate"),
                          "vat": row.get("vat") or "", "items": items})
        vat = "include VAT" if ctx["prices_include"] else "are before VAT"
        text = (f"Rates as printed on the invoice ({vat}); customer_price is the customer's price for the item, "
                f"read the same way. Lines:\n{json.dumps(lines, ensure_ascii=False)}")
        digest = hashlib.sha256(text.encode()).hexdigest()[:32]
        data = self.env["odin.ai.llm"].call(
            FEATURE, system=prompts.CHOOSE_SYSTEM, schema=prompts.CHOOSE_SCHEMA,
            messages=[{"role": "user", "content": text}],
            step_key=f"stock-photo-choose-{self.env.uid}-{digest}", effort=CHOOSE_EFFORT)
        for choice in final_json(data).get("choices") or []:
            index = (choice.get("line") or 0) - 1
            if index not in pending[:CHOOSE_MAX_LINES]:
                continue
            letter = (choice.get("choice") or "").strip().upper()
            line = result["lines"][index]
            match = line["match"]
            product_id = letters.get((index, letter))
            if letter != "NONE" and not product_id:
                continue  # a letter that was not offered
            sure = bool(choice.get("sure")) and bool(product_id)
            match.update(product_id=product_id or False, confidence="likely" if sure else matching.CHECK,
                         chosen={"reason": " ".join((choice.get("reason") or "").split())[:300], "sure": sure,
                                 "count": len(match["candidate_ids"]), "none": not product_id})
            result["lines"][index] = self._photo_line(ctx, line["read"], match)
        result["pending"] = []
        photo._store(result=result)
        return {"prefill": result}

    # ------------------------------------------------------------ what fills the window
    @api.model
    def _photo_prefill(self, reading, warehouse_id, photo_count):
        flags = []
        if not reading.get("is_invoice", True):
            flags.append(_flag("warn", _("This does not look like a customer invoice. Check that it is the right "
                                         "paper.")))
        customer, query, choices, customer_flags = self._photo_customer(reading.get("customer") or {})
        flags += customer_flags
        partner = self.env["res.partner"].browse(customer["id"]) if customer else self.env["res.partner"]
        today = fields.Date.context_today(self)
        day = self._photo_day(reading.get("date"))
        if not day:
            flags.append(_flag("warn", _("The date could not be read: type it.")))
        elif day > today:
            flags.append(_flag("warn", _("The date read, %s, is in the future: check it.", day.strftime("%d %b %Y"))))
        ref = " ".join((reading.get("invoice_no") or "").split())
        if not ref:
            flags.append(_flag("warn", _("The invoice number could not be read: type it.")))
        # The PIN: the customer's on file stays; one the invoice adds is saved on it.
        pin = "".join((((reading.get("customer") or {}).get("pin")) or "").split()).upper()
        if pin and pin == "".join((self.env.company.vat or "").split()).upper():
            pin = ""
        on_file = (customer or {}).get("vat") or ""
        if pin and on_file and "".join(on_file.split()).upper() != pin:
            flags.append(_flag("warn", _("The invoice has KRA PIN %(pin)s; the customer has %(file)s on file, which "
                                         "is kept. Change it here if the invoice is right.", pin=pin, file=on_file)))
        elif pin and customer and not on_file:
            flags.append(_flag("info", _("KRA PIN %s from the invoice is saved on the customer.", pin)))
        partner_vat = on_file or pin
        # Terms, and the due date they give.
        terms, terms_flags = self._photo_terms(reading.get("payment_terms"), day or today,
                                               self._photo_day(reading.get("due_date")))
        flags += terms_flags
        if terms and customer and customer.get("payment_term_id") and customer["payment_term_id"] != terms.id:
            flags.append(_flag("info", _("%(terms)s from the invoice; the customer has %(file)s on file. Saving puts "
                                         "%(terms)s on the customer.", terms=terms.name,
                                         file=self.env["account.payment.term"].browse(
                                             customer["payment_term_id"]).name)))
        cu = " ".join((reading.get("cu_invoice_no") or "").split())
        if cu:
            try:
                self._check_sale_cu(cu)
            except UserError as error:
                flags.append(_flag("warn", str(error)))
        flags += self._photo_page_flags(reading, photo_count)
        rows = [row for row in reading.get("lines") or [] if " ".join((row.get("description") or "").split())]
        prices_include, vat_flags = self._photo_prices_include(reading, rows)
        flags += vat_flags
        ctx = self._photo_context(partner, day or today, prices_include, warehouse_id)
        lines = [self._photo_line(ctx, row, self._photo_match(ctx, row)) for row in rows]
        if not lines:
            flags.append(_flag("warn", _("No item lines were read from the photos.")))
        flags += self._photo_total_flags(reading, rows, prices_include)
        for problem in reading.get("problems") or []:
            if " ".join(problem.split()):
                flags.append(_flag("warn", " ".join(problem.split())))
        totals = reading.get("totals") or {}
        return {
            "supplier": customer or False, "supplier_query": query, "customer_choices": choices,
            "purchase_ref": ref, "date": fields.Date.to_string(day) if day else False,
            "partner_vat": partner_vat, "payment_term_id": terms.id or False, "cu_invoice_no": cu,
            "lines": lines, "flags": flags, "total": totals.get("total"),
            "pending": [i for i, line in enumerate(lines) if self._photo_needs_choice(line["match"])],
            # What the second request needs to price and check the lines again.
            "context": {"partner_id": partner.id or False, "date": fields.Date.to_string(day or today),
                        "prices_include": prices_include},
        }

    @api.model
    def _photo_needs_choice(self, match):
        """The rules left the line between close items, or with only weak ones."""
        return bool(match["candidate_ids"]) and not match.get("chosen") and match["confidence"] in (
            matching.CHECK, matching.NONE)

    @api.model
    def _photo_day(self, text):
        try:
            return fields.Date.to_date((text or "").strip()[:10]) if (text or "").strip() else None
        except ValueError:
            return None

    @api.model
    def _photo_context(self, partner, day, prices_include, warehouse_id):
        """What every line of one invoice is matched and checked with."""
        book = self.sudo()
        pricelist = book._sale_pricelist(partner.sudo())
        history = set()
        if partner:
            # Storekeepers need not read Sales: what the customer bought, in sudo.
            history = set(self.env["sale.order.line"].sudo().search([
                ("company_id", "=", self.env.company.id), ("state", "=", "sale"),
                ("order_partner_id", "child_of", partner.commercial_partner_id.id),
            ], order="id desc", limit=2000).product_id.ids)
        return {"partner": partner, "pricelist": pricelist, "day": day, "prices_include": prices_include,
                "catalogue": None, "history": history,
                "warehouse": self._warehouse(warehouse_id), "taxes": self._sale_taxes()}

    @api.model
    def _photo_context_from(self, result, warehouse_id):
        saved = result.get("context") or {}
        partner = self.env["res.partner"].browse(saved.get("partner_id") or []).exists()
        return self._photo_context(partner, fields.Date.to_date(saved.get("date")) or fields.Date.context_today(self),
                                   saved.get("prices_include", True), warehouse_id)

    @api.model
    def _photo_name(self, product):
        return product.with_context(display_default_code=False).display_name

    @api.model
    def _photo_catalogue(self):
        """What a sale line can be: anything that can be sold, stock or not."""
        company = self.env.company
        products = self.env["product.product"].search([
            ("sale_ok", "=", True), ("type", "in", ("consu", "service")), ("company_id", "in", (False, company.id)),
        ], order="default_code, id", limit=CATALOGUE_LIMIT)
        return matching.Catalogue([{
            "id": p.id, "code": p.default_code or "", "name": self._photo_name(p),
            "category": p.categ_id.complete_name or "", "uom": p.uom_id.name,
        } for p in products])

    # ------------------------------------------------------------ the customer
    @api.model
    def _photo_customer(self, printed):
        """The customer and branch the invoice is for: (the customer as the
        window takes it, or False; what to put in the box when not sure; the
        customers it may be; flags)."""
        name = " ".join((printed.get("name") or "").split())
        branch = " ".join((printed.get("branch") or "").split())
        pin = "".join((printed.get("pin") or "").split()).upper()
        if pin == "".join((self.env.company.vat or "").split()).upper():
            pin = ""
        query = ", ".join(part for part in (name, branch) if part)
        if not name and not pin:
            return False, query, [], [_flag("warn", _("The customer's name could not be read: pick the customer."))]
        Partner = self.env["res.partner"]
        leaves = [("name", "ilike", word) for word in matching._name_words(name) if len(word) >= 3][:6]
        if pin:
            leaves.append(("vat", "=ilike", pin))
        companies = Partner.search(["|"] * (len(leaves) - 1) + leaves + [("parent_id", "=", False)], limit=30) \
            if leaves else Partner
        children = Partner.search([("parent_id", "in", companies.ids)], limit=500) if companies else Partner
        count = {company.id: 0 for company in companies}
        for child in children:
            count[child.parent_id.id] = count.get(child.parent_id.id, 0) + 1
        partners = [{"id": p.id, "name": p.name or "", "parent_id": p.parent_id.id or False,
                     "vat": p.commercial_partner_id.vat or "", "children": count.get(p.id, 0)}
                    for p in companies | children]
        found = matching.match_customer(name, branch, pin, partners)
        flags = []

        def window(entry):
            partner = Partner.browse(entry["partner"]["id"])
            return {**self._sale_customer(partner), "name": partner.display_name}

        choices = [window(entry) for entry in ([found["best"]] if found["best"] else []) + found["alternatives"]]
        if found["confidence"] == matching.NONE:
            flags.append(_flag("warn", _("No customer in Odoo matches “%s”. Pick the customer, or create it.", query)))
            return False, query, choices, flags
        best = found["best"]
        for _level, key, has_branches, printed_branch in found["notes"]:
            if key == "branch":
                flags.append(_flag("warn", _(
                    "The invoice is for the branch “%(branch)s”, which is not a contact under %(customer)s%(other)s.",
                    branch=printed_branch.title(), customer=best["company"]["name"],
                    other=_(": pick the branch, or the customer itself") if has_branches
                    else _(". The sale goes to the customer itself"))))
        if found["confidence"] == matching.CHECK:
            flags.append(_flag("warn", _("Not sure which customer “%s” is: pick it.", query)))
            return False, query, choices, flags
        return window(best), query, choices, flags

    # ------------------------------------------------------------ terms, pages, VAT, totals
    @api.model
    def _photo_terms(self, printed, day, due):
        text = " ".join((printed or "").split())
        if not text and not due:
            return self.env["account.payment.term"], []
        terms = self._sale_payment_terms()

        def due_on(term):
            return max((line._get_due_date(day) for line in term.line_ids), default=day)

        found = terms.filtered(lambda t: t.name.casefold() == text.casefold()) if text else terms.browse()
        days = re.search(r"\d+", text)
        if not found and days:
            number = int(days.group())
            found = terms.filtered(lambda t: re.search(rf"(?<!\d){number}(?!\d)", t.name) and len(t.line_ids) == 1)
            found = found.filtered(lambda t: due_on(t) == day + timedelta(days=number)) or found
        if not found and due:
            found = terms.filtered(lambda t: due_on(t) == due)
        term = found[:1]
        flags = []
        if text and not term:
            flags.append(_flag("warn", _("The payment terms on the invoice, “%s”, are not in Odoo: choose them.", text)))
        elif term and due and due_on(term) != due:
            flags.append(_flag("warn", _("The invoice is due on %(printed)s; %(terms)s dates it %(odoo)s. Check the "
                                         "terms and the date.", printed=due.strftime("%d %b %Y"), terms=term.name,
                                         odoo=due_on(term).strftime("%d %b %Y"))))
        return term, flags

    @api.model
    def _photo_page_flags(self, reading, photo_count):
        pages = {p.get("page") for p in reading.get("pages") or [] if (p.get("page") or 0) > 0}
        count = reading.get("page_count") or 0
        missing = sorted(set(range(1, count + 1)) - pages) if count and pages else []
        if missing:
            return [_flag("warn", _("The invoice has %(count)s pages; page %(missing)s is not in the photos. Add a "
                                    "photo of it: its lines are not read.", count=count,
                                    missing=", ".join(str(n) for n in missing)))]
        if count > photo_count and not pages:
            return [_flag("warn", _("The invoice has %(count)s pages and %(photos)s photo(s) were uploaded: check "
                                    "that every page is in them.", count=count, photos=photo_count))]
        return []

    @api.model
    def _photo_prices_include(self, reading, rows):
        """Whether the rates include VAT: from the arithmetic on the paper
        when it says, else as the model read it, else yes (as on the
        store's QuickBooks invoices)."""
        totals = reading.get("totals") or {}
        amounts = [row["amount"] for row in rows if isinstance(row.get("amount"), (int, float))]
        total, before = totals.get("total"), totals.get("before_vat")
        if amounts and len(amounts) == len(rows):
            added = sum(amounts)
            if total and abs(added - total) <= TOTAL_TOLERANCE:
                return True, []
            if before and total and abs(added - before) <= TOTAL_TOLERANCE and abs(total - before) > TOTAL_TOLERANCE:
                return False, []
        said = reading.get("prices_include_vat")
        if said in ("yes", "no"):
            return said == "yes", []
        return True, [_flag("info", _("The invoice does not show whether its rates include VAT: they are read as "
                                      "including it."))]

    @api.model
    def _photo_total_flags(self, reading, rows, prices_include):
        totals = reading.get("totals") or {}
        printed = totals.get("total") if prices_include else totals.get("before_vat")
        amounts = [row.get("amount") for row in rows]
        if printed is None or not rows or any(not isinstance(a, (int, float)) for a in amounts):
            return []
        added = sum(amounts)
        if abs(added - printed) > TOTAL_TOLERANCE:
            return [_flag("warn", _("The lines read add up to %(added)s; the invoice prints %(printed)s. A line may be "
                                    "missing or misread.", added=_money(added), printed=_money(printed)))]
        return []

    @api.model
    def _photo_tax(self, ctx, text, product):
        """The sales VAT the invoice's VAT column names, and flags. KRA
        invoices print the rate ("16.0% S"); a letter alone is read only
        where it cannot mean two things."""
        text = " ".join((text or "").split())
        taxes = ctx["taxes"]
        company = self.env.company
        default = (product.taxes_id.filtered(lambda t: t.company_id == company)[:1] & taxes) if product else taxes[:0]
        number = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text) or re.fullmatch(r"(\d+(?:[.,]\d+)?)", text)
        word = text.casefold()
        if number:
            percent = float(number.group(1).replace(",", "."))
        elif word in ("s", "std", "standard", "vat"):
            percent = 16.0
        elif word in ("z", "zero", "zero rated", "zero-rated", "exempt", "ex", "exempted"):
            percent = 0.0
        else:
            flags = [_flag("warn", _("The VAT on the invoice, “%s”, could not be read: check it.", text))] if text else []
            return default, flags
        same = taxes.filtered(lambda t: t.amount_type == "percent" and abs(t.amount - percent) < 0.001)
        if percent == 0 and len(same) > 1:
            kind = "exempt" if re.search(r"\b(e|ex|exempt\w*)\b", word) else "zero"
            same = same.filtered(lambda t: kind in t.name.casefold()) or same
        if not same:
            return taxes[:0], [_flag("warn", _("There is no sales VAT of %s%% in Odoo: choose the VAT.", f"{percent:g}"))]
        if default and default in same:
            return default, []
        tax = same.filtered(lambda t: bool(t.price_include) == bool(ctx["prices_include"]))[:1] or same[:1]
        flags = []
        if default:
            flags.append(_flag("info", _("The item's VAT in Odoo is %(odoo)s; the invoice says %(printed)s, which is "
                                         "used.", odoo=default.name, printed=text)))
        return tax, flags

    @api.model
    def _photo_pricelist(self, ctx, product, row, tax=None):
        """The customer's price for the item, written as the line's VAT reads
        a price (or as the invoice's rates are, without a VAT), and the
        pricelist's name."""
        book = self.sudo()
        qty = row.get("quantity") if isinstance(row.get("quantity"), (int, float)) and row["quantity"] > 0 else 1.0
        price = book._sale_price(product.sudo(), ctx["partner"].sudo(), ctx["pricelist"], qty,
                                 fields.Date.to_string(ctx["day"]))
        company = self.env.company
        default = product.taxes_id.filtered(lambda t: t.company_id == company)[:1]
        net = _net(price, default)
        if tax is not None:
            shown = _as_tax_reads(net, tax)
        elif ctx["prices_include"] and default and default.amount_type == "percent":
            shown = net * (1 + default.amount / 100)
        else:
            shown = net
        digits = self.env["decimal.precision"].precision_get("Product Price")
        return float_round(shown, precision_digits=digits), ctx["pricelist"].name or _("Sales Price")

    # ------------------------------------------------------------ the lines
    @api.model
    def _photo_match(self, ctx, row):
        """The item for one line read, by the rules: kept with the line so the
        model's choice, if asked, can replace it."""
        if ctx["catalogue"] is None:
            ctx["catalogue"] = self._photo_catalogue()
        catalogue = ctx["catalogue"]
        description = " ".join((row.get("description") or "").split())
        rate = row.get("rate") if isinstance(row.get("rate"), (int, float)) else None
        kwargs = {"code": row.get("code") or "", "unit": row.get("unit") or "", "history": ctx["history"]}
        first = catalogue.match(description, **kwargs)
        # Prices only for the items still in the running: they cost a pricelist lookup each.
        Product = self.env["product.product"]
        prices = {}
        if rate:
            for entry in first["candidates"]:
                product = Product.browse(entry["product"]["id"])
                prices[product.id] = self._photo_pricelist(ctx, product, row)[0]
        found = catalogue.match(description, rate=rate, prices=prices, **kwargs) if prices else first
        best = found["best"]
        # Items sharing next to nothing with the line are no choice to offer.
        ids = [c["product"]["id"] for c in found["candidates"] if c["words"] >= 0.2 or c is best]
        return {
            "product_id": best["product"]["id"] if best else False,
            "confidence": found["confidence"],
            "candidate_ids": ids,
            "close_ids": [c["product"]["id"] for c in found["close"]],
            "notes": {str(c["product"]["id"]): [list(note) for note in c["notes"]] for c in found["candidates"]},
            "excluded": [{"id": c["product"]["id"], "why": c["why"]} for c in found["excluded"]],
            "chosen": False,
        }

    @api.model
    def _photo_window_product(self, ctx, product):
        onhand = self.env["odin.stock.line"]._available(product, ctx["warehouse"])[product.id] \
            if product.is_storable else 0.0
        return {**self._product(product), "stock": product.is_storable, "tracked": product.tracking != "none",
                "onhand": onhand}

    @api.model
    def _photo_line(self, ctx, row, match):
        """One line of the window, from one line read and the item for it,
        with what the person should look at."""
        Product = self.env["product.product"]
        product = Product.browse(match.get("product_id") or []).exists()
        description = " ".join((row.get("description") or "").split())
        qty = row.get("quantity") if isinstance(row.get("quantity"), (int, float)) else None
        rate = row.get("rate") if isinstance(row.get("rate"), (int, float)) else None
        amount = row.get("amount") if isinstance(row.get("amount"), (int, float)) else None
        flags = []
        # The item.
        confidence = match["confidence"]
        chosen = match.get("chosen") or {}
        if not product:
            if chosen:
                flags.append(_flag("warn", _("None of the similar items is this one: %s Pick the item.",
                                             chosen.get("reason") or "")))
            else:
                flags.append(_flag("warn", _("No item in the catalogue matches. Pick the item.")))
        elif chosen:
            text = _("Chosen between %(count)s similar items: %(reason)s",
                     count=chosen.get("count"), reason=chosen.get("reason") or "")
            flags.append(_flag("info" if chosen.get("sure") else "warn", text))
        elif confidence == matching.CHECK:
            flags.append(_flag("warn", _("Not sure this is the item: check it against the photo.")))
        notes = (match.get("notes") or {}).get(str(product.id), []) if product else []
        for level, key, text in notes:
            if key in ("history", "price"):
                continue  # said by the price check below, or not worth saying
            if key == "code" and level == "info":
                flags.append(_flag("info", _("Its code is on the invoice.")))
            else:
                flags.append(_flag(level, f"{text[:1].upper()}{text[1:]}."))
        if product and product.tracking != "none":
            flags.append(_flag("warn", _("%s is tracked by lot or serial number: deliver this sale in Sales.",
                                         product.display_name)))
        for out in match.get("excluded") or []:
            other = Product.browse(out["id"]).exists()
            if other:
                flags.append(_flag("info", _("Not %(item)s: %(why)s.", item=self._photo_name(other),
                                             why="; ".join(out["why"]))))
        # The VAT and the price.
        tax, tax_flags = self._photo_tax(ctx, row.get("vat"), product)
        flags += tax_flags
        digits = self.env["decimal.precision"].precision_get("Product Price")
        cost = None
        if rate is not None:
            cost = rate
            if tax and tax.amount_type == "percent" and tax.amount and bool(tax.price_include) != bool(ctx["prices_include"]):
                cost = rate / (1 + tax.amount / 100) if ctx["prices_include"] else rate * (1 + tax.amount / 100)
                cost = float_round(cost, precision_digits=digits)
                flags.append(_flag("info", _("The rate %(rate)s %(how)s VAT; entered as %(cost)s, as %(tax)s reads a "
                                             "price.", rate=_money(rate), cost=_money(cost), tax=tax.name,
                                             how=_("includes") if ctx["prices_include"] else _("is before"))))
        else:
            flags.append(_flag("warn", _("The rate could not be read: type it.")))
        if qty is None:
            flags.append(_flag("warn", _("The quantity could not be read: type it.")))
        price, pricelist = (None, "")
        if product:
            price, pricelist = self._photo_pricelist(ctx, product, row, tax or None)
            if cost is not None and abs(price - cost) > PRICE_TOLERANCE:
                flags.append(_flag("warn", _("The invoice's rate is %(rate)s; the %(pricelist)s price is %(price)s.",
                                             rate=_money(cost), pricelist=pricelist, price=_money(price))))
        if qty is not None and rate is not None and amount is not None \
                and abs(qty * rate - amount) > max(0.05, 0.005 * abs(amount)):
            flags.append(_flag("warn", _("%(qty)s × %(rate)s is %(product)s; the invoice prints %(amount)s. One of "
                                         "them is misread.", qty=f"{qty:g}", rate=_money(rate),
                                         product=_money(qty * rate), amount=_money(amount))))
        handwriting = " ".join((row.get("handwriting") or "").split())
        if handwriting:
            flags.append(_flag("warn", _("Written by hand on this line: %s", handwriting)))
        unclear = " ".join((row.get("unclear") or "").split())
        if unclear:
            flags.append(_flag("warn", _("Not clear on the photo: %s", unclear)))
        level = "check" if any(f["level"] == "warn" for f in flags) else (
            "likely" if chosen.get("sure") else "sure")
        choice_ids = [i for i in (match.get("close_ids") or match.get("candidate_ids") or [])[:4]
                      if i != product.id] if (level == "check" or not product) else []
        return {
            "product": self._photo_window_product(ctx, product) if product else False,
            "query": "" if product else description,
            "qty": qty, "cost": cost, "tax_id": tax.id or False,
            "note": description if product and not product.is_storable else "",
            "photo": {
                "printed": description, "qty": qty, "rate": rate, "amount": amount, "vat": row.get("vat") or "",
                "page": row.get("photo") or 0, "level": level, "flags": flags,
                "choices": [self._photo_window_product(ctx, p) for p in Product.browse(choice_ids).exists()],
                "price": price, "pricelist": pricelist,
            },
            "read": row, "match": match,
        }

    # ------------------------------------------------------------ saving
    @api.model
    def _photos_given(self, photo_ids, replace_ids):
        """The photos sent with a sale: of this company, and not already
        another live sale's."""
        photos = self.env["odin.stock.photo"].sudo().browse(photo_ids or []).exists()
        replaced = self.env["odin.stock.line"].browse(replace_ids or []).exists().stock_sale_id
        for photo in photos:
            if photo.company_id != self.env.company:
                raise UserError(_("These photos are of another company. Nothing was saved."))
            sale = photo.sale_id
            if sale and sale not in replaced and not sale.undone_on:
                raise UserError(_("These photos are already with sale %s. Nothing was saved.", sale.sale_ref))
            if not sale and photo.user_id != self.env.user:
                raise UserError(_("These photos were uploaded by someone else. Nothing was saved."))
        return photos

    @api.model
    def book_sale_save(self, values, post=True, replace_ids=None):
        photos = self._photos_given(values.get("photo_ids"), replace_ids)
        result = super().book_sale_save(values, post=post, replace_ids=replace_ids)
        if photos:
            sale = self.env["odin.stock.line"].browse(result["ids"]).stock_sale_id
            photos._link(sale)
            result["photos"] = len(photos.attachment_ids)
        return result

    @api.model
    def book_sale_undo(self, line_id):
        """The photos go with the sale entered again, corrected; the undone
        invoice keeps its copy in its chatter."""
        sale = self.env["odin.stock.line"].browse(line_id).exists().stock_sale_id
        photo = sale.photo_ids[:1]
        result = super().book_sale_undo(line_id)
        if photo:
            result["again"]["photo"] = {"id": photo.id, "files": photo._files_info()}
        return result
