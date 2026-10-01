/**
 * Money formatting from decimal strings. No floating-point arithmetic is used for amounts:
 * the API sends exact decimal strings and we only regroup digits.
 */

const SYMBOL: Record<string, string> = { INR: "₹", USD: "$", EUR: "€", GBP: "£" };

function groupIndian(whole: string): string {
  if (whole.length <= 3) return whole;
  const tail = whole.slice(-3);
  let head = whole.slice(0, -3);
  const parts: string[] = [];
  while (head.length > 2) {
    parts.unshift(head.slice(-2));
    head = head.slice(0, -2);
  }
  if (head) parts.unshift(head);
  return `${parts.join(",")},${tail}`;
}

function groupWestern(whole: string): string {
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export function splitDecimal(value: string): { negative: boolean; whole: string; frac: string } {
  const v = value.trim();
  const negative = v.startsWith("-");
  const [w = "0", f = ""] = v.replace(/^[-+]/, "").split(".");
  return { negative, whole: w.replace(/^0+(?=\d)/, "") || "0", frac: f };
}

export function formatMoney(
  value: string | null | undefined,
  currency = "INR",
  opts: { signed?: boolean; absolute?: boolean; decimals?: boolean } = {},
): string {
  if (value === null || value === undefined || value === "") return "—";
  const { negative, whole, frac } = splitDecimal(value);
  const decimals = opts.decimals === false ? "" : `.${(frac + "00").slice(0, Math.max(2, frac.replace(/0+$/, "").length))}`;
  const grouped = currency === "INR" ? groupIndian(whole) : groupWestern(whole);
  const sym = SYMBOL[currency] ?? `${currency} `;
  const isZero = /^0*$/.test(whole + frac);
  let sign = "";
  if (!opts.absolute && negative && !isZero) sign = "−";
  else if (opts.signed && !negative && !isZero) sign = "+";
  return `${sign}${sym}${grouped}${decimals}`;
}

/** Compact Indian units for chart axes only (display, never arithmetic on results). */
export function compactINR(n: number): string {
  const a = Math.abs(n);
  const s = n < 0 ? "−" : "";
  if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(1)}Cr`;
  if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(1)}L`;
  if (a >= 1e3) return `${s}₹${(a / 1e3).toFixed(0)}k`;
  return `${s}₹${a.toFixed(0)}`;
}

/** Short amounts for chart labels that name a value (not axis ticks): ₹1.25L, ₹4.6k. Display only. */
export function shortINR(n: number): string {
  const a = Math.abs(n);
  const s = n < 0 ? "−" : "";
  const trim = (x: number, dp: number) => String(Number(x.toFixed(dp))); // 1.25, 1.3, 30 (not "30.00")
  if (a >= 1e7) return `${s}₹${trim(a / 1e7, 2)}Cr`;
  if (a >= 1e5) return `${s}₹${trim(a / 1e5, 2)}L`;
  if (a >= 1e4) return `${s}₹${trim(a / 1e3, 0)}k`;
  if (a >= 1e3) return `${s}₹${trim(a / 1e3, 1)}k`;
  return `${s}₹${a.toFixed(0)}`;
}

export function isNegative(value: string | null | undefined): boolean {
  return !!value && value.trim().startsWith("-") && !/^-0*(\.0*)?$/.test(value.trim());
}

const DATE = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const DAY = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
const MONTH = new Intl.DateTimeFormat("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });

export function formatDate(iso: string | null | undefined, withYear = true): string {
  if (!iso) return "—";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return (withYear ? DATE : DAY).format(d);
}

export function formatMonth(isoOrYm: string): string {
  return MONTH.format(new Date(`${isoOrYm.slice(0, 7)}-01T00:00:00Z`));
}

/** Converts an exclusive end date into the inclusive last day for display. */
export function inclusiveEnd(endExclusive: string): string {
  const d = new Date(`${endExclusive}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

export function periodLabel(start?: string | null, endExclusive?: string | null): string {
  if (!start || !endExclusive) return "";
  const end = inclusiveEnd(endExclusive);
  if (start.slice(8) === "01" && end.slice(0, 7) === start.slice(0, 7)) {
    const lastDay = new Date(Date.UTC(Number(start.slice(0, 4)), Number(start.slice(5, 7)), 0)).getUTCDate();
    if (Number(end.slice(8)) === lastDay) return formatMonth(start);
  }
  return `${formatDate(start, start.slice(0, 4) !== end.slice(0, 4))} to ${formatDate(end)}`;
}

export function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

export const KIND_LABEL: Record<string, string> = {
  BANK_SAVINGS: "Savings", BANK_CURRENT: "Current", CASH: "Cash", WALLET: "Wallet", CREDIT_CARD: "Credit card",
  LOAN: "Loan", BROKERAGE: "Brokerage", INVESTMENT: "Investment", FIXED_DEPOSIT: "Fixed deposit",
  OTHER_ASSET: "Other asset", OTHER_LIABILITY: "Other liability", PERSON: "Shared with people",
};

export const EFFECT_LABEL: Record<string, string> = {
  income: "Income", expense: "Expense", transfer: "Transfer", investment: "Investment",
  loan_principal: "Loan principal", adjustment: "Adjustment", unclassified: "Unclassified",
};

/** How a person's shared balance reads everywhere: "Priya owes you" or "You owe Priya". */
export function personLabel(name: string, balance: string | null): string {
  return balance?.trim().startsWith("-") ? `You owe ${name}` : `${name} owes you`;
}
