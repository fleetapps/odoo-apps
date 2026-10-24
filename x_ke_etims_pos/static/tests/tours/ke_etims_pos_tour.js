import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("KeEtimsPosReceiptTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            ProductScreen.addOrderline("Packaged maize flour", "1"),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Cash KES"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.receiptIsThere(),
            {
                content: "the receipt carries KRA's signature",
                trigger: ".pos-receipt .ke-etims-receipt:contains('SCU INFORMATION')",
            },
            {
                content: "with its QR code",
                trigger: ".pos-receipt .ke-etims-receipt img.ke-etims-qr",
            },
            {
                content: "and says the demo signer signed it",
                trigger: ".pos-receipt .ke-etims-demo:contains('DEMO - NOT A TAX INVOICE')",
            },
        ].flat(),
});
