import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { generateQRCodeDataUrl } from "@point_of_sale/utils";
import { patch } from "@web/core/utils/patch";

/** "ABCDEFGH" -> "ABCD-EFGH", the way the TIS specification prints them. */
function dashed(text) {
    const plain = (text || "").replaceAll("-", "");
    return (plain.match(/.{1,4}/g) || []).join("-");
}

patch(OrderReceipt.prototype, {
    get keEtims() {
        const order = this.order;
        if (!order.ke_etims_applicable) {
            return null;
        }
        const signed = order.ke_etims_state === "sent" && Boolean(order.ke_etims_signature);
        const [date, time] = (order.ke_etims_signed_at_display || " ").split(" ");
        return {
            signed,
            demo: Boolean(order.ke_etims_is_demo),
            // The TIS specification allows one original; every reprint says COPY.
            copy: (order.nb_print || 0) > 0,
            reference: order.pos_reference || order.name,
            label: order.ke_etims_receipt_label || "NS",
            date,
            time,
            cuId: order.ke_etims_scu_id,
            cuInvoiceNo: `${order.ke_etims_scu_id || ""}/${order.ke_etims_receipt_no || ""}`,
            counter: `${order.ke_etims_receipt_no || ""}/${order.ke_etims_total_receipt_no || ""}`,
            internalData: dashed(order.ke_etims_internal_data),
            signature: dashed(order.ke_etims_signature),
            kraInvoiceNo: order.ke_etims_kra_invoice_no,
            originalCuInvoice: order.ke_etims_original_cu_invoice,
            qr: signed && order.ke_etims_qr_url ? generateQRCodeDataUrl(order.ke_etims_qr_url) : null,
        };
    },
});
