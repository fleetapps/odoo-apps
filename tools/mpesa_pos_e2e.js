// Copyright 2026 Fleet Apps
// License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
//
// Drives a real browser through the three ways a cashier takes M-Pesa:
// prompt the phone, pick a payment made to the till, type a receipt.
// Needs Playwright and tools/mpesa_fake_daraja.py running; on a test
// database only. Settings come from the environment:
//
//   ODOO_URL=http://127.0.0.1:8069 ODOO_LOGIN=admin ODOO_PASSWORD=admin \
//   POS_CONFIG_ID=1 PRODUCT="Layers mash 50kg" PRICE=2500 \
//   METHOD="M-Pesa 600100" TILL=600100 FAKE_CONTROL=http://127.0.0.1:18081 \
//   SHOTS=/tmp/shots node tools/mpesa_pos_e2e.js

const { chromium } = require("playwright");

const env = process.env;
const ODOO = env.ODOO_URL || "http://127.0.0.1:8069";
const PRODUCT = env.PRODUCT || "Layers mash 50kg";
const PRICE = env.PRICE || "2500";
const METHOD = env.METHOD || "M-Pesa 600100";
const TILL = env.TILL || "600100";
const CONTROL = env.FAKE_CONTROL || "http://127.0.0.1:18081";
const SHOTS = env.SHOTS || ".";
const TYPED = "Q" + Math.random().toString(36).slice(2, 11).toUpperCase().padEnd(9, "X");

(async () => {
    const browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    const shot = (name) => page.screenshot({ path: `${SHOTS}/${name}.png` });
    const step = (text) => console.log("ok  ", text);
    const dialog = page.locator(".o_mpesa_dialog");
    let failed = false;

    async function openRegister() {
        await page.goto(`${ODOO}/web/login`);
        await page.fill('input[name="login"]', env.ODOO_LOGIN || "admin");
        await page.fill('input[name="password"]', env.ODOO_PASSWORD || "admin");
        await page.click('button[type="submit"]');
        await page.waitForURL(/odoo|web/, { timeout: 60000 });
        await page.goto(`${ODOO}/pos/ui?config_id=${env.POS_CONFIG_ID || 1}`, { timeout: 120000 });
        // The landing tile, then the opening cash dialog (both absent when
        // the session is already open).
        const tile = page.getByText("Open Register", { exact: true }).first();
        const product = page.getByText(PRODUCT).first();
        await Promise.race([tile.waitFor({ timeout: 120000 }), product.waitFor({ timeout: 120000 })]);
        if (await tile.isVisible()) {
            await tile.click();
        }
        await product.waitFor({ timeout: 120000 });
        const open = page.locator(".modal-dialog").getByRole("button", { name: /Open Register/i });
        if (await open.waitFor({ timeout: 10000 }).then(() => true, () => false)) {
            await page.waitForTimeout(1000);
            await open.click();
            await open.waitFor({ state: "hidden", timeout: 10000 });
        }
        step("register open");
    }

    async function sell() {
        await page.getByText(PRODUCT).first().click();
        await page.getByRole("button", { name: /^Payment$/ }).first().click();
        await page.getByText(METHOD).first().click();
        await page.locator(".send_payment_request").first().click();
        await dialog.waitFor({ timeout: 20000 });
    }

    // Odoo validates by itself once a terminal line pays the whole order.
    async function receiptScreen() {
        const newOrder = page.getByRole("button", { name: /New Order/ }).first();
        const validate = page
            .locator(".payment-screen button")
            .filter({ hasText: /^\s*Validate\s*$/ })
            .first();
        for (let i = 0; i < 120; i++) {
            if (await newOrder.isVisible()) {
                return;
            }
            if ((await validate.isVisible()) && (await validate.isEnabled())) {
                await validate.click({ timeout: 2000 }).catch(() => {});
            }
            await page.waitForTimeout(500);
        }
        throw new Error("the order never reached the receipt screen");
    }

    async function nextOrder() {
        await page.getByRole("button", { name: /New Order/ }).first().click();
    }

    try {
        await openRegister();

        await sell();
        await dialog.locator('input[type="tel"]').fill("0708374149");
        await dialog.getByRole("button", { name: /Send prompt/ }).click();
        await receiptScreen();
        await page.getByText(/M-PESA [A-Z0-9]{10}/).first().waitFor({ timeout: 30000 });
        await shot("1_stk_ticket");
        step("phone prompt paid; the ticket shows the M-PESA receipt");
        await nextOrder();

        const paid = await fetch(`${CONTROL}/pay?till=${TILL}&amount=${PRICE}&name=WANJIKU`, {
            method: "POST",
        }).then((r) => r.json());
        await page.waitForTimeout(6000);
        await sell();
        await dialog.getByRole("button", { name: "Paid to till" }).click();
        await dialog.getByText("WANJIKU").first().waitFor({ timeout: 20000 });
        await shot("2_paid_to_till");
        await dialog.getByText("WANJIKU").first().click();
        await receiptScreen();
        await page.getByText(`M-PESA ${paid.receipt}`).first().waitFor({ timeout: 30000 });
        step(`payment to the till (${paid.receipt}) picked and printed`);
        await nextOrder();

        await sell();
        await dialog.getByRole("button", { name: "Type receipt" }).click();
        await dialog.locator('input[type="text"]').fill(TYPED);
        await dialog.getByRole("button", { name: /Use this receipt/ }).click();
        await receiptScreen();
        await page.getByText(`M-PESA ${TYPED} *`).first().waitFor({ timeout: 30000 });
        await shot("3_typed_ticket");
        step(`typed receipt ${TYPED} printed with its flag`);
    } catch (e) {
        failed = true;
        console.log("FAIL", e.message.split("\n")[0]);
        await shot("failure");
    }
    const real = errors.filter((e) => !/favicon|ERR_ABORTED/.test(e));
    if (real.length) {
        failed = true;
        console.log("browser errors:", real);
    }
    await browser.close();
    process.exit(failed ? 1 : 0);
})();
