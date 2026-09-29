// Screenshot every page of the design lab (synthetic e2e data) for visual review.
// Usage: node scripts/gallery.mjs [out-dir] [only-substring]
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.LAB_URL ?? "http://localhost:3010";
const OUT = path.resolve(process.argv[2] ?? "../../.data/gallery");
const ONLY = process.argv[3] ?? "";
const EMAIL = process.env.E2E_EMAIL ?? "e2e@oneledger.local";
const PASSWORD = process.env.E2E_PASSWORD ?? "e2e-owner-password-123";

const PAGES = [
  ["home", "/"],
  ["transactions", "/transactions?start_date=2026-08-01&end_date_exclusive=2026-09-01"],
  ["calendar", "/transactions?view=calendar&start_date=2026-08-01&end_date_exclusive=2026-09-01"],
  ["stats", "/stats?kind=month&anchor=2026-08-01"],
  ["stats-flow", "/stats?kind=month&anchor=2026-08-01&tab=flow"],
  ["people", "/people"],
  ["accounts", "/accounts"],
  ["imports", "/imports"],
  ["review", "/review"],
  ["assistant", "/assistant"],
  ["net-worth", "/net-worth"],
  ["cards", "/cards"],
  ["loans", "/loans"],
  ["investments", "/investments"],
  ["recurring", "/recurring"],
  ["budgets", "/budgets"],
  ["categories", "/categories"],
  ["settings", "/settings"],
];

const VARIANTS = [
  { name: "desktop", viewport: { width: 1360, height: 900 }, theme: "light" },
  { name: "dark", viewport: { width: 1360, height: 900 }, theme: "dark" },
  { name: "mobile", viewport: { width: 390, height: 844 }, theme: "light", isMobile: true },
];

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
// Sign in once (the login endpoint is rate limited) and reuse the session cookie everywhere.
const auth = await browser.newContext();
{
  const page = await auth.newPage();
  await page.goto(`${BASE}/login`);
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 30_000 });
}
const storageState = await auth.storageState();
await auth.close();
for (const v of VARIANTS) {
  const ctx = await browser.newContext({ viewport: v.viewport, deviceScaleFactor: 1, isMobile: v.isMobile ?? false, hasTouch: v.isMobile ?? false });
  await ctx.addInitScript((t) => { try { localStorage.setItem("ol-theme", t); } catch {} }, v.theme);
  const page = await ctx.newPage();
  if (!ONLY || "login".includes(ONLY)) {
    await page.goto(`${BASE}/login`);
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/login-${v.name}.png`, fullPage: true });
  }
  await ctx.addCookies(storageState.cookies);
  for (const [name, url] of PAGES) {
    if (ONLY && !name.includes(ONLY)) continue;
    await page.goto(`${BASE}${url}`);
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${OUT}/${name}-${v.name}.png`, fullPage: true });
  }
  if (!ONLY || "detail".includes(ONLY)) {
    const visit = async (name, from, link) => {
      await page.goto(`${BASE}${from}`);
      await page.waitForLoadState("networkidle").catch(() => {});
      const el = page.locator(link).first();
      if (!(await el.count())) return;
      await el.click();
      await page.waitForLoadState("networkidle").catch(() => {});
      await page.waitForTimeout(900);
      await page.screenshot({ path: `${OUT}/detail-${name}-${v.name}.png`, fullPage: true });
    };
    await visit("account", "/accounts", 'main a[href^="/accounts/"]');
    await visit("loan", "/loans", 'main a[href^="/loans/"]');
    await visit("import", "/imports", 'main a[href^="/imports/"]');
    // The Add sheet and a transaction's detail sheet.
    await page.goto(`${BASE}/transactions?start_date=2026-08-01&end_date_exclusive=2026-09-01`);
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.getByRole("button", { name: /netflix/i }).first().click().catch(() => {});
    await page.waitForTimeout(900);
    await page.screenshot({ path: `${OUT}/sheet-transaction-${v.name}.png`, fullPage: false });
    await page.keyboard.press("Escape");
    await page.goto(`${BASE}/transactions?add=1`);
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${OUT}/sheet-add-${v.name}.png`, fullPage: false });
  }
  if (ONLY === "wizard") {
    await page.goto(`${BASE}/imports`);
    await page.getByLabel("Account").selectOption({ index: 1 });
    await page.getByLabel("Statement file").setInputFiles(path.resolve("../../tests/fixtures/synthetic_hdfc_jun_aug_2026.csv"));
    await page.getByRole("button", { name: "Upload" }).click();
    await page.getByRole("heading", { name: /Check before importing|Match the columns/ }).waitFor({ timeout: 30_000 });
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${OUT}/wizard-${v.name}.png` });
    const cancel = page.getByRole("button", { name: "Cancel import" });
    if (await cancel.count()) await cancel.click();
  }
  if (!ONLY || "sheet".includes(ONLY)) {
    await page.goto(`${BASE}${PAGES[1][1]}`);
    await page.waitForLoadState("networkidle").catch(() => {});
    const row = page.getByRole("button", { name: /netflix/i }).first();
    if (await row.count()) {
      await row.click();
      await page.waitForTimeout(500);
      await page.screenshot({ path: `${OUT}/sheet-transaction-${v.name}.png` });
      await page.keyboard.press("Escape");
    }
    await page.goto(`${BASE}/accounts?new=1`);
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${OUT}/sheet-add-account-${v.name}.png` });
  }
  await ctx.close();
}
await browser.close();
console.log(`Saved to ${OUT}`);
