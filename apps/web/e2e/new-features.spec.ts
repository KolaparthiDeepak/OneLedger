import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Runs after money-flows.spec.ts on the same database (HDFC Savings with Jun–Aug data, a card, a loan,
// a Food budget and a Cash wallet created during onboarding).
const EMAIL = process.env.E2E_EMAIL ?? "e2e@oneledger.local";
const PASSWORD = process.env.E2E_PASSWORD ?? "e2e-owner-password-123";
const PNG = Buffer.from("89504e470d0a1a0a0000000d4948445200000001000000010806000000", "hex");

type Cookies = Awaited<ReturnType<import("@playwright/test").BrowserContext["cookies"]>>;
let session: Cookies = [];

// Sign in once (sign-in is rate limited) and reuse the session cookie in every test.
test.beforeAll(async ({ browser }) => {
  const ctx = await browser.newContext();
  const p = await ctx.newPage();
  await p.goto("/login");
  await p.getByLabel("Email").fill(EMAIL);
  await p.getByLabel("Password").fill(PASSWORD);
  await p.getByRole("button", { name: "Sign in" }).click();
  await expect(p.getByRole("link", { name: "Transactions" }).first()).toBeVisible();
  session = await ctx.cookies();
  await ctx.close();
});

async function signIn(page: Page) {
  await page.context().addCookies(session);
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Transactions" }).first()).toBeVisible();
}

test.describe.configure({ mode: "serial" });

test("quick add: a cash expense typed as a sum, then a saved template", async ({ page }) => {
  await signIn(page);
  await page.getByRole("button", { name: "Add", exact: true }).first().click();
  const sheet = page.getByRole("dialog");
  await sheet.getByLabel("Amount").fill("120+45.5");
  await expect(sheet.getByText("₹165.50")).toBeVisible();
  await sheet.getByLabel("Paid from").selectOption({ label: "Cash" });
  await sheet.getByLabel("What was it?").fill("Vegetables market");
  await sheet.getByLabel("Save for next time").check();
  await sheet.getByRole("button", { name: "Save", exact: true }).click();
  await expect(sheet).toBeHidden();

  await page.goto("/transactions");
  await expect(page.getByRole("button", { name: /Vegetables market/ }).first()).toContainText("−₹165.50");

  await page.getByRole("button", { name: "Add", exact: true }).first().click();
  await page.getByRole("dialog").getByRole("button", { name: /Vegetables market/ }).click();
  await expect(page.getByRole("dialog").getByLabel("Amount")).toHaveValue("165.50");
  await page.keyboard.press("Escape");
});

test("quick add: moving money into the cash wallet is not spending", async ({ page }) => {
  await signIn(page);
  const spentBefore = await page.evaluate(async () => (await (await fetch("/api/bff/analytics/summary?period=this_month")).json()).data.net_expenses as string);
  await page.getByRole("button", { name: "Add", exact: true }).first().click();
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("tab", { name: "Transfer" }).click();
  await sheet.getByLabel("Amount").fill("2000");
  await sheet.getByLabel("From").selectOption({ label: "HDFC Savings" });
  await sheet.getByLabel("To", { exact: true }).selectOption({ label: "Cash" });
  await sheet.getByRole("button", { name: "Save", exact: true }).click();
  await expect(sheet).toBeHidden();
  const spentAfter = await page.evaluate(async () => (await (await fetch("/api/bff/analytics/summary?period=this_month")).json()).data.net_expenses as string);
  expect(spentAfter).toBe(spentBefore);
  await page.goto("/transactions");
  await expect(page.getByRole("button", { name: /HDFC Savings to Cash/ }).first()).toContainText("Transfer");
});

test("calendar shows each day's totals and that day's transactions", async ({ page }) => {
  await signIn(page);
  await page.goto("/transactions?view=calendar&start_date=2026-08-01&end_date_exclusive=2026-09-01");
  await expect(page.getByText("August 2026").first()).toBeVisible();
  await page.getByRole("button", { name: /^5 Aug 2026: in/ }).click();
  await expect(page.getByRole("button", { name: /Nobroker Rent Payment/ })).toBeVisible();
});

test("stats: spending donut, other periods and the cash-flow diagram", async ({ page }) => {
  await signIn(page);
  await page.goto("/stats?kind=month&anchor=2026-08-01");
  await expect(page.getByRole("heading", { name: "Stats" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Housing/ })).toBeVisible();
  await page.getByRole("button", { name: /Food/ }).first().click();
  await expect(page.getByRole("link", { name: /Groceries/ })).toBeVisible();
  await page.getByRole("tab", { name: "Year" }).click();
  await expect(page.getByRole("heading", { name: "2026" })).toBeVisible();
  await page.getByRole("tab", { name: "Cash flow" }).click();
  await expect(page.getByText("Where the money went")).toBeVisible();
});

test("share a dinner with someone: only your part is spending, they owe you the rest", async ({ page }) => {
  await signIn(page);
  await page.goto("/transactions?start_date=2026-08-01&end_date_exclusive=2026-09-01&q=starbucks");
  await page.getByRole("button", { name: /STARBUCKS/ }).first().click();
  const sheet = page.locator("dialog[open]");
  await sheet.getByText("Split with people", { exact: true }).click();
  await sheet.getByLabel("Add a person").fill("Priya");
  await sheet.getByRole("button", { name: "Add", exact: true }).click();
  await expect(sheet.getByRole("button", { name: "Priya" })).toHaveAttribute("aria-pressed", "true");
  await sheet.getByRole("button", { name: "Save split" }).click();
  await expect(sheet.getByText(/Shared\. What they owe you/)).toBeVisible();
  await page.keyboard.press("Escape");
  await page.goto("/people");
  await expect(page.getByRole("button", { name: /Priya/ })).toContainText("owes you");
});

test("receipts can be attached to a transaction", async ({ page }) => {
  await signIn(page);
  await page.goto("/transactions?start_date=2026-08-01&end_date_exclusive=2026-09-01&q=decathlon");
  await page.getByRole("button", { name: /DECATHLON/ }).first().click();
  const sheet = page.locator("dialog[open]");
  await sheet.getByText("Receipts", { exact: true }).click();
  await sheet.locator('input[type="file"]').setInputFiles({ name: "bill.png", mimeType: "image/png", buffer: PNG });
  await expect(sheet.getByRole("img", { name: "Receipt bill.png" })).toBeVisible();
  await sheet.getByRole("button", { name: "Remove bill.png" }).click();
  await expect(sheet.getByRole("img", { name: "Receipt bill.png" })).toHaveCount(0);
});

test("budgets: add one from the suggestions; alerts open from the bell", async ({ page }) => {
  await signIn(page);
  await page.goto("/budgets");
  const suggestions = page.locator("section", { has: page.getByRole("heading", { name: "Suggested from your last three months" }) });
  await suggestions.getByRole("button", { name: /Add ₹/ }).first().click();
  await expect(page.locator("section", { has: page.getByRole("heading", { name: /Budgets for/ }) }).getByText("Housing")).toBeVisible();
  await page.getByRole("button", { name: /^Alerts/ }).click();
  await expect(page.getByRole("dialog", { name: "Alerts" })).toBeVisible();
});

test("change the password and change it back", async ({ page }) => {
  await signIn(page);
  await page.goto("/settings#security");
  const change = async (from: string, to: string) => {
    await page.getByLabel("Current password").fill(from);
    await page.getByLabel("New password", { exact: true }).fill(to);
    await page.getByLabel("New password again").fill(to);
    await page.getByRole("button", { name: "Change password" }).click();
    await expect(page.getByText(/^Password changed/)).toBeVisible();
  };
  await change(PASSWORD, "a-temporary-passphrase-42");
  await change("a-temporary-passphrase-42", PASSWORD);
});

test("phone: the + button opens Add; calendar fits the screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  await page.getByRole("navigation", { name: "Quick" }).getByRole("button", { name: "Add a transaction" }).click();
  await expect(page.getByRole("dialog").getByRole("tab", { name: "Expense" })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.goto("/transactions?view=calendar&start_date=2026-08-01&end_date_exclusive=2026-09-01");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  // Large day totals use short amounts on a phone instead of being cut off.
  await expect(page.getByText("1.25L", { exact: true })).toBeVisible();
});

test("phone alerts: switching them on gives a private ntfy topic", async ({ page }) => {
  await signIn(page);
  await page.goto("/settings#phone");
  const panel = page.locator("#phone");
  // Point at a closed local port first, so the test never publishes to the real ntfy.sh.
  await panel.getByText("Your own ntfy server, or a new topic").click();
  await panel.getByLabel("ntfy server", { exact: true }).fill("http://localhost:9");
  await panel.getByRole("button", { name: "Save" }).click();
  // The switch is controlled by the saved setting, so click its label and wait for the server.
  await panel.getByText("Send alerts to my phone").click();
  await expect(panel.getByText(/^oneledger-[A-Za-z0-9_-]{20,}$/)).toBeVisible();
  await expect(panel.getByRole("switch", { name: /Send alerts to my phone/ })).toBeChecked();
  await panel.getByText("Send alerts to my phone").click();
  await expect(panel.getByRole("switch", { name: /Send alerts to my phone/ })).not.toBeChecked();
});

test("import: picking a statement file chooses its account", async ({ page }) => {
  await signIn(page);
  await page.goto("/imports");
  const file = path.resolve(__dirname, "../../../tests/fixtures/synthetic_hdfc_jun_aug_2026.csv");
  await page.getByLabel("Statement file").setInputFiles(file);
  await expect(page.getByRole("status").filter({ hasText: "Account chosen from the file" })).toContainText("ending 1234");
  await expect(page.getByLabel("Account").locator("option:checked")).toHaveText("HDFC Savings");
});

test("adding a credit card as an account asks for its billing dates", async ({ page }) => {
  await signIn(page);
  await page.goto("/accounts?new=1");
  const sheet = page.getByRole("dialog");
  await sheet.getByLabel("Name").fill("Amex Gold");
  await sheet.getByLabel("Type").selectOption({ label: "Credit card" });
  await sheet.getByLabel("Statement day of month").fill("12");
  await sheet.getByLabel("Days to pay after statement").fill("18");
  await sheet.getByRole("button", { name: "Add account" }).click();
  // The billing dates are saved in a second request; the app opens the new account when both are done.
  await page.waitForURL(/\/accounts\/[0-9a-f-]{36}$/);
  await page.goto("/cards");
  await expect(page.getByText(/Statement on day 12 of each month, due 18 days later/)).toBeVisible();
});

test("two-step setup shows a QR code and the key", async ({ page }) => {
  await signIn(page);
  await page.goto("/settings#security");
  await page.getByRole("button", { name: "Turn on two-step sign-in" }).click();
  const qr = page.getByRole("img", { name: /QR code to add OneLedger/ });
  await expect(qr).toBeVisible();
  expect(await qr.getAttribute("src")).toMatch(/^data:image\/png;base64,/);
  await expect(page.getByText(/Can.t scan\? Enter this key/)).toBeVisible();
  if (process.env.SHOT_DIR) await page.locator("form", { has: qr }).screenshot({ path: `${process.env.SHOT_DIR}/totp-settings.png` });
  await page.getByRole("button", { name: "Cancel" }).click();
});
