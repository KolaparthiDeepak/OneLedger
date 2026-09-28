/** Exact decimal addition of server money strings (8 decimal places), so display totals never drift. */
export function addMoney(...values: (string | null | undefined)[]): string {
  const SCALE = 8;
  let total = BigInt(0);
  for (const v of values) {
    if (!v) continue;
    const neg = v.startsWith("-");
    const [int, frac = ""] = v.replace(/^[-+]/, "").split(".");
    const units = BigInt((int || "0") + (frac + "0".repeat(SCALE)).slice(0, SCALE));
    total += neg ? -units : units;
  }
  const neg = total < BigInt(0);
  const digits = (neg ? -total : total).toString().padStart(SCALE + 1, "0");
  const frac = digits.slice(-SCALE).replace(/0+$/, "");
  return `${neg ? "-" : ""}${digits.slice(0, -SCALE)}${frac ? `.${frac}` : ""}`;
}

type Flows = { income?: string; net_expenses?: string; unclassified_inflow?: string; unclassified_outflow?: string };

/** "Money in": income plus credits not yet categorised (own-account transfers excluded). */
export const moneyIn = (d: Flows) => addMoney(d.income, d.unclassified_inflow);

/** "Spent": purchases net of refunds plus debits not yet categorised (transfers, card bill payments, investments and loan principal excluded). */
export const spent = (d: Flows) => addMoney(d.net_expenses, d.unclassified_outflow);

/** Flip the sign of a server money string ("-5" ↔ "5") without floating point. */
export function negate(v: string | null | undefined): string | null | undefined {
  if (v == null || v === "") return v;
  if (/^[-+]?0*(\.0*)?$/.test(v)) return v.replace(/^[-+]/, "");
  return v.startsWith("-") ? v.slice(1) : `-${v.replace(/^\+/, "")}`;
}
