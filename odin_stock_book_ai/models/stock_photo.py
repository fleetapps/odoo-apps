"""The photos of one invoice, as the window uploaded them, and what was read
from them. Kept with the sale once it is saved (and posted on its invoice),
for anyone checking later what the paper said.

The files are kept as taken; what is sent to the model is a copy made for it:
turned upright (phones store the turn in EXIF), at most 2576 pixels on the
long edge (the most the current Claude models read; more is only cost) and
JPEG. A PDF goes as it is.
"""

import base64
import binascii
import hashlib
import io
import json
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.mimetypes import guess_mimetype

PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp", "application/pdf")
PHOTO_MAX_MB = 10
PHOTOS_MAX = 6
LONG_EDGE = 2576
PURGE_DAYS = 30


class StockPhoto(models.Model):
    _name = "odin.stock.photo"
    _description = "Stock Book invoice photo"
    _order = "id desc"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, readonly=True, default=lambda self: self.env.company)
    user_id = fields.Many2one("res.users", string="Uploaded by", readonly=True, default=lambda self: self.env.user)
    sale_id = fields.Many2one("odin.stock.sale", string="Sale", readonly=True, index=True, ondelete="set null",
                              check_company=True)
    invoice_id = fields.Many2one("account.move", string="On invoice", readonly=True, copy=False,
                                 help="The invoice whose chatter has a copy of the photos.")
    attachment_ids = fields.One2many("ir.attachment", "res_id", string="Pages", readonly=True,
                                     domain=[("res_model", "=", "odin.stock.photo")])
    reading = fields.Text(readonly=True, help="What was read from the photos, as the model gave it.")
    result = fields.Text(readonly=True, help="What the window was filled in with, flags included.")
    read_count = fields.Integer(readonly=True)

    def unlink(self):
        self.env["ir.attachment"].sudo().search(
            [("res_model", "=", self._name), ("res_id", "in", self.ids)]).unlink()
        return super().unlink()

    @api.model
    def _cron_purge(self):
        """Photos read in a window closed without saving."""
        cutoff = fields.Datetime.now() - timedelta(days=PURGE_DAYS)
        self.sudo().search([("sale_id", "=", False), ("create_date", "<", cutoff)]).unlink()

    # ------------------------------------------------------------ the files
    @api.model
    def _read_files(self, files):
        """The files sent by the window, read as what they are, not as what
        the browser called them."""
        result = []
        for file in files or []:
            name = " ".join((file.get("name") or "").split())[:200] or _("Invoice photo")
            try:
                raw = base64.b64decode(file.get("data") or "", validate=True)
            except (binascii.Error, ValueError):
                raw = b""
            if not raw:
                raise UserError(_("%s could not be read. Add it again.", name))
            if len(raw) > PHOTO_MAX_MB * 1024 * 1024:
                raise UserError(_("%(name)s is larger than %(mb)s MB. Take the photo again at a lower resolution.",
                                  name=name, mb=PHOTO_MAX_MB))
            mimetype = guess_mimetype(raw)
            if mimetype not in PHOTO_TYPES:
                raise UserError(_("%s is not a photo (JPG, PNG, WebP) or a PDF.", name))
            result.append({"name": name, "raw": raw, "mimetype": mimetype})
        return result

    def _add_files(self, files):
        self.ensure_one()
        if len(self.attachment_ids) + len(files) > PHOTOS_MAX:
            raise UserError(_("At most %s photos for one invoice.", PHOTOS_MAX))
        self.env["ir.attachment"].sudo().create([
            {**file, "res_model": self._name, "res_id": self.id} for file in files])

    def _pages(self):
        return self.attachment_ids.sorted("id")

    def _files_info(self):
        """The pages for the window: shown beside the lines, opened full size."""
        return [{"id": a.id, "name": a.name, "mimetype": a.mimetype, "size": a.file_size,
                 "url": f"/web/content/{a.id}"} for a in self.sudo()._pages()]

    def _fingerprint(self, attempt=0):
        """The same pages read again give the same key, so a request Odoo
        retries reuses the answer instead of paying for it twice; Read again
        (a new attempt) reads them afresh."""
        digest = hashlib.sha256()
        for attachment in self.sudo()._pages():
            digest.update((attachment.checksum or "").encode())
        digest.update(str(attempt or 0).encode())
        return digest.hexdigest()[:32]

    def _content_blocks(self):
        """The pages as the model reads them, each named, in order."""
        pages = self.sudo()._pages()
        blocks = []
        for number, attachment in enumerate(pages, 1):
            # Plain text for the model, in English whatever the user's language.
            blocks.append({"type": "text", "text": f"Photo {number} of {len(pages)}:"})
            raw = attachment.raw
            if attachment.mimetype == "application/pdf":
                blocks.append({"type": "document", "source": {
                    "type": "base64", "media_type": "application/pdf", "data": base64.b64encode(raw).decode()}})
            else:
                blocks.append({"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(_for_model(raw)).decode()}})
        return blocks

    # ------------------------------------------------------------ the sale
    def _link(self, sale):
        """The photos belong to this sale now; on its invoice once it has one."""
        for photo in self.sudo():
            if photo.sale_id != sale:
                photo.write({"sale_id": sale.id, "invoice_id": False})
        sale._photos_to_invoice()

    def _store(self, reading=None, result=None):
        values = {}
        if reading is not None:
            values.update(reading=json.dumps(reading), read_count=self.read_count + 1)
        if result is not None:
            values["result"] = json.dumps(result)
        self.sudo().write(values)


def _for_model(raw):
    """A copy of a photo for the model: upright, JPEG, at most LONG_EDGE."""
    from PIL import Image, ImageOps  # noqa: PLC0415 (Pillow ships with Odoo)

    try:
        image = Image.open(io.BytesIO(raw))
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.thumbnail((LONG_EDGE, LONG_EDGE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=85, optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise UserError(_("A photo could not be opened: %s. Take it again.", error)) from error
    return out.getvalue()
