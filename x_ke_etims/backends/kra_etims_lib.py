# -*- coding: utf-8 -*-
"""KRA's OSCU API through the MIT-licensed ``etims`` library (MutaiT/etims).

Experimental, and labelled so wherever a device can be set to it: the library
is an alpha (0.1.0a1) and its author has not yet verified it against KRA's live
sandbox. It is offered because it is a careful second reading of the
specification and comes with the fake KRA the tests for *both* KRA backends
run against.

It is imported lazily and is not in the manifest's external dependencies: a
database without it installs and runs, and this backend simply reports itself
unavailable. Its own retries are switched off -- the queue owns retrying, and
two retry loops stacked on each other would double the wait and hide the
attempt count.

The library computes invoice totals from its line amounts. Lines are passed
with ``amounts`` set to Odoo's own figures, so what KRA signs is the invoice,
not the library's recalculation of it.
"""

from datetime import datetime, timezone
from decimal import Decimal

from .base import (
    EAT, Adapter, DeviceInfo, EtimsConfigError, EtimsError, Receipt,
    register,
)
from .kra_direct import receipt_qr_url

try:  # pragma: no cover - depends on the environment
    import etims as _probe  # noqa: F401
    ETIMS_IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover
    ETIMS_IMPORT_ERROR = str(exc)


def _etims():
    try:
        import etims
    except ImportError as exc:
        raise EtimsConfigError(
            "The etims Python package is not installed on this server, so the "
            "\"KRA OSCU via etims library\" backend cannot be used. Install it "
            "(pip install etims) or choose another backend.") from exc
    return etims


def _aware(value):
    """etims wants aware datetimes; the ORM's are naive UTC."""
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(EAT)


@register
class KraEtimsLibAdapter(Adapter):
    code = "kra_etims_lib"
    label = "KRA OSCU via etims library"
    experimental = True
    strict_sequence = True

    @classmethod
    def is_available(cls):
        return ETIMS_IMPORT_ERROR is None

    @classmethod
    def unavailable_reason(cls):
        if ETIMS_IMPORT_ERROR:
            return "The etims Python package is not installed."
        return None

    def secrets(self):
        return [self.device.sudo().cmc_key]

    # ------------------------------------------------------------- plumbing

    def _environment(self):
        return "production" if self.device.environment == "production" else "sandbox"

    def _client(self):
        etims = _etims()
        device = self.device.sudo()
        if not device.cmc_key:
            raise EtimsConfigError(
                "The device has no communication key yet. Initialise it first.")
        # The library does its arithmetic in Decimal; the code list stores floats.
        rates = {etims.TaxType(letter): Decimal(str(rate))
                 for letter, rate in self.device._ke_etims_tax_rates().items()
                 if letter in "ABCDE"}
        return etims.OscuClient(
            device.tin, device.bhf_id, device.cmc_key,
            environment=self._environment(),
            base_url=device.base_url or None,
            tax_rates=rates,
            timeout=self.timeout,
            retry=etims.RetryPolicy(attempts=1),
            session=self.session,
        )

    def _run(self, endpoint, request, call):
        """Run one library call, record it and translate its errors."""
        etims = _etims()
        try:
            result = call()
        except etims.KraError as exc:
            self.record(endpoint, request, exc.response or None, error=str(exc))
            raise EtimsError(
                exc.message, code=exc.code,
                duplicate=isinstance(exc, etims.DuplicateError),
                retryable=getattr(exc, "retryable", False),
            ) from exc
        except etims.TransportError as exc:
            self.record(endpoint, request, None, error=str(exc))
            raise EtimsError(
                str(exc), retryable=True,
                may_have_been_processed=exc.may_have_been_processed) from exc
        except etims.ProtocolError as exc:
            self.record(endpoint, request, exc.body, status=exc.status_code, error=str(exc))
            raise EtimsError(
                str(exc), retryable=True,
                may_have_been_processed=exc.may_have_been_processed) from exc
        except (etims.InvalidDataError, etims.ConfigurationError) as exc:
            self.record(endpoint, request, None, error=str(exc))
            raise EtimsConfigError(str(exc)) from exc
        except ValueError as exc:
            # A code KRA added after the library's enums were written.
            self.record(endpoint, request, None, error=str(exc))
            raise EtimsConfigError(str(exc)) from exc
        self.record(endpoint, request, _summary(result))
        return result

    def _operator(self, operator):
        return _etims().Operator(id=operator.id[:20], name=operator.name[:60])

    # ------------------------------------------------------- reference data

    def initialize(self):
        etims = _etims()
        device = self.device
        info = self._run("initialize_device", {"tin": device.tin, "bhfId": device.bhf_id},
                         lambda: etims.OscuClient.initialize_device(
                             device.tin, device.bhf_id, device.serial or "",
                             environment=self._environment(),
                             base_url=device.base_url or None,
                             timeout=self.timeout, session=self.session))
        return DeviceInfo(
            tin=info.tin, bhf_id=info.branch_id, taxpayer_name=info.taxpayer_name,
            branch_name=info.branch_name, scu_id=info.sdc_id, mrc_no=info.mrc_no,
            device_id=info.device_id, cmc_key=info.cmc_key)

    def sync_codes(self, since=None):
        client = self._client()
        result = self._run("get_codes", {}, lambda: client.get_codes(_aware(since)))
        return [
            {
                "class": cls.class_id, "name": cls.name,
                "codes": [
                    {"code": code.code, "name": code.name, "description": code.description,
                     "sort_order": code.sort_order, "active": code.active is not False,
                     "user_defined": code.user_defined}
                    for code in cls.codes
                ],
            }
            for cls in result
        ]

    def sync_item_classes(self, since=None):
        client = self._client()
        result = self._run("get_item_classes", {},
                           lambda: client.get_item_classes(_aware(since)))
        return [
            {"code": row.code, "name": row.name, "level": row.level,
             "tax_type": row.tax_type.value if row.tax_type else None,
             "major_target": bool(row.major_target), "active": row.active is not False}
            for row in result
        ]

    def fetch_branches(self):
        client = self._client()
        result = self._run("get_branches", {}, lambda: client.get_branches())
        return [
            {"bhf_id": row.branch_id, "name": row.name, "status": row.status_code,
             "is_head_office": bool(row.is_head_office), "raw": {}}
            for row in result
        ]

    def fetch_notices(self, since=None):
        client = self._client()
        result = self._run("get_notices", {}, lambda: client.get_notices(_aware(since)))
        return [
            {"number": row.number, "title": row.title, "contents": row.contents,
             "url": row.detail_url,
             "registered_at": row.registered_at.astimezone(timezone.utc).replace(tzinfo=None)
             if row.registered_at else None}
            for row in result
        ]

    def register_item(self, item):
        etims = _etims()
        client = self._client()
        lib_item = etims.Item(
            item_code=item.item_code,
            item_class_code=item.item_class_code,
            product_type=etims.ProductType(item.product_type),
            name=item.name[:200],
            packaging_unit=item.packaging_unit,
            quantity_unit=item.quantity_unit,
            tax_type=etims.TaxType(item.tax_type),
            default_price=item.default_price,
            origin_country=item.origin_country,
            barcode=(item.barcode or None) and item.barcode[:20],
            active=item.active,
        )
        self._run("save_item", {"itemCd": item.item_code},
                  lambda: client.save_item(lib_item, operator=self._operator(item.operator)))
        return None

    # ---------------------------------------------------------------- sales

    def sign_sale(self, sale):
        return self._sign(sale)

    def sign_credit_note(self, sale):
        return self._sign(sale)

    def _sign(self, sale):
        etims = _etims()
        client = self._client()
        lines = [
            etims.SaleLine(
                item_code=line.item_code,
                item_class_code=line.item_class_code,
                name=line.name[:200],
                quantity=line.quantity,
                unit_price=line.unit_price,
                tax_type=etims.TaxType(line.tax_type),
                packaging_unit=line.packaging_unit,
                quantity_unit=line.quantity_unit,
                package_quantity=line.package_quantity,
                discount_rate=line.discount_rate,
                barcode=(line.barcode or None) and line.barcode[:20],
                amounts=etims.LineAmounts(
                    supply_amount=line.supply_amount,
                    discount_amount=line.discount_amount,
                    taxable_amount=line.taxable_amount,
                    tax_amount=line.tax_amount,
                ),
            )
            for line in sale.lines
        ]
        credit = sale.is_credit_note
        lib_sale = etims.Sale(
            trader_invoice_no=sale.trader_invoice_no[:50],
            lines=lines,
            confirmed_at=_aware(sale.confirmed_at),
            invoice_no=sale.invoice_no,
            receipt_type=etims.ReceiptType.CREDIT_NOTE if credit else etims.ReceiptType.SALE,
            original_invoice_no=sale.original_invoice_no if credit else None,
            credit_note_reason=etims.CreditNoteReason(sale.reason_code) if credit else None,
            credit_note_at=_aware(sale.refunded_at) if credit else None,
            customer_pin=sale.customer_pin or None,
            customer_name=(sale.customer_name or None) and sale.customer_name[:60],
            customer_phone=(sale.customer_phone or None) and sale.customer_phone[:20],
            payment_type=etims.PaymentType(sale.payment_type) if sale.payment_type else None,
            sales_type=etims.SalesType(sale.sales_type),
            sale_date=sale.sale_date,
            remark=sale.remark,
        )
        result = self._run("save_sale", {"invcNo": sale.invoice_no, "rcptTyCd": sale.receipt_type},
                           lambda: client.save_sale(lib_sale, operator=self._operator(sale.operator)))
        device = self.device
        return Receipt(
            status="signed",
            kra_invoice_no=sale.invoice_no,
            receipt_no=result.receipt_no,
            total_receipt_no=result.total_receipt_no,
            internal_data=result.internal_data,
            signature=result.signature,
            scu_id=device.scu_id,
            mrc_no=device.mrc_no,
            signed_at=result.signed_at.astimezone(timezone.utc).replace(tzinfo=None)
            if result.signed_at else None,
            # Same format as the library's receipt.qr_url, built here so that a
            # base_url override (the fake KRA) still points at KRA's page.
            qr_url=receipt_qr_url(device.environment, device.tin, device.bhf_id,
                                  result.signature),
            raw=dict(result.raw),
        )

    # ------------------------------------------------------------ purchases

    def fetch_purchases(self, since=None):
        client = self._client()
        result = self._run("get_purchase_sales", {},
                           lambda: client.get_purchase_sales(_aware(since)))
        purchases = []
        for row in result:
            purchases.append({
                "supplier_pin": row.supplier_pin,
                "supplier_name": row.supplier_name,
                "supplier_bhf_id": row.supplier_branch_id,
                "supplier_invoice_no": row.supplier_invoice_no,
                "receipt_type": row.receipt_type,
                "payment_type": row.payment_type.value if row.payment_type else None,
                "confirmed_at": row.confirmed_at.astimezone(timezone.utc).replace(tzinfo=None)
                if row.confirmed_at else None,
                "sale_date": row.sale_date,
                "total_taxable": row.total_taxable,
                "total_tax": row.total_tax,
                "total_amount": row.total_amount,
                "remark": row.remark,
                "provider_ref": "%s/%s/%s" % (row.supplier_pin, row.supplier_branch_id,
                                              row.supplier_invoice_no),
                "lines": [
                    {
                        "seq": line.item_seq, "item_class_code": line.item_class_code,
                        "item_code": line.item_code, "name": line.name,
                        "barcode": line.barcode, "packaging_unit": line.packaging_unit,
                        "package_quantity": line.package_quantity,
                        "quantity_unit": line.quantity_unit, "quantity": line.quantity,
                        "unit_price": line.unit_price, "supply_amount": line.supply_amount,
                        "discount_rate": line.discount_rate,
                        "discount_amount": line.discount_amount,
                        "tax_type": line.tax_type.value if line.tax_type else None,
                        "taxable_amount": line.taxable_amount,
                        "tax_amount": line.tax_amount, "total_amount": line.total_amount,
                    }
                    for line in row.lines
                ],
                "raw": {},
            })
        return purchases

    def confirm_purchase(self, purchase):
        etims = _etims()
        client = self._client()
        lines = [
            etims.PurchaseLine(
                item_class_code=line.item_class_code,
                name=line.name[:200],
                quantity=line.quantity,
                unit_price=line.unit_price,
                tax_type=etims.TaxType(line.tax_type),
                quantity_unit=line.quantity_unit,
                packaging_unit=line.packaging_unit,
                package_quantity=line.package_quantity,
                discount_rate=line.discount_rate,
                item_code=line.item_code,
                supplier_item_class_code=line.supplier_item_class_code,
                supplier_item_code=line.supplier_item_code,
                supplier_item_name=line.supplier_item_name,
                expiry_date=line.expiry_date,
                amounts=etims.LineAmounts(
                    supply_amount=line.supply_amount,
                    discount_amount=line.discount_amount,
                    taxable_amount=line.taxable_amount,
                    tax_amount=line.tax_amount,
                ),
            )
            for line in purchase.lines
        ]
        lib_purchase = etims.Purchase(
            invoice_no=purchase.invoice_no,
            lines=lines,
            purchase_date=purchase.purchase_date,
            payment_type=etims.PaymentType(purchase.payment_type),
            supplier_pin=purchase.supplier_pin,
            supplier_branch_id=purchase.supplier_bhf_id,
            supplier_name=(purchase.supplier_name or None) and purchase.supplier_name[:60],
            supplier_invoice_no=purchase.supplier_invoice_no,
            registration_type=etims.RegistrationType(purchase.registration_type),
            receipt_type=etims.PurchaseReceiptType(purchase.receipt_type),
            status=etims.TransactionStatus(purchase.status),
            confirmed_at=_aware(purchase.confirmed_at),
            remark=purchase.remark,
        )
        self._run("save_purchase", {"invcNo": purchase.invoice_no},
                  lambda: client.save_purchase(lib_purchase,
                                               operator=self._operator(purchase.operator)))

    # ---------------------------------------------------------------- stock

    def report_stock_io(self, stock_io):
        etims = _etims()
        client = self._client()
        lines = [
            etims.StockIOLine(
                item_class_code=line.item_class_code,
                name=line.name[:200],
                quantity=line.quantity,
                unit_price=line.unit_price,
                tax_type=etims.TaxType(line.tax_type),
                packaging_unit=line.packaging_unit,
                quantity_unit=line.quantity_unit,
                package_quantity=line.package_quantity,
                discount_amount=line.discount_amount,
                item_code=line.item_code,
                expiry_date=line.expiry_date,
                amounts=etims.LineAmounts(
                    supply_amount=line.supply_amount,
                    discount_amount=line.discount_amount,
                    taxable_amount=line.taxable_amount,
                    tax_amount=line.tax_amount,
                ),
            )
            for line in stock_io.lines
        ]
        lib_io = etims.StockIO(
            sar_no=stock_io.sar_no,
            io_type=etims.StockIOType(stock_io.io_type),
            lines=lines,
            occurred_on=stock_io.occurred_on,
            original_sar_no=stock_io.original_sar_no or 0,
            registration_type=etims.RegistrationType(stock_io.registration_type),
            customer_pin=stock_io.customer_pin,
            customer_name=stock_io.customer_name,
            customer_branch_id=stock_io.customer_bhf_id,
            remark=stock_io.remark,
        )
        self._run("save_stock_io", {"sarNo": stock_io.sar_no, "sarTyCd": stock_io.io_type},
                  lambda: client.save_stock_io(lib_io,
                                               operator=self._operator(stock_io.operator)))

    def save_stock_master(self, stock_master):
        etims = _etims()
        client = self._client()
        lib_master = etims.StockMaster(item_code=stock_master.item_code,
                                       remaining_quantity=stock_master.remaining_quantity)
        self._run("save_stock_master", {"itemCd": stock_master.item_code},
                  lambda: client.save_stock_master(
                      lib_master, operator=self._operator(stock_master.operator)))

    # -------------------------------------------------------------- imports

    def fetch_imports(self, since=None):
        client = self._client()
        result = self._run("get_imported_items", {},
                           lambda: client.get_imported_items(_aware(since)))
        return [
            {
                "task_code": row.task_code, "declaration_date": row.declaration_date,
                "item_seq": row.item_seq, "declaration_no": row.declaration_no,
                "hs_code": row.hs_code, "name": row.name,
                "status": row.status.value if row.status else None,
                "origin_country": row.origin_country, "export_country": row.export_country,
                "package_quantity": row.package_quantity,
                "packaging_unit": row.packaging_unit, "quantity": row.quantity,
                "quantity_unit": row.quantity_unit, "gross_weight": row.gross_weight,
                "net_weight": row.net_weight, "supplier_name": row.supplier_name,
                "agent_name": row.agent_name, "invoice_amount": row.invoice_amount,
                "invoice_currency": row.invoice_currency,
                "exchange_rate": row.exchange_rate,
                "provider_ref": "%s/%s" % (row.task_code, row.item_seq),
                "raw": {},
            }
            for row in result
        ]

    def update_import(self, update):
        etims = _etims()
        client = self._client()
        lib_update = etims.ImportedItemUpdate(
            task_code=update.task_code,
            declaration_date=update.declaration_date,
            item_seq=update.item_seq,
            hs_code=update.hs_code,
            item_class_code=update.item_class_code,
            item_code=update.item_code,
            status=etims.ImportItemStatus(update.status),
            remark=update.remark,
        )
        self._run("update_imported_item", {"taskCd": update.task_code},
                  lambda: client.update_imported_item(
                      lib_update, operator=self._operator(update.operator)))


def _summary(result):
    """Something loggable for a library result, without its secrets."""
    if result is None:
        return {"resultCd": "000"}
    raw = getattr(result, "raw", None)
    if raw:
        return dict(raw)
    if isinstance(result, (list, tuple)) or hasattr(result, "items"):
        try:
            return {"count": len(result)}
        except TypeError:
            pass
    name = type(result).__name__
    return {"result": name, "at": datetime.now(timezone.utc).isoformat()}
