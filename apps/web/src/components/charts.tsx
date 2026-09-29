"use client";

import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, Pie, PieChart, ResponsiveContainer, Sankey, Tooltip, XAxis, YAxis } from "recharts";
import type { LinkProps, NodeProps } from "recharts/types/chart/Sankey";
import { categoryColor } from "@/lib/categories";
import { compactINR, formatDate, formatMoney, formatMonth, shortINR } from "@/lib/format";
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

const TOOLTIP = { background: "var(--surface)", border: "1px solid var(--rule)", borderRadius: 10, color: "var(--ink)", boxShadow: "var(--shadow)", fontSize: 13 };

/** Net worth over time. Month-end points rebuilt from balances and daily snapshots share one line;
 * points where some balance was unknown are drawn hollow and say so. */
export function NetWorthChart({ points }: { points: { date: string; net_worth: string; partial: boolean; source?: string }[] }) {
  const data = points.map((p) => ({ date: p.date, v: Number(p.net_worth), raw: p }));
  return (
    <figure>
      <div className="h-56" aria-hidden>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
            <defs>
              <linearGradient id="nw-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--ink)" stopOpacity={0.14} />
                <stop offset="100%" stopColor="var(--ink)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="var(--rule)" strokeDasharray="2 4" />
            <XAxis dataKey="date" tickLine={false} axisLine={false} tick={{ fill: "var(--ink-faint)", fontSize: 12 }} minTickGap={40}
              tickFormatter={(d: string) => formatDate(d, false)} />
            <YAxis tickFormatter={compactINR} tickLine={false} axisLine={false} width={60} tick={{ fill: "var(--ink-faint)", fontSize: 12 }} domain={["auto", "auto"]} />
            <Tooltip contentStyle={TOOLTIP} labelFormatter={(d) => formatDate(String(d))}
              formatter={(_v, _n, item) => [formatMoney(item.payload.raw.net_worth) + (item.payload.raw.partial ? " (incomplete)" : ""), "Net worth"]} />
            <Area type="monotone" dataKey="v" stroke="var(--ink)" strokeWidth={2} fill="url(#nw-fill)" isAnimationActive={false}
              dot={(props: { cx?: number; cy?: number; index?: number }) => {
                const p = data[props.index ?? 0]?.raw;
                if (!p || props.cx == null || props.cy == null || !(p.partial || data.length < 16)) return <g key={props.index} />;
                return <circle key={props.index} cx={props.cx} cy={props.cy} r={3} fill={p.partial ? "var(--surface)" : "var(--ink)"} stroke="var(--ink)" strokeWidth={1.5} />;
              }} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {points.some((p) => p.partial) ? <figcaption className="mt-2 text-xs text-ink-faint">Hollow points: some balances were unknown on that date, so the figure is incomplete.</figcaption> : null}
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-ink-faint">Show as table</summary>
        <table className="mt-2 w-full text-left">
          <tbody>
            {points.map((p) => (
              <tr key={p.date} className="border-t border-rule"><td className="py-1">{formatDate(p.date)}</td><td className="num text-right">{formatMoney(p.net_worth)}{p.partial ? " (incomplete)" : ""}</td></tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

/** Balance of one account over time (end of day). */
export function BalanceChart({ points, liability }: { points: { date: string; balance: string }[]; liability?: boolean }) {
  const data = points.map((p) => ({ date: p.date, v: Number(p.balance), raw: p.balance }));
  const tone = liability ? "var(--debit)" : "var(--cat-1)";
  return (
    <figure>
      <div className="h-44" aria-hidden>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ left: 0, right: 4, top: 6, bottom: 0 }}>
            <defs>
              <linearGradient id="bal-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={tone} stopOpacity={0.22} />
                <stop offset="100%" stopColor={tone} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="var(--rule)" strokeDasharray="2 4" />
            <XAxis dataKey="date" tickLine={false} axisLine={false} tick={{ fill: "var(--ink-faint)", fontSize: 11 }} minTickGap={36} tickFormatter={(d: string) => formatDate(d, false)} />
            <YAxis tickFormatter={compactINR} tickLine={false} axisLine={false} width={52} tick={{ fill: "var(--ink-faint)", fontSize: 11 }} domain={["auto", "auto"]} />
            <Tooltip contentStyle={TOOLTIP} labelFormatter={(d) => formatDate(String(d))} formatter={(_v, _n, item) => [formatMoney(item.payload.raw), liability ? "Owed" : "Balance"]} />
            <Area type="stepAfter" dataKey="v" stroke={tone} strokeWidth={1.8} fill="url(#bal-fill)" isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <p className="sr-only">Balance from {formatDate(points[0]?.date)} to {formatDate(points[points.length - 1]?.date)}: {formatMoney(points[points.length - 1]?.balance)} at the end.</p>
    </figure>
  );
}

export type Slice = { key: string; label: string; amount: string; share?: string; code?: string | null };

/** Donut of category shares with the total in the middle. Colours follow the category, not the rank. */
export function Donut({ slices, total, caption, onPick, picked }: { slices: Slice[]; total: string; caption: string; onPick?: (key: string) => void; picked?: string | null }) {
  const data = slices.map((s) => ({ ...s, v: Math.abs(Number(s.amount)) }));
  return (
    <figure className="relative mx-auto aspect-square w-full max-w-[260px]">
      <div className="absolute inset-0" aria-hidden>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={data} dataKey="v" nameKey="label" innerRadius="66%" outerRadius="96%" paddingAngle={data.length > 1 ? 1.2 : 0} stroke="var(--surface)" strokeWidth={2}
              isAnimationActive={false} onClick={(_d, index) => { const k = data[index]?.key; if (k) onPick?.(k); }}>
              {data.map((d) => (
                <Cell key={d.key} fill={d.key === "uncategorised" ? "var(--review)" : categoryColor(d.code)} opacity={picked && picked !== d.key ? 0.35 : 1} className={onPick ? "cursor-pointer" : undefined} />
              ))}
            </Pie>
            <Tooltip contentStyle={TOOLTIP} formatter={(_v, _n, item) => [formatMoney(item.payload.amount), item.payload.label]} />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
        <span className="text-xs text-ink-faint">{caption}</span>
        <span className="display text-[1.55rem] font-medium leading-tight">{formatMoney(total, "INR", { decimals: false })}</span>
      </figcaption>
    </figure>
  );
}

type SankeyNodeIn = { key: string; name: string; group: string };

/** Where money came from and where it went in a period. */
export function CashFlowSankey({ nodes, links }: { nodes: SankeyNodeIn[]; links: { source: number; target: number; value: string }[] }) {
  const data = { nodes: nodes.map((n) => ({ ...n })), links: links.map((l) => ({ source: l.source, target: l.target, value: Number(l.value), raw: l.value })) };
  // Money kept, invested and loan principal each get their own colour, apart from the spending categories.
  const SAVING: Record<string, string> = { "out:kept": "var(--credit)", "out:invest": "var(--focus)", "out:principal": categoryColor("LOANS") };
  const colorOf = (n: SankeyNodeIn) =>
    n.group === "hub" ? "var(--ink)" : n.group === "income" ? "var(--credit)" : n.group === "saving" ? (SAVING[n.key] ?? "var(--cat-3)") : n.key === "out:uncat" ? "var(--review)" : categoryColor(n.key.replace(/^out:/, ""));
  const height = Math.max(260, Math.min(620, nodes.length * 34));
  return (
    <figure>
      <div style={{ height }} aria-hidden>
        <ResponsiveContainer width="100%" height="100%">
          <Sankey data={data} nodePadding={18} nodeWidth={10} linkCurvature={0.55} iterations={48} margin={{ left: 150, right: 170, top: 24, bottom: 8 }}
            node={(props: NodeProps) => {
              const n = nodes[props.index]!;
              const right = n.group !== "income";
              if (n.group === "hub") {
                return (
                  <g key={props.index}>
                    <rect x={props.x} y={props.y} width={props.width} height={Math.max(props.height, 2)} rx={2} fill={colorOf(n)} />
                    <text x={props.x + props.width / 2} y={props.y - 8} textAnchor="middle" fontSize={12} fontWeight={600} fill="var(--ink)">
                      {n.name}<tspan fill="var(--ink-faint)" fontWeight={400} dx={6}>{shortINR(Number(props.payload.value ?? 0))}</tspan>
                    </text>
                  </g>
                );
              }
              return (
                <g key={props.index}>
                  <rect x={props.x} y={props.y} width={props.width} height={Math.max(props.height, 2)} rx={2} fill={colorOf(n)} />
                  <text x={right ? props.x + props.width + 8 : props.x - 8} y={props.y + props.height / 2} textAnchor={right ? "start" : "end"} dominantBaseline="middle" fontSize={12} fill="var(--ink)">
                    {n.name}
                    <tspan fill="var(--ink-faint)" dx={6}>{shortINR(Number(props.payload.value ?? 0))}</tspan>
                  </text>
                </g>
              );
            }}
            link={(props: LinkProps) => {
              const l = data.links[props.index]!;
              const target = nodes[l.target]!;
              const color = target.group === "hub" ? colorOf(nodes[l.source]!) : colorOf(target);
              return (
                <path key={props.index} d={`M${props.sourceX},${props.sourceY}C${props.sourceControlX},${props.sourceY} ${props.targetControlX},${props.targetY} ${props.targetX},${props.targetY}`}
                  fill="none" stroke={color} strokeOpacity={0.28} strokeWidth={Math.max(props.linkWidth, 1)} />
              );
            }}>
            <Tooltip contentStyle={TOOLTIP} formatter={(_v, _n, item) => [formatMoney(String(item.payload?.payload?.raw ?? item.value)), ""]} />
          </Sankey>
        </ResponsiveContainer>
      </div>
      <table className="sr-only">
        <tbody>
          {links.map((l, i) => <tr key={i}><td>{nodes[l.source]?.name}</td><td>{nodes[l.target]?.name}</td><td>{formatMoney(l.value)}</td></tr>)}
        </tbody>
      </table>
    </figure>
  );
}

/** Spending per day in a period (bars), with the day's exact amount on hover. Every day of the period
 * gets a slot, so days with no spending show as gaps and the axis is a real timeline. */
export function DailyBars({ days, start, endExclusive }: { days: { date: string; spent: string }[]; start?: string; endExclusive?: string }) {
  const byDate = new Map(days.map((d) => [d.date, d]));
  const dates: string[] = [];
  if (start && endExclusive) {
    for (let d = new Date(`${start}T00:00:00Z`); d.toISOString().slice(0, 10) < endExclusive && dates.length < 400; d.setUTCDate(d.getUTCDate() + 1)) {
      dates.push(d.toISOString().slice(0, 10));
    }
  } else {
    dates.push(...days.map((d) => d.date));
  }
  const data = dates.map((date) => {
    const raw = byDate.get(date) ?? { date, spent: "0" };
    return { label: date.slice(8), v: Number(raw.spent), raw };
  });
  return (
    <div className="h-36" aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ left: -8, right: 0, top: 4, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--rule)" strokeDasharray="2 4" />
          <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: "var(--ink-faint)", fontSize: 11 }} interval="preserveStartEnd" minTickGap={10} />
          <YAxis tickFormatter={compactINR} tickLine={false} axisLine={false} width={48} tick={{ fill: "var(--ink-faint)", fontSize: 11 }} />
          <Tooltip cursor={{ fill: "var(--sunken)" }} contentStyle={TOOLTIP} labelFormatter={(_l, p) => formatDate(p?.[0]?.payload?.raw?.date)} formatter={(_v, _n, item) => [formatMoney(item.payload.raw.spent), "Spent"]} />
          <Bar dataKey="v" fill="var(--debit)" fillOpacity={0.8} radius={[3, 3, 0, 0]} maxBarSize={14} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Horizontal share bars; widths are display-only and never feed back into totals. */
export function ShareBars({ items, currency = "INR", hrefFor }: { items: { key: string; label: string; amount: string; share?: string; code?: string | null }[]; currency?: string; hrefFor?: (key: string) => string }) {
  const max = Math.max(...items.map((i) => Math.abs(Number(i.amount))), 1);
  return (
    <ul className="-mx-2 flex flex-col">
      {items.map((i) => {
        const w = Math.max(2, (Math.abs(Number(i.amount)) / max) * 100);
        const body = (
          <>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className={i.key === "uncategorised" ? "flex min-w-0 items-center gap-2 text-review" : "flex min-w-0 items-center gap-2"}>
                <span aria-hidden className="inline-block size-2.5 shrink-0 rounded-sm" style={{ background: i.key === "uncategorised" ? "var(--review)" : categoryColor(i.code) }} />
                <span className="truncate">{i.label}</span>
              </span>
              <span className="num shrink-0 font-medium">{formatMoney(i.amount, currency)}<span className="ml-2 inline-block w-12 text-right text-xs font-normal text-ink-faint">{i.share ? `${i.share}%` : ""}</span></span>
            </div>
            <div className="mt-1.5 h-1.5 rounded-full bg-sunken"><div className="h-1.5 rounded-full" style={{ width: `${w}%`, background: i.key === "uncategorised" ? "var(--review)" : categoryColor(i.code), opacity: 0.85 }} /></div>
          </>
        );
        return <li key={i.key}>{hrefFor ? <a href={hrefFor(i.key)} className="block rounded-lg px-2 py-2 hover:bg-sunken/60">{body}</a> : <div className="px-2 py-2">{body}</div>}</li>;
      })}
    </ul>
  );
}
