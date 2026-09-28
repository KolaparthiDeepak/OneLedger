// Drive the main flows in the design lab (synthetic data) and screenshot each step.
// Usage: node scripts/walkthrough.mjs [out-dir]
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.LAB_URL ?? "http://localhost:3010";
const OUT = path.resolve(process.argv[2] ?? "../../.data/walkthrough");
fs.mkdirSync(OUT, { recursive: true });
const shot = (page, name, full = false) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: full });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
await page.goto(`${BASE}/login?signed_out=1`);
await shot(page, "01-login-signed-out");
await page.getByLabel("Email").fill(process.env.E2E_EMAIL ?? "e2e@oneledger.local");
await page.getByLabel("Password").fill(process.env.E2E_PASSWORD ?? "e2e-owner-password-123");
await page.getByRole("button", { name: "Show" }).click();
await shot(page, "02-login-password-shown");
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL((u) => !u.pathname.startsWith("/login"));

// Transaction: reason, statement row, inline tag.
await page.goto(`${BASE}/transactions?start_date=2026-06-01&end_date_exclusive=2026-09-01`);
await page.getByRole("button", { name: /netflix/i }).first().click();
const dialog = page.locator("dialog[open]");
await dialog.getByText("From your statement", { exact: true }).click();
await page.waitForTimeout(300);
await shot(page, "03-sheet-statement-row");
await dialog.getByText("Notes and tags", { exact: true }).click();
await dialog.getByLabel("New tag").fill("Subscriptions review");
await dialog.getByRole("button", { name: "Add", exact: true }).click();
await page.waitForTimeout(500);
await dialog.getByRole("button", { name: "Save notes and tags" }).click();
await page.waitForTimeout(800);
await shot(page, "04-sheet-tag-added");
await page.keyboard.press("Escape");

// Quick filters and bulk select.
await page.getByRole("button", { name: "Money in", exact: true }).click();
await page.waitForTimeout(800);
await shot(page, "05-quick-filter-money-in");
await page.getByRole("button", { name: "Everything" }).click();
await page.getByRole("button", { name: "Select", exact: true }).click();
const boxes = page.getByRole("checkbox");
await boxes.nth(0).click();
await boxes.nth(1).click();
await page.waitForTimeout(300);
await shot(page, "06-bulk-select");
await page.getByRole("button", { name: "Done selecting" }).click();

for (const [name, url] of [["07-settings-ai", "/settings"], ["08-categories", "/categories"], ["09-review", "/review"]]) {
  await page.goto(`${BASE}${url}`);
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(600);
  await shot(page, name, true);
}
await browser.close();
console.log(`Saved to ${OUT}`);
