/** Calendar periods for reports: week (Mon–Sun), month, year, or a custom range. Dates are ISO strings. */
export type PeriodKind = "week" | "month" | "year";
export type Period = { kind: PeriodKind; start: string; endExclusive: string };

const iso = (d: Date) => d.toISOString().slice(0, 10);
const utc = (s: string) => new Date(`${s}T00:00:00Z`);

export function periodFor(kind: PeriodKind, anchor: string): Period {
  const d = utc(anchor);
  if (kind === "week") {
    const dow = (d.getUTCDay() + 6) % 7; // Monday = 0
    const start = new Date(d);
    start.setUTCDate(d.getUTCDate() - dow);
    const end = new Date(start);
    end.setUTCDate(start.getUTCDate() + 7);
    return { kind, start: iso(start), endExclusive: iso(end) };
  }
  if (kind === "year") {
    return { kind, start: `${d.getUTCFullYear()}-01-01`, endExclusive: `${d.getUTCFullYear() + 1}-01-01` };
  }
  const start = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1));
  const end = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1));
  return { kind, start: iso(start), endExclusive: iso(end) };
}

export function shiftPeriod(p: Period, by: number): Period {
  const d = utc(p.start);
  if (p.kind === "week") d.setUTCDate(d.getUTCDate() + 7 * by);
  else if (p.kind === "year") d.setUTCFullYear(d.getUTCFullYear() + by);
  else d.setUTCMonth(d.getUTCMonth() + by);
  return periodFor(p.kind, iso(d));
}

const MONTH = new Intl.DateTimeFormat("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });
const DAY = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });

export function periodTitle(p: Period): string {
  if (p.kind === "month") return MONTH.format(utc(p.start));
  if (p.kind === "year") return p.start.slice(0, 4);
  const last = utc(p.endExclusive);
  last.setUTCDate(last.getUTCDate() - 1);
  return `${DAY.format(utc(p.start))} – ${DAY.format(last)}`;
}

export function monthKey(isoDate: string): string {
  return isoDate.slice(0, 7);
}

export function shiftMonth(ym: string, by: number): string {
  const d = new Date(Date.UTC(Number(ym.slice(0, 4)), Number(ym.slice(5, 7)) - 1 + by, 1));
  return iso(d).slice(0, 7);
}

/** Every date in [start, endExclusive), for calendars. */
export function daysOf(start: string, endExclusive: string): string[] {
  const out: string[] = [];
  const d = utc(start);
  const end = utc(endExclusive);
  while (d < end) {
    out.push(iso(d));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
}
