# Business Documents

The company's own document formats, filled in by Odoo from the record each one
belongs to, on the company letterhead with that document's terms.

**Documents** (the app) shows one card per document, grouped Sales, Export,
Purchases, Stock and Internal. A card says what the document is for and where
it is printed from. Clicking it opens those records: open one, then **Print**.
Each Print menu offers only the documents that apply to that record, so a
receipt never offers a delivery note.

| Document | Printed from |
|---|---|
| Estimate | A quotation |
| Pro Forma Invoice | A quotation or sales order |
| Sales Invoice | A customer invoice (customer in Kenya) |
| Sales Receipt | A paid or part-paid invoice |
| Credit Note | A credit note |
| Delivery Note / Packing List | A delivery |
| Goods Replacement Note | A customer return |
| Commercial Invoice | A customer invoice (customer abroad) |
| Export Packing List | A delivery to a customer abroad |
| Request for Quotation | An RFQ (the Print RFQ button too) |
| Purchase Order | A confirmed purchase order |
| Goods Received Note | A receipt |
| Goods Return Note | A return to a supplier |
| Transfer Form | An internal transfer |
| Stock Count Sheet | Physical Inventory lines, one sheet for those selected |
| Requisition Form | A purchase request |
| Memo | Written in the app |

## Print buttons

Each record has a button for its documents, so no one has to find them in a
menu: **Print Delivery Note**, **Print Packing List** (export), **Print GRN**,
**Print Return Note**, **Print Replacement Note** and **Print Transfer Form**
on transfers; **Print Estimate** and **Print Pro Forma** on quotations;
**Print PO** on confirmed purchase orders (Print is the RFQ); **Print
Requisition** on purchase requests; **Print Receipt** on paid invoices (Print
is the invoice, commercial invoice or credit note); **Print Count Sheet** on
the lines selected in Physical Inventory. The Stock Book prints them too: a
posted line's panel, and the notice after Add sale or Add purchase.

## Packing

A delivery has a **Packing** tab, and its Operations lines have Cartons,
Pallet and weight columns. Cartons and weights work themselves out from each
product's units per carton, net weight and weight; type over them as packed
and weighed (0 goes back to the product's figure), and type the pallet each
line is on and the delivery's pallets and volume. The tab lists the products
still missing what the packing list needs, with a link to fill them in. The
pack size prints from the product's Pack size or, when empty, from its name
(500 Ml prints 0.5 L).

## What is filled in from where

- **Letterhead**: the company's name, address, KRA PIN (Tax ID), phone, email,
  website and logo (Settings > Companies).
- **Payment details**: the M-Pesa paybill and account (Documents >
  Configuration > Letterhead & payment details) and the company's bank
  accounts in the document's currency, with their SWIFT code.
- **Terms**: per document (Documents > Configuration > Documents & terms).
  `{company}` prints as the company's name.
- **Export**: HS code and country of origin (product, Inventory tab, Odoo's
  own fields), KEBS permit, brand, pack size, net weight and units per carton
  (same place); gross weight is the product's Weight. Pallets and volume are
  typed on the delivery's Packing tab.

## Export invoices

An invoice for a customer in another country is an export invoice: it prints
as the commercial invoice, in the company's currency and the export currency
(USD), with the rate between them. The rate starts at Odoo's rate for the
invoice date (the CBK monthly average once the feed is on) and can be typed
over on the invoice, under the currency, while it is a draft. On an invoice
raised in USD the rate is Odoo's own invoice rate, so the accounts use the
rate the document prints.

## Replacing Odoo's documents

With *These documents replace Odoo's* on (the default), Print menus offer these
documents instead of Odoo's invoice, quotation, pro forma, purchase order,
RFQ, delivery slip, return slip and count sheet; Send & Print and the portal
use the company's invoice, commercial invoice and credit note; and the
quotation, pro forma, RFQ, purchase order and delivery emails attach them.
A journal or a customer can still name another invoice report.

## Issued documents

Every document printed or sent is listed under **Issued**: which document,
for which record and customer or supplier, by whom and when. An issued memo
keeps its PDF: reprints are that same copy.

## Memos

Anyone writes a memo. A Documents manager (Settings administrators are) issues
it: it is numbered `RINZ/MEMO/HR/2026/001` (the company's short name, the
department's code, the year) and locked. Where it asks, staff click *I have
read it*; the printed memo has a receipt table to sign.
