import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

// Runs after first-run.spec.ts (workers: 1, files in alphabetical order) on the same database,
// so HDFC Savings with Jun–Aug statements already exists.
const EMAIL = process.env.E2E_EMAIL ?? "e2e@oneledger.local";
const PASSWORD = process.env.E2E_PASSWORD ?? "e2e-owner-password-123";
const FIXTURES = path.resolve(__dirname, "../../../tests/fixtures");
const SHOTS = path.resolve(__dirname, "../../../.data/screenshots");

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("link", { name: "Transactions" }).first()).toBeVisible();
}

test.describe.configure({ mode: "serial" });

test("credit card: purchases count once, bill payments match the bank", async ({ page }) => {
  await signIn(page);
  await page.goto("/cards");
  await page.getByRole("button", { name: "Add card" }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Name").fill("HDFC Regalia");
  await dialog.getByLabel("Issuer").fill("HDFC");
  await dialog.getByLabel("Card number").fill("4111111111114321");
  await dialog.getByLabel("Credit limit").fill("300000");
  await dialog.getByLabel("Currently owed").fill("0");
  await dialog.getByLabel("As of").fill("2026-05-31");
  await dialog.getByRole("button", { name: "Add card" }).click();
  await expect(page.getByText("HDFC Regalia")).toBeVisible();

  await page.goto("/imports");
  await page.getByLabel("Account").selectOption({ label: "HDFC Regalia" });
  await page.getByLabel("Statement file").setInputFiles(path.join(FIXTURES, "synthetic_card_jun_aug_2026.csv"));
  await page.getByRole("button", { name: "Upload" }).click();
  // Card statement: one amount column plus a Dr/Cr column.
  if (await page.getByRole("heading", { name: "Match the columns" }).isVisible()) {
    await page.getByLabel("How are amounts shown?").selectOption("drcr_column");
    await page.getByLabel("Amount column").selectOption("Amount");
    await page.getByLabel("Dr/Cr column").selectOption("Dr/Cr");
    await page.getByRole("button", { name: "Preview import" }).click();
  }
  await expect(page.getByRole("heading", { name: "Check before importing" })).toBeVisible();
  await page.getByRole("button", { name: /^Import \d+ transactions$/ }).click();
  await expect(page.getByRole("heading", { name: "Imported" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/transfers between your accounts matched automatically/)).toBeVisible();

  // The three bank-side card payments are no longer "unmatched" review items.
  await page.goto("/review");
  await expect(page.getByText("Transfers with no matching account")).toHaveCount(0);
  await page.goto("/cards");
  await page.screenshot({ path: `${SHOTS}/cards.png`, fullPage: true });
});

test("loan: EMIs already on the statement are matched and split; lender figures replace the estimate", async ({ page }) => {
  await signIn(page);
  await page.goto("/loans");
  await page.getByRole("button", { name: "Add loan" }).first().click();
  const d = page.getByRole("dialog");
  await d.getByLabel("Lender").fill("SBI");
  await d.getByLabel("Original loan amount").fill("3000000");
  await d.getByLabel("Interest rate (% per year)").fill("8.5");
  await d.getByLabel("Outstanding principal now").fill("2400000");
  await d.getByLabel("Outstanding as of").fill("2026-05-31");
  await d.getByLabel("Loan start date").fill("2022-06-01");
  await d.getByLabel("First EMI date").fill("2022-07-05");
  await d.getByLabel("Tenure (months)").fill("240");
  await d.getByLabel("EMI", { exact: true }).fill("21500");
  await d.getByRole("button", { name: "Add loan" }).click();
  await page.getByRole("link", { name: "Details" }).first().click();
  await expect(page.getByRole("heading", { name: /SBI/ })).toBeVisible();

  // The Jun, Jul and Aug EMIs from the bank statement were matched when the loan was added.
  const payments = page.locator("section", { has: page.getByRole("heading", { name: "Payments recorded" }) });
  await expect(payments.getByText("Matched automatically, estimated")).toHaveCount(3);
  const june = payments.getByRole("listitem").filter({ hasText: "5 Jun 2026" });
  await june.getByRole("button", { name: "Enter lender figures" }).click();
  await june.getByRole("textbox", { name: "Principal" }).fill("4500");
  await june.getByRole("textbox", { name: "Interest" }).fill("17000");
  await june.getByRole("button", { name: "Save" }).click();
  await expect(june.getByText("Lender figures")).toBeVisible();
  await expect(june).toContainText("₹4,500.00");

  // Only interest counts as spending: August's "Loans" spending is the interest part, not the EMI.
  const aug = await page.evaluate(async () => {
    const r = await (await fetch("/api/bff/analytics/categories?start_date=2026-08-01&end_date_exclusive=2026-09-01")).json();
    return (r.data.categories as { code: string; amount: string }[]).find((c) => c.code === "LOANS")?.amount;
  });
  expect(Number(aug)).toBeLessThan(21500);
  expect(Number(aug)).toBeGreaterThan(15000);

  await page.getByRole("textbox", { name: "Amount", exact: true }).fill("200000");
  await page.getByLabel("On", { exact: true }).fill("2026-10-05");
  await page.getByRole("button", { name: "Simulate" }).click();
  await expect(page.getByText(/in interest/)).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/loan.png`, fullPage: true });
});

test("rules: dry-run shows matches before anything changes", async ({ page }) => {
  await signIn(page);
  await page.goto("/categories");
  await page.getByRole("button", { name: "New rule" }).click();
  await page.getByLabel("Description contains").fill("XYZ SERVICES");
  await page.getByLabel("Put in category").selectOption({ label: "Other" });
  await page.getByRole("button", { name: "Preview matches" }).click();
  await expect(page.getByText(/Nothing has been changed yet/)).toBeVisible();
  await page.getByRole("button", { name: "Save rule and apply to these" }).click();
  await expect(page.getByText("XYZ SERVICES → Other")).toBeVisible();
  await page.goto("/transactions?uncategorized=1");
  await expect(page.getByRole("button", { name: /XYZ SERVICES/ })).toHaveCount(0);
});

test("review, budgets, net worth and settings pages render real data", async ({ page }) => {
  await signIn(page);
  await page.goto("/budgets");
  await page.getByRole("button", { name: "Add budget" }).first().click();
  await page.getByRole("dialog").getByLabel("Category").selectOption({ label: "Food" });
  await page.getByRole("dialog").getByLabel("Monthly amount").fill("8000");
  await page.getByRole("dialog").getByLabel("Starting from").fill("2026-06");
  await page.getByRole("dialog").getByRole("button", { name: "Add budget" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  await page.getByLabel("Month", { exact: true }).fill("2026-08");
  await expect(page.getByText("Food").first()).toBeVisible();
  await expect(page.getByText(/transactions/).first()).toBeVisible();

  await page.goto("/net-worth");
  await expect(page.getByRole("heading", { name: "What you owe" })).toBeVisible();
  await expect(page.getByText("SBI Home Loan")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/net-worth.png`, fullPage: true });

  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Signed-in sessions" })).toBeVisible();
  await expect(page.getByText("Create token")).toHaveCount(0);

  // Theme lives in the header; CSV download lives on the overview.
  const before = await page.evaluate(() => document.documentElement.dataset.theme ?? "");
  await page.getByRole("button", { name: /Switch to (dark|light) theme/ }).click();
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).not.toBe(before);
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Download all transactions as CSV" })).toBeVisible();
});

test("deleting an imported statement removes its transactions", async ({ page }) => {
  await signIn(page);
  await page.goto("/imports");
  await page.evaluate(async () => {
    const r = await fetch("/api/bff/accounts", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: "Scratch Bank", kind: "BANK_SAVINGS" }),
    });
    if (!r.ok) throw new Error(`account create failed: ${r.status}`);
  });
  await page.reload();
  await page.getByLabel("Account").selectOption({ label: "Scratch Bank" });
  await page.getByLabel("Statement file").setInputFiles(path.join(FIXTURES, "synthetic_hdfc_jun_aug_2026.csv"));
  await page.getByRole("button", { name: "Upload" }).click();
  await page.getByRole("button", { name: /^Import \d+ transactions$/ }).click();
  await expect(page.getByRole("heading", { name: "Imported" })).toBeVisible({ timeout: 30_000 });

  await page.getByRole("button", { name: "Delete import" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("transactions it added");
  await page.getByRole("button", { name: /^Delete import and \d+ transactions$/ }).click();
  await expect(page.getByText("This import was deleted.")).toBeVisible();

  const left = await page.evaluate(async () => {
    const accounts = (await (await fetch("/api/bff/accounts")).json()) as { id: string; name: string }[];
    const id = accounts.find((a) => a.name === "Scratch Bank")!.id;
    const t = await (await fetch(`/api/bff/transactions?account_id=${id}&limit=10`)).json();
    return t.items.length as number;
  });
  expect(left).toBe(0);
});

test("tag a transaction inline, then categorise several at once", async ({ page }) => {
  await signIn(page);
  await page.goto("/transactions?start_date=2026-06-01&end_date_exclusive=2026-09-01");
  await page.getByRole("button", { name: /netflix/i }).first().click();
  const sheet = page.locator("dialog[open]");
  await sheet.getByText("Notes and tags", { exact: true }).click();
  await sheet.getByLabel("New tag").fill("Streaming");
  await sheet.getByRole("button", { name: "Add", exact: true }).click();
  await sheet.getByRole("button", { name: "Save notes and tags" }).click();
  await expect(sheet.getByText("Notes and tags saved.")).toBeVisible();
  await sheet.getByText("From your statement", { exact: true }).click();
  await expect(sheet.getByText("NETFLIX.COM SUBSCRIPTION").last()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByText("#Streaming").first()).toBeVisible();

  await page.getByRole("button", { name: "Select", exact: true }).click();
  await page.getByRole("checkbox", { name: /Select Uber/ }).first().click();
  await page.getByRole("checkbox", { name: /Select Uber/ }).nth(1).click();
  await expect(page.getByText("2 selected")).toBeVisible();
  await page.getByLabel("Category for the selected transactions").selectOption({ label: "Travel" });
  await page.getByRole("button", { name: "Set category" }).click();
  await expect(page.getByText("2 selected")).toBeHidden();
  await expect(page.getByRole("button", { name: /^Uber/ }).first()).toContainText("Travel");
});

test("invite someone: they get their own empty ledger, and the link works once", async ({ page, browser }) => {
  await signIn(page);
  await page.goto("/settings");
  await page.getByLabel("Note for you (optional)").fill("Priya");
  await page.getByRole("button", { name: "Create invite link" }).click();
  const link = await page.getByLabel("Invite link").inputValue();
  const path = new URL(link).pathname;
  expect(path).toMatch(/^\/invite\//);

  const guest = await browser.newContext();
  const g = await guest.newPage();
  await g.goto(path);
  await expect(g.getByRole("heading", { name: /invited you/ })).toBeVisible();
  await g.getByLabel("Your name").fill("Priya");
  await g.getByLabel("Email").fill("priya@oneledger.local");
  await g.getByLabel("Password", { exact: true }).fill("priya-password-123");
  await g.getByLabel("Repeat password").fill("priya-password-123");
  await g.getByRole("button", { name: "Create my ledger" }).click();
  await expect(g.getByRole("heading", { name: "Set up OneLedger" })).toBeVisible({ timeout: 20_000 });
  // Her ledger is empty: none of the owner's accounts are visible.
  const accounts = await g.evaluate(async () => (await (await fetch("/api/bff/accounts")).json()) as unknown[]);
  expect(accounts).toHaveLength(0);
  await guest.close();

  const again = await browser.newContext();
  const a = await again.newPage();
  await a.goto(path);
  await expect(a.getByRole("heading", { name: "This link can't be used" })).toBeVisible();
  await again.close();

  await page.reload();
  await expect(page.getByText("priya@oneledger.local")).toBeVisible();
  await expect(page.getByText("Joined")).toBeVisible();
});
