"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { CashFlowSankey, DailyBars, Donut } from "@/components/charts";
import { Icon } from "@/components/icons";
import { Amount, cx, Empty, ErrorNote, Loading, PageHeader, Panel, Provenance } from "@/components/ui";
import { useApi } from "@/lib/api";
import { categoryColor } from "@/lib/categories";
import { formatMoney, todayISO } from "@/lib/format";
import { moneyIn, spent } from "@/lib/money";
import { periodFor, periodTitle, shiftPeriod, type PeriodKind } from "@/lib/period";

type Sub = { category_id: string; code: string; name: string; amount: string; count: number };
type Cat = { category_id: string | null; code: string | null; name: string; amount: string; share: string; count: number; subcategories: Sub[] };
type Breakdown = { data: { total: string; categories: Cat[] }; provenance: { query_id: string; start_date: string; end_date_exclusive: string; transaction_count: number; partial: boolean; warnings: string[]; evidence_url: string } };
type Summary = { data: { income: string; net_expenses: string; unclassified_inflow: string; unclassified_outflow: string; investment_contributions: string; loan_principal_paid: string } };
type Sankey = { nodes: { key: string; name: string; group: string }[]; links: { source: number; target: number; value: string }[]; money_in: string; money_out: string };

const KINDS: { key: PeriodKind; label: string }[] = [
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
  { key: "year", label: "Year" },
];
const TABS = [
  { key: "spending", label: "Spending" },
  { key: "income", label: "Income" },
  { key: "flow", label: "Cash flow" },
] as const;

function Breakdown({ b, total, caption, range, effect }: { b: Breakdown; total: string; caption: string; range: string; effect: "expense" | "income" }) {
  const [picked, setPicked] = useState<string | null>(null);
  const cats = b.data.categories;
  if (!cats.length) return <Empty title={effect === "expense" ? "No spending in this period" : "No income in this period"}>Try another period, or import a statement that covers it.</Empty>;
  const slices = cats.map((c) => ({ key: c.category_id ?? "uncategorised", code: c.code, label: c.name, amount: c.amount, share: c.share }));
  return (
    <div className="grid items-start gap-8 md:grid-cols-[260px_minmax(0,1fr)]">
      <div className="md:sticky md:top-6"><Donut slices={slices} total={total} caption={caption} picked={picked} onPick={(k) => setPicked(picked === k ? null : k)} /></div>
      <ul className="flex flex-col divide-y divide-rule">
        {cats.map((c) => {
          const key = c.category_id ?? "uncategorised";
          const open = picked === key;
          return (
            <li key={key} className={cx(picked && !open && "opacity-60")}>
              <button type="button" onClick={() => setPicked(open ? null : key)} aria-expanded={open}
                className="-mx-2 flex w-[calc(100%+1rem)] items-center gap-3 rounded-lg px-2 py-2.5 text-left hover:bg-sunken/60">
                <span aria-hidden className="size-3 shrink-0 rounded-sm" style={{ background: c.category_id ? categoryColor(c.code) : "var(--review)" }} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{c.name}</span>
                  <span className="text-xs text-ink-faint">{c.count} {c.count === 1 ? "transaction" : "transactions"}</span>
                </span>
                <span className="text-right">
                  <span className="num block font-medium">{formatMoney(c.amount)}</span>
                  <span className="num block text-xs text-ink-faint">{c.share}%</span>
                </span>
              </button>
              {open ? (
                <div className="mb-3 ml-6 flex flex-col gap-1 text-sm">
                  {c.subcategories.length ? c.subcategories.map((s) => (
                    <Link key={s.category_id} href={`/transactions?category_id=${s.category_id}&${range}`} className="flex justify-between gap-3 rounded-md px-2 py-1 text-ink-soft hover:bg-sunken hover:text-ink">
                      <span>{s.name}</span><span className="num">{formatMoney(s.amount)}</span>
                    </Link>
                  )) : null}
                  <Link href={c.category_id ? `/transactions?category_id=${c.category_id}&${range}` : `/transactions?uncategorized=1&${range}`} className="px-2 py-1 text-xs font-medium text-ink-soft underline-offset-4 hover:underline">
                    See {c.name.toLowerCase()} transactions
                  </Link>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Phone layout of the cash-flow diagram: where money came from, then where it went, as bars. */
function FlowList({ flow }: { flow: Sankey }) {
  const hub = flow.nodes.findIndex((n) => n.group === "hub");
  const ins = flow.links.filter((l) => l.target === hub);
  const outs = flow.links.filter((l) => l.source === hub).sort((a, b) => Number(b.value) - Number(a.value));
  const max = Math.max(...flow.links.map((l) => Number(l.value)), 1);
  const row = (l: { source: number; target: number; value: string }, idx: number) => {
    const n = flow.nodes[idx]!;
    const color = n.group === "income" ? "var(--credit)" : n.group === "saving" ? "var(--cat-3)" : n.key === "out:uncat" ? "var(--review)" : categoryColor(n.key.replace(/^out:/, ""));
    return (
      <li key={`${l.source}-${l.target}`} className="py-1.5">
        <div className="flex justify-between gap-3 text-sm"><span className="truncate">{n.name}</span><span className="num shrink-0">{formatMoney(l.value, "INR", { decimals: false })}</span></div>
        <div className="mt-1 h-1.5 rounded-full bg-sunken"><div className="h-1.5 rounded-full" style={{ width: `${Math.max(2, (Number(l.value) / max) * 100)}%`, background: color }} /></div>
      </li>
    );
  };
  return (
    <div className="flex flex-col gap-4 sm:hidden">
      <section><h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">Came in</h3><ul>{ins.map((l) => row(l, l.source))}</ul></section>
      <section><h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">Went to</h3><ul>{outs.map((l) => row(l, l.target))}</ul></section>
    </div>
  );
}

function StatsInner() {
  const params = useSearchParams();
  const router = useRouter();
  const kind = (params.get("kind") as PeriodKind) || "month";
  const anchor = params.get("anchor") || todayISO();
  const tab = (params.get("tab") as (typeof TABS)[number]["key"]) || "spending";
  const p = periodFor(kind, anchor);
  const range = `start_date=${p.start}&end_date_exclusive=${p.endExclusive}`;
  const set = (next: Record<string, string>) => {
    const u = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(next)) u.set(k, v);
    router.replace(`/stats?${u.toString()}`, { scroll: false });
  };
  const { data: summary } = useApi<Summary>(`/analytics/summary?${range}`);
  const { data: spend, error } = useApi<Breakdown>(tab === "spending" ? `/analytics/categories?${range}` : null);
  const { data: income } = useApi<Breakdown>(tab === "income" ? `/analytics/categories?effect=income&${range}` : null);
  const { data: daily } = useApi<{ days: { date: string; spent: string; money_in: string }[] }>(tab === "spending" && kind !== "year" ? `/analytics/daily?${range}` : null);
  const { data: flow } = useApi<Sankey>(tab === "flow" ? `/analytics/sankey?${range}` : null);
  const s = summary?.data;
  const future = p.start > todayISO();

  const spendTotal = spend ? spend.data.total : "0";
  const uncatOut = s?.unclassified_outflow ?? "0";

  return (
    <>
      <PageHeader title="Stats" description="Where your money came from and went, for any week, month or year." />
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center">
          <button type="button" onClick={() => set({ anchor: shiftPeriod(p, -1).start })} aria-label="Previous period" className="inline-flex size-9 items-center justify-center rounded-lg text-ink-soft hover:bg-sunken hover:text-ink"><Icon name="left" className="size-5" /></button>
          <h2 className="display min-w-[10rem] text-center text-xl font-medium">{periodTitle(p)}</h2>
          <button type="button" onClick={() => set({ anchor: shiftPeriod(p, 1).start })} disabled={shiftPeriod(p, 1).start > todayISO()} aria-label="Next period" className="inline-flex size-9 items-center justify-center rounded-lg text-ink-soft hover:bg-sunken hover:text-ink disabled:opacity-30"><Icon name="right" className="size-5" /></button>
        </div>
        <div role="tablist" aria-label="Period" className="inline-flex rounded-lg border border-rule bg-surface p-0.5">
          {KINDS.map((k) => (
            <button key={k.key} role="tab" type="button" aria-selected={kind === k.key} onClick={() => set({ kind: k.key, anchor: kind === k.key ? anchor : p.start })}
              className={cx("min-h-8 rounded-md px-3 text-sm font-medium", kind === k.key ? "bg-accent text-accent-ink" : "text-ink-soft hover:text-ink")}>{k.label}</button>
          ))}
        </div>
      </div>

      {s ? (
        <dl className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: "Money in", v: moneyIn(s), tone: "text-credit" },
            { label: "Spent", v: spent(s), tone: "text-debit" },
            { label: "Invested", v: s.investment_contributions, tone: "" },
            { label: "Loan principal", v: s.loan_principal_paid, tone: "" },
          ].map((x) => (
            <div key={x.label} className="rounded-xl border border-rule bg-surface px-4 py-3">
              <dt className="text-xs text-ink-faint">{x.label}</dt>
              <dd className={cx("num mt-1 text-lg font-medium", x.tone)}>{formatMoney(x.v, "INR", { decimals: false })}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      <div role="tablist" aria-label="Report" className="mb-5 flex gap-1 border-b border-rule">
        {TABS.map((t) => (
          <button key={t.key} role="tab" type="button" aria-selected={tab === t.key} onClick={() => set({ tab: t.key })}
            className={cx("-mb-px border-b-2 px-3 py-2 text-sm font-medium", tab === t.key ? "border-ink text-ink" : "border-transparent text-ink-soft hover:text-ink")}>{t.label}</button>
        ))}
      </div>

      {future ? <Empty title="This period hasn't started yet" /> : tab === "spending" ? (
        error ? <ErrorNote error={error} /> : !spend ? <Loading /> : (
          <div className="flex flex-col gap-6">
            {daily && daily.days.length ? (
              <Panel title="Spent each day"><DailyBars days={daily.days} /></Panel>
            ) : null}
            <Panel title="By category">
              <Breakdown b={spend} total={spendTotal} caption="Categorised" range={range} effect="expense" />
              {Number(uncatOut) > 0 ? (
                <p className="mt-4 rounded-lg bg-review-wash px-3 py-2 text-sm">
                  <Amount value={uncatOut} colored={false} signed={false} /> went out without a category and isn&apos;t in the chart. <Link href={`/transactions?uncategorized=1&${range}`} className="font-medium underline">Categorise it</Link>
                </p>
              ) : null}
              <Provenance p={spend.provenance} />
            </Panel>
          </div>
        )
      ) : tab === "income" ? (
        !income ? <Loading /> : <Panel title="By source"><Breakdown b={income} total={income.data.total} caption="Income" range={range} effect="income" /><Provenance p={income.provenance} /></Panel>
      ) : (
        !flow ? <Loading /> : flow.links.length ? (
          <Panel title="Where the money went">
            <p className="-mt-2 mb-3 text-sm text-ink-soft">Transfers between your own accounts are left out. What you kept is money in minus everything that went out.</p>
            <div className="hidden sm:block"><CashFlowSankey nodes={flow.nodes} links={flow.links} /></div>
            <FlowList flow={flow} />
          </Panel>
        ) : <Empty title="Nothing to show for this period" />
      )}
    </>
  );
}

export default function StatsPage() {
  return <Suspense fallback={<Loading />}><StatsInner /></Suspense>;
}
