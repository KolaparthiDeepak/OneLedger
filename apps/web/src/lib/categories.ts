/** Stable chart colours per top-level category, from the theme's --cat-* inks. */
// Chosen so the categories most people have in a month never share a colour.
const ORDER: Record<string, number> = {
  FOOD: 1, HOUSING: 2, SHOPPING: 3, LOANS: 4, TRAVEL: 5, ENTERTAINMENT: 6, TRANSPORT: 7, SUBSCRIPTIONS: 8,
  HEALTHCARE: 9, CASH: 10, OTHER: 11, EDUCATION: 8, INSURANCE: 6, TAXES: 9, FEES: 11, INVESTMENTS: 3,
  INCOME: 1, INCOME_SALARY: 1, TRANSFERS: 10,
};

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return (h % 11) + 1;
}

export function categoryColor(code: string | null | undefined): string {
  if (!code) return "var(--review)";
  const root = code.split("_")[0] ?? code;
  const n = ORDER[code] ?? ORDER[root] ?? hash(root);
  return `var(--cat-${n})`;
}
