"use client";

import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { compactINR, formatMoney, formatMonth } from "@/lib/format";
import { moneyIn, spent } from "@/lib/money";

type MonthPoint = { month: string; income: string; net_expenses: string; unclassified_inflow?: string; unclassified_outflow?: string };

/** Money in vs spent per month (same definitions as the home summary, uncategorised included). */
export function MonthlyChart({ months }: { months: MonthPoint[] }) {
  const data = months.map((m) => {
    const inn = moneyIn(m), out = spent(m);
    // Bar heights only; the tooltip and table show the exact strings.
    return { label: formatMonth(m.month).split(" ")[0]?.slice(0, 3), in: Number(inn), out: Number(out), raw: { in: inn, out } };
  });
  return (
    <figure>
      <div className="h-52" aria-hidden>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} barGap={3} barCategoryGap="28%" margin={{ left: -8, right: 0, top: 4, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--rule)" strokeDasharray="2 4" />
            <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: "var(--ink-faint)", fontSize: 12 }} />
            <YAxis tickFormatter={compactINR} tickLine={false} axisLine={false} width={56} tick={{ fill: "var(--ink-faint)", fontSize: 12 }} />
            <Tooltip
              cursor={{ fill: "var(--sunken)" }}
              contentStyle={{ background: "var(--surface)", border: "1px solid var(--rule)", borderRadius: 10, color: "var(--ink)", boxShadow: "var(--shadow)", fontSize: 13 }}
              formatter={(_v, name, item) => [formatMoney(name === "Money in" ? item.payload.raw.in : item.payload.raw.out), name]}
            />
            <Bar dataKey="in" name="Money in" fill="var(--credit)" radius={[4, 4, 0, 0]} maxBarSize={18} isAnimationActive={false} />
            <Bar dataKey="out" name="Spent" fill="var(--debit)" fillOpacity={0.85} radius={[4, 4, 0, 0]} maxBarSize={18} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-3 flex gap-5 text-xs text-ink-soft">
        <span className="inline-flex items-center gap-1.5"><span className="inline-block size-2 rounded-full bg-credit" />Money in</span>
        <span className="inline-flex items-center gap-1.5"><span className="inline-block size-2 rounded-full bg-debit" />Spent</span>
      </figcaption>
      {/* Not shown on screen: lets screen readers read the chart's numbers. */}
      <div className="sr-only">
        <table>
          <thead className="text-ink-faint"><tr><th className="py-1 font-normal">Month</th><th className="font-normal text-right">In</th><th className="font-normal text-right">Spent</th></tr></thead>
          <tbody>
            {months.map((m, i) => (
              <tr key={m.month} className="border-t border-rule">
                <td className="py-1">{formatMonth(m.month)}</td>
                <td className="num text-right">{formatMoney(data[i]!.raw.in)}</td>
                <td className="num text-right">{formatMoney(data[i]!.raw.out)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

export function NetWorthChart({ points }: { points: { date: string; net_worth: string; partial: boolean }[] }) {
  const data = points.map((p) => ({ date: p.date, v: Number(p.net_worth), raw: p }));
  return (
    <figure>
      <div className="h-56" aria-hidden>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--rule)" />
            <XAxis dataKey="date" tickLine={false} axisLine={false} tick={{ fill: "var(--ink-faint)", fontSize: 12 }} minTickGap={32} />
            <YAxis tickFormatter={compactINR} tickLine={false} axisLine={false} width={60} tick={{ fill: "var(--ink-faint)", fontSize: 12 }} />
            <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--rule)", borderRadius: 8, color: "var(--ink)" }}
              formatter={(_v, _n, item) => [formatMoney(item.payload.raw.net_worth) + (item.payload.raw.partial ? " (incomplete)" : ""), "Net worth"]} />
            <Line type="monotone" dataKey="v" stroke="var(--ink)" strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-ink-faint">Show as table</summary>
        <table className="mt-2 w-full text-left">
          <tbody>
            {points.map((p) => (
              <tr key={p.date} className="border-t border-rule"><td className="py-1">{p.date}</td><td className="num text-right">{formatMoney(p.net_worth)}{p.partial ? " (incomplete)" : ""}</td></tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

/** Horizontal share bars; widths are display-only and never feed back into totals. */
export function ShareBars({ items, currency = "INR", hrefFor }: { items: { key: string; label: string; amount: string; share?: string }[]; currency?: string; hrefFor?: (key: string) => string }) {
  const max = Math.max(...items.map((i) => Math.abs(Number(i.amount))), 1);
  return (
    <ul className="-mx-2 flex flex-col">
      {items.map((i) => {
        const w = Math.max(2, (Math.abs(Number(i.amount)) / max) * 100);
        const body = (
          <>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className={i.key === "uncategorised" ? "truncate text-review" : "truncate"}>{i.label}</span>
              <span className="num shrink-0 font-medium">{formatMoney(i.amount, currency)}<span className="ml-2 inline-block w-12 text-right text-xs font-normal text-ink-faint">{i.share ? `${i.share}%` : ""}</span></span>
            </div>
            <div className="mt-1.5 h-1 rounded-full bg-sunken"><div className={i.key === "uncategorised" ? "h-1 rounded-full bg-review" : "h-1 rounded-full bg-ink/60"} style={{ width: `${w}%` }} /></div>
          </>
        );
        return <li key={i.key}>{hrefFor ? <a href={hrefFor(i.key)} className="block rounded-lg px-2 py-2 hover:bg-sunken/60">{body}</a> : <div className="px-2 py-2">{body}</div>}</li>;
      })}
    </ul>
  );
}
