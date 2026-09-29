import base64
import hashlib
import logging
from collections import defaultdict
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal

import psycopg2
import pytz

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL, format_date, formatLang

from ..lib import gymkhana_parser as gp
from .pos_item_map import stock_effect

_logger = logging.getLogger(__name__)

POS_TZ = 'Africa/Nairobi'
CENT = Decimal('0.01')


def dec(value):
    """Float read from the ORM -> exact Decimal (floats hold the 2-decimal values exactly enough)."""
    return Decimal(repr(float(value))).quantize(Decimal('0.000001'))


class PosImport(models.Model):
    _name = 'pos.import'
    _description = "POS Day Import"
    _inherit = ['mail.thread']
    _order = 'business_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(compute='_compute_name', store=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    state = fields.Selection([
        ('draft', "Draft"),
        ('review', "Review"),
        ('posted', "Posted"),
        ('cancelled', "Cancelled"),
    ], default='draft', required=True, readonly=True, copy=False, tracking=True)

    pdf_file = fields.Binary(string="Group Sales Register (PDF)", attachment=True, copy=False)
    pdf_filename = fields.Char(copy=False)
    file_hash = fields.Char(readonly=True, copy=False, index=True)
    parse_error = fields.Char(readonly=True, copy=False)

    business_date = fields.Date(readonly=True, copy=False, index=True, tracking=True)
    outlet = fields.Char(readonly=True, copy=False)
    pdf_amount = fields.Monetary(string="PDF Amount excl. Tax", readonly=True, copy=False)
    pdf_tax = fields.Monetary(string="PDF Tax", readonly=True, copy=False)
    pdf_net = fields.Monetary(string="PDF Grand Total", readonly=True, copy=False, tracking=True)
    pdf_line_count = fields.Integer(string="PDF Lines", readonly=True, copy=False)

    line_ids = fields.One2many('pos.import.line', 'import_id', string="Lines", copy=False)
    unmatched_line_ids = fields.One2many(
        'pos.import.line', 'import_id', string="Unmatched Lines",
        domain=[('match_state', '!=', 'ok')])
    unmatched_count = fields.Integer(compute='_compute_unmatched_count')

    order_ids = fields.One2many('sale.order', 'pos_import_id', string="Sale Orders", readonly=True, copy=False)
    picking_ids = fields.Many2many('stock.picking', string="Deliveries", compute='_compute_picking_ids')
    invoice_id = fields.Many2one('account.move', string="Invoice", readonly=True, copy=False)
    refund_id = fields.Many2one('account.move', string="Credit Note", readonly=True, copy=False)
    return_picking_ids = fields.Many2many(
        'stock.picking', 'pos_import_return_picking_rel', 'import_id', 'picking_id',
        string="Returns", readonly=True, copy=False)
    order_count = fields.Integer(compute='_compute_counts')
    picking_count = fields.Integer(compute='_compute_counts')
    stock_result = fields.Json(readonly=True, copy=False, help="Stock before/after, recorded when the day was posted.")

    review_summary = fields.Json(compute='_compute_review')
    review_detail = fields.Json(compute='_compute_review')
    can_post = fields.Boolean(compute='_compute_review')
    can_undo = fields.Boolean(compute='_compute_can_undo')

    _date_uniq = models.UniqueIndex(
        "(company_id, business_date) WHERE state IN ('review', 'posted')",
        "This date has already been imported.")
    _hash_uniq = models.UniqueIndex(
        "(company_id, file_hash) WHERE state IN ('review', 'posted')",
        "This file has already been imported.")

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends('business_date')
    def _compute_name(self):
        for imp in self:
            imp.name = f"POS {imp.business_date.isoformat()}" if imp.business_date else self.env._("New POS import")

    @api.depends('line_ids.match_state')
    def _compute_unmatched_count(self):
        for imp in self:
            imp.unmatched_count = len(imp.line_ids.filtered(lambda l: l.match_state != 'ok'))

    @api.depends('order_ids.picking_ids')
    def _compute_picking_ids(self):
        for imp in self:
            imp.picking_ids = imp.order_ids.picking_ids.filtered(lambda p: not p.return_id)

    @api.depends('order_ids', 'picking_ids')
    def _compute_counts(self):
        for imp in self:
            imp.order_count = len(imp.order_ids)
            imp.picking_count = len(imp.picking_ids)

    @api.depends('state', 'invoice_id.payment_state')
    def _compute_can_undo(self):
        for imp in self:
            imp.can_undo = imp.state == 'posted' and imp.invoice_id.payment_state == 'not_paid'

    @api.depends('state', 'line_ids.product_id', 'line_ids.match_state', 'business_date',
                 'company_id.pos_import_partner_id', 'company_id.pos_import_tax_ids',
                 'company_id.pos_import_rounding_product_id', 'invoice_id')
    def _compute_review(self):
        for imp in self:
            if imp.state == 'draft' or not imp.id:
                imp.review_summary = imp.review_detail = False
                imp.can_post = False
                continue
            summary, detail = imp._pos_review_data()
            imp.review_summary = summary
            imp.review_detail = detail
            imp.can_post = imp.state == 'review' and not summary['blocking'] and not imp.unmatched_count

    # ------------------------------------------------------------------
    # Screen 1: drop the PDF
    # ------------------------------------------------------------------

    @api.onchange('pdf_file')
    def _onchange_pdf_file(self):
        """Instant feedback while the file is still only in the form."""
        preview = {'parse_error': False, 'business_date': False, 'outlet': False,
                   'pdf_amount': 0.0, 'pdf_tax': 0.0, 'pdf_net': 0.0, 'pdf_line_count': 0}
        if self.pdf_file:
            try:
                result = self._pos_parse_and_check(base64.b64decode(self.pdf_file))[0]
            except UserError as e:
                preview['parse_error'] = e.args[0]
            else:
                preview.update(self._pos_header_values(result))
        self.update(preview)

    def action_review(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(self.env._("This import has already been read."))
        if not self.pdf_file:
            raise UserError(self.env._("Drop the day's Group Sales Register PDF first."))
        data = base64.b64decode(self.pdf_file)
        result, file_hash = self._pos_parse_and_check(data)
        bars = self._pos_bars()
        mapping = {m.pos_key: m.product_id for m in self.env['pos.item.map'].search(
            [('company_id', '=', self.company_id.id)])}
        lines = []
        for ln in result['lines']:
            lines.append(Command.create({
                'sequence': ln['sequence'],
                'bar_id': bars[ln['group']].id,
                'group_name': ln['group'],
                'sub_group': ln['sub_group'],
                'pos_key': ln['key'],
                'pos_name': ln['name'],
                'pos_unit': ln['unit'],
                'bar_suffix': ln['bar_suffix'],
                'item_count': int(ln['count']),
                'rate': float(ln['rate']),
                'qty': float(ln['qty']),
                'amount': float(ln['amount']),
                'disc': float(ln['disc']),
                'tax': float(ln['tax']),
                'net': float(ln['net']),
                'page': ln['page'],
                'rate_mismatch': ln['rate_mismatch'],
                'product_id': mapping.get(ln['key'], self.env['product.product']).id,
            }))
        self.with_context(pos_import_system=True).write({
            **self._pos_header_values(result),
            'file_hash': file_hash,
            'parse_error': False,
            'state': 'review',
            'line_ids': lines,
        })
        return True

    def _pos_header_values(self, result):
        header, total = result['header'], result['grand_total']
        return {
            'business_date': header['date_from'],
            'outlet': header['outlet'],
            'pdf_amount': float(total['amount']),
            'pdf_tax': float(total['tax']),
            'pdf_net': float(total['net']),
            'pdf_line_count': len(result['lines']),
        }

    def _pos_bars(self):
        """Active bars of the company, by POS group name."""
        bars = self.env['pos.import.bar'].search([('company_id', '=', self.company_id.id)])
        return {bar.pos_group_name: bar for bar in bars}

    def _pos_parse_and_check(self, data):
        """Parse the PDF and apply every hard block that doesn't need the item
        mapping. Raises UserError with a single plain-English sentence."""
        company = self.company_id or self.env.company
        bars = self._pos_bars()
        suffixes = tuple({bar.pos_suffix for bar in bars.values()} | set(gp.DEFAULT_SUFFIXES))
        try:
            result = gp.parse(data, suffixes=suffixes)
        except gp.ParseError as e:
            raise UserError(str(e)) from None
        header = result['header']
        day = header['date_from']
        if header['date_to'] != day:
            raise UserError(self.env._(
                "The report covers %(start)s to %(end)s; it must cover exactly one day.",
                start=format_date(self.env, day), end=format_date(self.env, header['date_to'])))
        if not company.pos_import_outlet:
            raise UserError(self.env._("Set the POS outlet in Settings before importing."))
        if header['outlet'].casefold() != company.pos_import_outlet.strip().casefold():
            raise UserError(self.env._(
                "This report is for the outlet \"%(outlet)s\", but %(company)s imports \"%(expected)s\".",
                outlet=header['outlet'], company=company.name, expected=company.pos_import_outlet))
        self._pos_check_date(day, company)
        file_hash = hashlib.sha256(data).hexdigest()
        self._pos_check_duplicates(day, file_hash, company)
        for group in result['groups']:
            bar = bars.get(group['name'])
            if not bar:
                raise UserError(self.env._(
                    "The group \"%(group)s\" isn't configured: add it under POS Import > "
                    "Configuration > Bars.", group=group['name']))
            for sub in group['subgroups']:
                for ln in sub['lines']:
                    if ln['bar_suffix'] != bar.pos_suffix:
                        raise UserError(self.env._(
                            "Page %(page)s: %(item)s is listed under %(group)s, whose items end in (%(suffix)s).",
                            page=ln['page'], item=ln['raw_name'], group=group['name'], suffix=bar.pos_suffix))
        return result, file_hash

    def _pos_check_date(self, day, company):
        today = fields.Date.context_today(self.with_context(tz=POS_TZ))
        if day > today:
            raise UserError(self.env._("%(day)s is in the future.", day=format_date(self.env, day)))
        journal = self._pos_sale_journal(company)
        violations = company._get_violated_lock_dates(day, True, journal)
        if violations:
            raise UserError(self.env._(
                "%(day)s is locked in accounting: %(locks)s.",
                day=format_date(self.env, day), locks=company._format_lock_dates(violations)))

    def _pos_check_duplicates(self, day, file_hash, company):
        others = self.search([
            ('company_id', '=', company.id), ('state', 'in', ('review', 'posted')),
            ('id', 'not in', self.ids),
            '|', ('business_date', '=', day), ('file_hash', '=', file_hash),
        ])
        for other in others:
            state = dict(self._fields['state']._description_selection(self.env))[other.state].lower()
            if other.business_date == day:
                raise UserError(self.env._(
                    "%(day)s has already been imported (%(name)s, %(state)s).",
                    day=format_date(self.env, day), name=other.name, state=state))
            raise UserError(self.env._(
                "This file has already been imported (%(name)s, %(state)s).", name=other.name, state=state))

    def _pos_sale_journal(self, company):
        return self.env['account.journal'].search([
            *self.env['account.journal']._check_company_domain(company),
            ('type', '=', 'sale')], limit=1)

    def unlink(self):
        if any(imp.state in ('posted', 'cancelled') for imp in self):
            raise UserError(self.env._("A posted or undone import is kept for the record and can't be deleted."))
        return super().unlink()

    # ------------------------------------------------------------------
    # Screen 2: review
    # ------------------------------------------------------------------

    def _pos_blocking_issues(self):
        """Everything red except unmatched lines (shown in their own banner)."""
        self.ensure_one()
        company = self.company_id
        issues = []
        if not company.pos_import_partner_id:
            issues.append(self.env._("Set the POS customer (the club) in Settings."))
        taxes = company.pos_import_tax_ids
        if not taxes:
            issues.append(self.env._("Set the POS taxes in Settings."))
        elif not all(taxes.mapped('price_include')):
            issues.append(self.env._("The POS taxes must be price-included: the PDF's net amounts include tax."))
        elif any(t.company_id != company or t.type_tax_use != 'sale' for t in taxes):
            issues.append(self.env._("The POS taxes must be sales taxes of %(company)s.", company=company.name))
        if not company.pos_import_rounding_product_id:
            issues.append(self.env._("Set the POS rounding product in Settings."))
        for bar in self.line_ids.bar_id:
            if not bar.active:
                issues.append(self.env._("The bar %(bar)s has been archived.", bar=bar.name))
        if self.state == 'review':
            try:
                self._pos_check_date(self.business_date, company)
            except UserError as e:
                issues.append(e.args[0])
        return issues

    def _pos_explode(self, product, qty):
        """Components (product, qty in the component's UoM) a kit deducts,
        mirroring stock.move.action_explode() (which reads BoMs as superuser too)."""
        bom = self.env['mrp.bom'].sudo()._bom_find(product, company_id=self.company_id.id, bom_type='phantom')[product]
        if not bom:
            return [(product, qty)]
        factor = product.uom_id._compute_quantity(qty, bom.product_uom_id) / bom.product_qty
        _boms, lines = bom.explode(product, factor, picking_type=bom.picking_type_id)
        result = []
        for bom_line, data in lines:
            component = bom_line.product_id.with_env(self.env)
            if component.type != 'consu':
                continue
            result.append((component, bom_line.product_uom_id._compute_quantity(data['qty'], component.uom_id, round=False)))
        return result

    def _pos_stock_preview(self):
        """{bar: {product: qty}} deducted from each bar, after kits are exploded."""
        deductions = defaultdict(lambda: defaultdict(float))
        sources = defaultdict(lambda: defaultdict(list))
        revenue_only = defaultdict(list)
        for line in self.line_ids.filtered(lambda l: l.match_state == 'ok'):
            effect = stock_effect(line.product_id)
            label = f"{line.pos_name} ({line.pos_unit})" if line.pos_unit else line.pos_name
            if effect == 'storable':
                deductions[line.bar_id][line.product_id] += line.qty
                sources[line.bar_id][line.product_id].append(label)
            elif effect == 'kit':
                for component, qty in self._pos_explode(line.product_id, line.qty):
                    if component.is_storable:
                        deductions[line.bar_id][component] += qty
                        sources[line.bar_id][component].append(label)
                    else:
                        revenue_only[line.bar_id].append(label)
            else:
                revenue_only[line.bar_id].append(label)
        return deductions, sources, revenue_only

    def _pos_on_hand(self, location, products):
        groups = self.env['stock.quant']._read_group(
            [('location_id', 'child_of', location.id), ('product_id', 'in', products.ids)],
            ['product_id'], ['quantity:sum'])
        return {product: qty for product, qty in groups}

    def _pos_money_preview(self):
        """Invoice total and taxes exactly as Odoo will compute them."""
        company = self.company_id
        AccountTax = self.env['account.tax']
        base_lines = []
        for bar, lines in self._pos_lines_by_bar().items():
            for vals in self._pos_order_line_values(bar, lines):
                base_lines.append(AccountTax._prepare_base_line_for_taxes_computation(
                    vals, product_id=vals['product'], tax_ids=company.pos_import_tax_ids,
                    price_unit=vals['price_unit'], quantity=vals['qty'], currency_id=self.currency_id,
                    computation_key=self._pos_computation_key(bar)))
        AccountTax._add_tax_details_in_base_lines(base_lines, company)
        AccountTax._round_base_lines_tax_details(base_lines, company)
        return AccountTax._get_tax_totals_summary(base_lines=base_lines, currency=self.currency_id, company=company)

    def _pos_lines_by_bar(self):
        by_bar = {}
        for line in self.line_ids.sorted('sequence'):
            by_bar.setdefault(line.bar_id, self.env['pos.import.line'])
            by_bar[line.bar_id] |= line
        return by_bar

    def _pos_review_data(self):
        self.ensure_one()
        currency = self.currency_id

        def money(amount):
            return formatLang(self.env, amount, digits=currency.decimal_places)

        def qty(amount, uom=None):
            return formatLang(self.env, amount, digits=uom and max(0, -Decimal(str(uom.rounding)).as_tuple().exponent) or 2)

        by_bar = self._pos_lines_by_bar()
        configured = self.env['pos.import.bar'].search([('company_id', '=', self.company_id.id)])
        blocking = self._pos_blocking_issues() if self.state == 'review' else []
        warnings = []
        day = format_date(self.env, self.business_date, date_format='EEEE d MMM y')

        cards = [{
            'id': bar.id, 'name': bar.name, 'code': bar.pos_suffix,
            'total': money(sum(lines.mapped('net'))), 'lines': len(lines),
        } for bar, lines in by_bar.items()]
        for bar in configured - self.line_ids.bar_id:
            cards.append({'id': bar.id, 'name': bar.name, 'code': bar.pos_suffix, 'total': money(0), 'lines': 0, 'empty': True})
            warnings.append(self.env._("%(bar)s has no sales on this day.", bar=bar.name))

        # ---- stock -------------------------------------------------------
        stock_tabs = []
        if self.state in ('posted', 'cancelled') and self.stock_result:
            stock_tabs = self.stock_result
        elif self.state == 'review':
            deductions, sources, revenue_only = self._pos_stock_preview()
            for bar in by_bar:
                products = self.env['product.product'].union(*deductions[bar].keys())
                on_hand = self._pos_on_hand(bar.location_id, products)
                rows = []
                for product in products.sorted('display_name'):
                    taken = deductions[bar][product]
                    before = on_hand.get(product, 0.0)
                    after = before - taken
                    negative = product.uom_id.compare(after, 0) < 0
                    rows.append({
                        'product': product.display_name, 'uom': product.uom_id.name,
                        'qty': qty(taken, product.uom_id), 'before': qty(before, product.uom_id),
                        'after': qty(after, product.uom_id), 'negative': negative,
                        'from': ", ".join(dict.fromkeys(sources[bar][product])),
                    })
                negatives = [r for r in rows if r['negative']]
                if negatives:
                    warnings.append(self.env._(
                        "%(bar)s: %(count)s product(s) go below zero after this day.", bar=bar.name, count=len(negatives)))
                stock_tabs.append({
                    'bar_id': bar.id, 'bar': bar.name, 'location': bar.location_id.display_name,
                    'rows': rows, 'revenue_only': sorted(set(revenue_only[bar])),
                })

        # ---- prices --------------------------------------------------------
        price_rows = []
        for line in self.line_ids.filtered(lambda l: l.match_state == 'ok' and l.price_warning):
            price_rows.append({'bar_id': line.bar_id.id, 'item': line.pos_name, 'unit': line.pos_unit,
                               'product': line.product_id.display_name, 'message': line.price_warning})
        if price_rows and self.state == 'review':
            warnings.append(self.env._("%(count)s line(s) are priced differently from the Odoo list price "
                                       "(happy hour or a price change?).", count=len(price_rows)))
        rate_rows = self.line_ids.filtered('rate_mismatch')
        if rate_rows and self.state == 'review':
            warnings.append(self.env._(
                "Check the quantity of %(items)s: rate x quantity doesn't match the amount on the PDF.",
                items=", ".join(f"{l.pos_name} ({l.bar_id.name})" for l in rate_rows)))

        # ---- money -----------------------------------------------------------
        money_data = {'pdf_net': money(self.pdf_net), 'pdf_tax': money(self.pdf_tax), 'pdf_amount': money(self.pdf_amount)}
        if self.invoice_id:
            totals = self.invoice_id.tax_totals
            money_data['source'] = 'invoice'
        elif self.state == 'review' and not self.unmatched_count and self.company_id.pos_import_tax_ids:
            totals = self._pos_money_preview()
            money_data['source'] = 'preview'
        else:
            totals = None
        if totals:
            odoo_tax = totals['tax_amount_currency']
            money_data.update({
                'total': money(totals['total_amount_currency']),
                'untaxed': money(totals['base_amount_currency']),
                'taxes': [{'name': g['group_name'], 'amount': money(g['tax_amount_currency'])}
                          for subtotal in totals['subtotals'] for g in subtotal['tax_groups']],
                'odoo_tax': money(odoo_tax),
                'tax_difference': money(odoo_tax - self.pdf_tax),
                'total_matches': not currency.compare_amounts(totals['total_amount_currency'], self.pdf_net),
            })
            if not money_data['total_matches'] and self.state == 'review':
                blocking.append(self.env._(
                    "The invoice would total %(odoo)s but the PDF says %(pdf)s.",
                    odoo=money(totals['total_amount_currency']), pdf=money(self.pdf_net)))
        money_data['bars'] = [{'name': bar.name, 'net': money(sum(lines.mapped('net'))),
                               'untaxed': money(sum(lines.mapped('amount'))), 'lines': len(lines),
                               'analytic': bar.analytic_account_id.display_name} for bar, lines in by_bar.items()]

        # ---- records -----------------------------------------------------------
        goods = {bar for bar, lines in by_bar.items()
                 if any(l.product_id.type == 'consu' for l in lines if l.match_state == 'ok')}
        records = {
            'orders': len(by_bar),
            'deliveries': [bar.picking_type_id.sequence_code or bar.picking_type_id.name for bar in by_bar if bar in goods],
            'invoices': 1,
        }
        if self.state in ('posted', 'cancelled'):
            records['links'] = {
                'orders': [{'model': 'sale.order', 'id': o.id, 'name': o.name, 'extra': o.pos_import_bar_id.name}
                           for o in self.order_ids],
                'pickings': [{'model': 'stock.picking', 'id': p.id, 'name': p.name, 'extra': p.picking_type_id.name}
                             for p in self.picking_ids],
                'invoice': self.invoice_id and [{'model': 'account.move', 'id': self.invoice_id.id,
                                                 'name': self.invoice_id.name,
                                                 'extra': money(self.invoice_id.amount_total)}] or [],
                'undo': [{'model': 'stock.picking', 'id': p.id, 'name': p.name, 'extra': self.env._("Return")}
                         for p in self.return_picking_ids]
                        + ([{'model': 'account.move', 'id': self.refund_id.id, 'name': self.refund_id.name,
                             'extra': self.env._("Credit note")}] if self.refund_id else []),
            }

        summary = {
            'title': f"{day} · {currency.name} {money(self.pdf_net)} · {self.pdf_line_count} lines",
            'state': self.state,
            'bars': cards,
            'blocking': blocking,
            'warnings': warnings,
        }
        detail = {
            'state': self.state,
            'money': money_data,
            'stock': stock_tabs,
            'prices': price_rows,
            'records': records,
        }
        return summary, detail

    # ------------------------------------------------------------------
    # Posting: one transaction, all or nothing
    # ------------------------------------------------------------------

    def action_post(self):
        self.ensure_one()
        # 1. Lock the row, then check the state: with the unique indexes, a
        #    double click (or two users) can't post the same day twice.
        self.env.cr.execute(SQL("SELECT state FROM pos_import WHERE id = %s FOR UPDATE", self.id))
        self.invalidate_recordset(['state'])
        if self.state != 'review':
            raise UserError(self.env._("This import is not waiting for review (it is %(state)s).", state=self.state))
        self._pos_check_before_posting()
        try:
            with self.env.cr.savepoint():
                self._pos_post_day()
        except (UserError, ValidationError, psycopg2.Error):
            raise
        except Exception as e:
            _logger.exception("POS import %s: posting failed", self.name)
            raise UserError(self.env._("Posting failed and nothing was saved: %(error)s", error=e)) from e
        return True

    def _pos_check_before_posting(self):
        """Re-validate everything at the last moment, from the stored PDF."""
        if self.unmatched_count:
            raise UserError(self.env._("%(count)s line(s) are not matched to a usable product.", count=self.unmatched_count))
        issues = self._pos_blocking_issues()
        if issues:
            raise UserError(issues[0])
        result, file_hash = self._pos_parse_and_check(base64.b64decode(self.pdf_file))
        if file_hash != self.file_hash:
            raise UserError(self.env._("The stored PDF has changed since it was reviewed."))
        pdf_lines = [(ln['key'], ln['group'], dec(ln['qty']), dec(ln['net'])) for ln in result['lines']]
        our_lines = [(l.pos_key, l.group_name, dec(l.qty), dec(l.net)) for l in self.line_ids.sorted('sequence')]
        if pdf_lines != our_lines:
            raise UserError(self.env._("The import lines no longer match the PDF; delete this import and drop the PDF again."))
        if any(bar.pos_group_name != group for bar, group in
               ((l.bar_id, l.group_name) for l in self.line_ids)):
            raise UserError(self.env._("The bar configuration changed since the PDF was read; delete this import and drop the PDF again."))

    def _pos_business_datetime(self):
        """Business date 23:59 Nairobi time, as a naive UTC datetime."""
        local = pytz.timezone(POS_TZ).localize(datetime.combine(self.business_date, time(23, 59)))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _pos_computation_key(self, bar):
        """Tax rounding key of a bar's lines (sale.order.line.extra_tax_data, copied
        to the invoice lines). With 'round globally', Odoo spreads the rounding
        cents over every line it rounds together: on the one invoice of the day
        that would move a cent or two from one bar to another. Rounding each bar
        on its own keeps every bar's invoiced total (and so its analytic split)
        equal to its group total on the PDF, and the invoice equal to the grand total."""
        return f"pos_import_bar,{bar.id}"

    def _pos_order_line_values(self, bar, lines):
        """Order lines of one bar: price_unit = Net / Qty (tax included), plus a
        "POS rounding" line when a Net can't be split exactly, so the order
        still totals the PDF's net to the cent."""
        company = self.company_id
        precision = self.env['decimal.precision'].precision_get('Product Price')
        quantum = Decimal(1).scaleb(-precision)
        values, expected, priced = [], Decimal(0), Decimal(0)
        for line in lines.sorted('sequence'):
            q, net = dec(line.qty), dec(line.net).quantize(CENT)
            price_unit = (net / q).quantize(quantum, ROUND_HALF_UP)
            values.append({'line': line, 'product': line.product_id, 'qty': float(q), 'price_unit': float(price_unit)})
            expected += net
            priced += (price_unit * q).quantize(CENT, ROUND_HALF_UP)
        difference = expected - priced
        if difference:
            values.append({'line': None, 'product': company.pos_import_rounding_product_id,
                           'qty': 1.0, 'price_unit': float(difference)})
        return values

    def _pos_post_day(self):
        company = self.company_id
        partner = company.pos_import_partner_id
        taxes = company.pos_import_tax_ids
        when = self._pos_business_datetime()
        day = self.business_date
        by_bar = self._pos_lines_by_bar()

        # 2. One sale order per bar present in the PDF
        orders = self.env['sale.order']
        for bar, lines in by_bar.items():
            analytic = {str(bar.analytic_account_id.id): 100}
            order_lines = [Command.create({
                'product_id': vals['product'].id,
                'product_uom_qty': vals['qty'],
                'product_uom_id': vals['product'].uom_id.id,
                'price_unit': vals['price_unit'],
                'discount': 0.0,
                'tax_ids': [Command.set(taxes.ids)],
                'analytic_distribution': analytic,
                'extra_tax_data': {'computation_key': self._pos_computation_key(bar)},
                **({'name': self.env._("POS rounding")} if vals['line'] is None else {}),
            }) for vals in self._pos_order_line_values(bar, lines)]
            orders |= self.env['sale.order'].with_company(company).create({
                'company_id': company.id,
                'partner_id': partner.id,
                'partner_invoice_id': partner.id,
                'partner_shipping_id': bar.delivery_partner_id.id,
                'date_order': when,
                'warehouse_id': (bar.picking_type_id.warehouse_id or bar.location_id.warehouse_id).id,
                'client_order_ref': f"{self.name} {bar.name}",
                'origin': self.name,
                'pos_import_id': self.id,
                'pos_import_bar_id': bar.id,
                'order_line': order_lines,
            })
        for order in orders:
            group_net = sum(dec(n) for n in by_bar[order.pos_import_bar_id].mapped('net'))
            if order.currency_id != company.currency_id:
                raise UserError(self.env._("The order for %(bar)s is not in %(currency)s; check the customer's pricelist.",
                                           bar=order.pos_import_bar_id.name, currency=company.currency_id.name))
            if order.currency_id.compare_amounts(order.amount_total, float(group_net)):
                raise UserError(self.env._("The %(bar)s order totals %(odoo)s but the PDF says %(pdf)s.",
                                           bar=order.pos_import_bar_id.name, odoo=order.amount_total, pdf=group_net))

        # 3. Confirm. The deliveries are born with the bar's SAL operation type
        #    and the bar as source (see stock.rule), before any reservation.
        orders.action_confirm()
        pickings = orders.picking_ids
        for order in orders:
            bar = order.pos_import_bar_id
            for picking in order.picking_ids:
                wrong = picking.move_ids.filtered(lambda m: m.location_id != bar.location_id)
                if picking.picking_type_id != bar.picking_type_id or picking.location_id != bar.location_id or wrong:
                    raise UserError(self.env._(
                        "The delivery of %(bar)s would not take its stock from %(location)s.",
                        bar=bar.name, location=bar.location_id.display_name))

        # 4. Quantities = demand, validate, then back-date to 23:59 of the day
        moves = pickings.move_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
        for move in moves:
            move.quantity = move.product_uom_qty
        moves.picked = True
        pickings.with_context(
            skip_backorder=True, picking_ids_not_to_backorder=pickings.ids,
            skip_sms=True, force_period_date=day,
        ).button_validate()
        not_done = pickings.filtered(lambda p: p.state != 'done')
        if not_done:
            raise UserError(self.env._("The delivery %(name)s could not be validated.", name=not_done[0].name))
        done_moves = pickings.move_ids.filtered(lambda m: m.state == 'done')
        pickings.write({'date_done': when})
        done_moves.write({'date': when})
        done_moves.move_line_ids.write({'date': when})
        # Services (and anything else not delivered through stock) are delivered
        # on the day too, so a "deliver then invoice" policy still invoices them.
        for line in orders.order_line.filtered(lambda l: l.qty_delivered_method == 'manual'):
            line.qty_delivered = line.product_uom_qty

        # 5. One consolidated invoice for the day
        invoice = orders._create_invoices()
        if len(invoice) != 1:
            raise UserError(self.env._("Odoo made %(count)s invoices for this day instead of one.", count=len(invoice)))
        invoice.write({'invoice_date': day, 'pos_import_id': self.id})
        invoice.action_post()
        if invoice.state != 'posted':
            raise UserError(self.env._("The invoice could not be posted."))
        if invoice.currency_id.compare_amounts(invoice.amount_total, self.pdf_net):
            raise UserError(self.env._("The invoice totals %(odoo)s but the PDF says %(pdf)s; nothing was posted.",
                                       odoo=invoice.amount_total, pdf=self.pdf_net))
        if invoice.invoice_date != day or invoice.date != day:
            raise UserError(self.env._("The invoice could not be dated %(day)s.", day=format_date(self.env, day)))

        self.write({
            'state': 'posted',
            'invoice_id': invoice.id,
            'stock_result': self._pos_stock_outcome(orders),
        })
        self.message_post(body=self.env._(
            "Day posted: %(orders)s, %(pickings)s, invoice %(invoice)s.",
            orders=", ".join(orders.mapped('name')), pickings=", ".join(pickings.mapped('name')),
            invoice=invoice.name))

    def _pos_stock_outcome(self, orders):
        """What actually left each bar, from the validated moves."""
        tabs = []
        for order in orders:
            bar = order.pos_import_bar_id
            taken = defaultdict(float)
            for move in order.picking_ids.move_ids.filtered(lambda m: m.state == 'done' and m.product_id.is_storable):
                taken[move.product_id] += move.product_qty
            products = self.env['product.product'].union(*taken.keys())
            on_hand = self._pos_on_hand(bar.location_id, products)
            rows = []
            for product in products.sorted('display_name'):
                after = on_hand.get(product, 0.0)
                digits = max(0, -Decimal(str(product.uom_id.rounding)).as_tuple().exponent)
                rows.append({
                    'product': product.display_name, 'uom': product.uom_id.name,
                    'qty': formatLang(self.env, taken[product], digits=digits),
                    'before': formatLang(self.env, after + taken[product], digits=digits),
                    'after': formatLang(self.env, after, digits=digits),
                    'negative': product.uom_id.compare(after, 0) < 0,
                })
            revenue = [l.pos_name for l in order.pos_import_id.line_ids
                       if l.bar_id == bar and stock_effect(l.product_id) == 'revenue']
            tabs.append({'bar_id': bar.id, 'bar': bar.name, 'location': bar.location_id.display_name,
                         'rows': rows, 'revenue_only': sorted(set(revenue)), 'posted': True})
        return tabs

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def action_undo(self):
        self.ensure_one()
        if not self.env.user.has_group('gymkhana_pos_import.group_pos_import_manager'):
            raise AccessError(self.env._("Only a POS import administrator can undo a posted day."))
        self.env.cr.execute(SQL("SELECT state FROM pos_import WHERE id = %s FOR UPDATE", self.id))
        self.invalidate_recordset(['state'])
        if self.state != 'posted':
            raise UserError(self.env._("Only a posted day can be undone."))
        invoice = self.invoice_id
        if invoice.state != 'posted' or invoice.payment_state != 'not_paid':
            raise UserError(self.env._("The invoice %(invoice)s is paid or partly paid; the day can't be undone.",
                                       invoice=invoice.name))
        self._pos_check_date(self.business_date, self.company_id)
        day, when = self.business_date, self._pos_business_datetime()

        # Return every delivery in full, dated like the delivery it cancels out.
        returns = self.env['stock.picking']
        for picking in self.picking_ids.filtered(lambda p: p.state == 'done'):
            wizard = self.env['stock.return.picking'].with_context(
                active_id=picking.id, active_model='stock.picking').create({'picking_id': picking.id})
            for line in wizard.product_return_moves:
                line.quantity = line.move_id.quantity
            returns |= wizard._create_return()
        moves = returns.move_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
        for move in moves:
            move.quantity = move.product_uom_qty
        moves.picked = True
        returns.with_context(skip_backorder=True, picking_ids_not_to_backorder=returns.ids,
                             skip_sms=True, force_period_date=day).button_validate()
        if returns.filtered(lambda p: p.state != 'done'):
            raise UserError(self.env._("The returns could not be validated."))
        returns.write({'date_done': when})
        returns.move_ids.write({'date': when})
        returns.move_ids.move_line_ids.write({'date': when})

        # Credit note for the full invoice, reconciled with it.
        refund = invoice._reverse_moves([{
            'invoice_date': day, 'date': day,
            'ref': self.env._("Undo of %(name)s", name=self.name),
            'pos_import_id': self.id,
        }], cancel=True)
        if refund.state != 'posted' or invoice.payment_state != 'reversed':
            raise UserError(self.env._("The credit note could not be reconciled with %(invoice)s.", invoice=invoice.name))

        # The orders are now fully returned and credited: cancel them so they
        # don't reappear as "to invoice".
        orders = self.order_ids
        orders.filtered('locked').action_unlock()
        orders.action_cancel()

        self.write({'state': 'cancelled', 'refund_id': refund.id, 'return_picking_ids': [Command.set(returns.ids)]})
        self.message_post(body=self.env._(
            "Day undone: returns %(returns)s, credit note %(refund)s. The date can be imported again.",
            returns=", ".join(returns.mapped('name')), refund=refund.name))
        return True

    # ------------------------------------------------------------------
    # Navigation (screen 3)
    # ------------------------------------------------------------------

    def _pos_action(self, model, records, name):
        action = {'type': 'ir.actions.act_window', 'res_model': model, 'name': name,
                  'domain': [('id', 'in', records.ids)], 'view_mode': 'list,form'}
        if len(records) == 1:
            action.update(view_mode='form', res_id=records.id, views=[(False, 'form')])
        return action

    def action_open_orders(self):
        return self._pos_action('sale.order', self.order_ids, self.env._("Sale Orders"))

    def action_open_pickings(self):
        return self._pos_action('stock.picking', self.picking_ids | self.return_picking_ids, self.env._("Deliveries"))

    def action_open_invoices(self):
        return self._pos_action('account.move', self.invoice_id | self.refund_id, self.env._("Invoice"))
