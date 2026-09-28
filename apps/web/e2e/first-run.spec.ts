import { expect, test } from "@playwright/test";
import path from "node:path";

const EMAIL = process.env.E2E_EMAIL ?? "e2e@oneledger.local";
const PASSWORD = process.env.E2E_PASSWORD ?? "e2e-owner-password-123";
const FIXTURES = path.resolve(__dirname, "../../../tests/fixtures");
const SHOTS = path.resolve(__dirname, "../../../.data/screenshots");

test("first run: sign in, add account, import, review, dashboard", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill("wrong-password-xyz");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Invalid email or password.")).toBeVisible();
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByRole("heading", { name: "Set up OneLedger" })).toBeVisible();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Name", { exact: true }).fill("HDFC Savings");
  await page.getByLabel("Bank or institution").fill("HDFC Bank");
  await page.getByLabel("Account or card number").fill("50100012341234");
  await page.getByLabel("Balance", { exact: true }).fill("45000");
  await page.getByLabel("Balance at the end of").fill("2026-05-31");
  await page.getByRole("button", { name: "Add and import a statement" }).click();

  await expect(page.getByRole("heading", { name: "Import statements" })).toBeVisible();
  await page.getByLabel("Statement file").setInputFiles(path.join(FIXTURES, "synthetic_hdfc_jun_aug_2026.csv"));
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByRole("heading", { name: "Check before importing" })).toBeVisible();
  await expect(page.getByText("Will be added")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/import-preview.png`, fullPage: true });
  await page.getByRole("button", { name: /^Import \d+ transactions$/ }).click();
  await expect(page.getByRole("heading", { name: "Imported" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/new transactions added/)).toBeVisible();

  // Importing the same file again must add nothing.
  await page.goto("/imports");
  await page.getByLabel("Account").selectOption({ label: "HDFC Savings" });
  await page.getByLabel("Statement file").setInputFiles(path.join(FIXTURES, "synthetic_hdfc_jun_aug_2026.csv"));
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByText("You imported this exact file before", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Import 0 transactions" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel import" }).click();

  await page.goto("/transactions?start_date=2026-06-01&end_date_exclusive=2026-09-01");
  await expect(page.getByRole("heading", { name: "Transactions" })).toBeVisible();
  await page.getByRole("button", { name: /netflix/i }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/transaction-sheet.png` });
  await page.keyboard.press("Escape");

  await page.goto("/");
  await expect(page.getByText("Money in").first()).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "What is Spent?" }).click();
  await expect(page.getByRole("tooltip")).toContainText("credit-card bill payments");
  await expect(page.getByText("Last six months")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/dashboard-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.getByRole("navigation", { name: "Quick" })).toBeVisible();
  await expect(page.getByText("Money in").first()).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `${SHOTS}/dashboard-mobile.png`, fullPage: true });
  await page.goto("/transactions?start_date=2026-06-01&end_date_exclusive=2026-09-01");
  await expect(page.getByRole("button", { name: /netflix/i }).first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/transactions-mobile.png`, fullPage: true });
  await page.setViewportSize({ width: 1360, height: 900 });

  await page.goto("/review");
  await expect(page.getByRole("heading", { name: "Review" })).toBeVisible();
  await expect(page.getByText(/Nothing to review|no category yet|Possible|Transfers|Balances/).first()).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/review.png`, fullPage: true });
});

test("BFF rejects cross-site mutations and unknown routes", async ({ request }) => {
  const r = await request.post("/api/bff/accounts", { headers: { origin: "https://evil.example" }, data: { name: "x" } });
  expect(r.status()).toBe(403);
  const r2 = await request.get("/api/bff/internal/runner");
  expect(r2.status()).toBe(404);
  const r3 = await request.get("/api/bff/accounts");
  expect(r3.status()).toBe(401);
});
