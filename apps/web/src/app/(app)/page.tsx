"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { AlertList, type Alert } from "@/components/alerts";
import { Donut, MonthlyChart, ShareBars } from "@/components/charts";
import { InfoTip } from "@/components/info-tip";
import { Icon } from "@/components/icons";
import { useQuickAdd } from "@/components/quick-add-context";
import { Amount, Button, ButtonLink, cx, Empty, ErrorNote, Loading, Meter, Panel, Provenance } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { moneyIn, negate, spent } from "@/lib/money";
import { formatDate, formatMoney, formatMonth, KIND_LABEL, todayISO } from "@/lib/format";
import { shiftMonth } from "@/lib/period";

type Prov = { query_id: string; start_date: string | null; end_date_exclusive: string | null; transaction_count: number; partial: boolean; warnings: string[]; evidence_url: string };
type Summary = { data: Record<string, string> & { income: string; net_expenses: string; unclassified_inflow: string; unclassified_outflow: string; unclassified_count: number; savings_provisional: boolean }; provenance: Prov };
type Bill = { kind: string; id: string; label: string; date: string; amount: string; currency: string; estimated: boolean; overdue: boolean; confirmed?: boolean; href: string; detail: string };
type Safe =
  | { available: false; reason: string; message: string }
  | { available: true; currency: string; liquid_balance: string; until: string; until_label: string | null; days_left: number; obligations: { label: string; date: string; amount: string; kind: string }[]; obligations_total: string; safe_total: string; per_day: string; shortfall: boolean; assumptions: string[] };
type Budget = { id: string; name: string; spent: string; available: string; percent: string; over: boolean; currency: string };
type Dashboard = {
  as_of: string;
  period: { start: string; end_exclusive: string; note: string | null };
  summary: Summary;
  previous_summary: Summary;
  categories: { data: { total: string; categories: { category_id: string | null; code: string | null; name: string; amount: string; share: string }[] }; provenance: Prov };
  monthly: { data: { months: { month: string; income: string; net_expenses: string; savings: string }[] } };
  balances: { data: { accounts: { account_id: string; name: string; kind: string; nature: string; balance: string | null; derived_through: string | null; status: string; currency: string }[]; unknown_count: number }; provenance: Prov };
  net_worth: { data: { totals: Record<string, { net_worth: string; assets: string; liabilities: string }>; changes: Record<string, { available: boolean; delta: Record<string, string> }> }; provenance: Prov };
  review_counts: Record<string, number>;
  bills: Bill[];
  safe_to_spend: Safe;
  budgets: Budget[];
  alerts: Alert[];
  data_range: { first: string | null; last: string | null };
};

function Change({ cur, prev }: { cur: string; prev: string }) {
  // Display-only comparison of two server totals; the authoritative figures are shown alongside.
  const diff = Number(cur) - Number(prev);
  if (!Number(prev) || !Number(cur)) return <span className="text-ink-faint">No earlier month to compare</span>;
  const pct = Math.round((diff / Math.abs(Number(prev))) * 100);
  return <span>{pct > 0 ? `${pct}% more than` : pct < 0 ? `${-pct}% less than` : "About the same as"} the month before</span>;
}

function Figure({ label, info, value, cur, prev, tone }: { label: string; info: React.ReactNode; value: string; cur: string; prev: string; tone: "credit" | "debit" }) {
  return (
    <div className="min-w-0 py-5 sm:py-1">
      <div className="flex items-center text-sm font-medium text-ink-soft">
        <span className={tone === "credit" ? "mr-2 inline-block size-2 rounded-full bg-credit" : "mr-2 inline-block size-2 rounded-full bg-debit"} aria-hidden />
        {label}
        <InfoTip label={`What is ${label}?`}>{info}</InfoTip>
      </div>
      <p className="display mt-1.5 text-[2.1rem] font-medium leading-none sm:text-[2.6rem]"><Amount value={value} colored={false} signed={false} /></p>
      <p className="mt-2 text-xs text-ink-faint"><Change cur={cur} prev={prev} /></p>
    </div>
  );
}

function MonthSwitch({ start, dataRange, current }: { start: string; dataRange: { first: string | null; last: string | null }; current: string }) {
  const router = useRouter();
  const ym = start.slice(0, 7);
  const first = dataRange.first?.slice(0, 7);
  const thisMonth = todayISO().slice(0, 7);
  const go = (m: string) => router.replace(m === current ? "/" : `/?month=${m}`, { scroll: false });
  const prevOk = !first || shiftMonth(ym, -1) >= first;
  const nextOk = shiftMonth(ym, 1) <= thisMonth;
  const btn = "inline-flex size-9 items-center justify-center rounded-lg text-ink-soft hover:bg-sunken hover:text-ink disabled:opacity-30 disabled:hover:bg-transparent";
  return (
    <div className="flex items-center gap-1">
      <button type="button" className={btn} disabled={!prevOk} onClick={() => go(shiftMonth(ym, -1))} aria-label="Previous month"><Icon name="left" className="size-5" /></button>
      <h1 id="month-h" className="display min-w-[9.5rem] text-center text-[1.7rem] font-medium leading-tight">{formatMonth(start)}</h1>
      <button type="button" className={btn} disabled={!nextOk} onClick={() => go(shiftMonth(ym, 1))} aria-label="Next month"><Icon name="right" className="size-5" /></button>
    </div>
  );
}

function SafeToSpend({ s }: { s: Safe }) {
  if (!s.available) return null;
  const until = formatDate(s.until, false);
  return (
    <section aria-labelledby="safe-h" className={cx("rounded-2xl border p-5 sm:p-6", s.shortfall ? "border-debit/35 bg-debit-wash" : "border-rule bg-surface")}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="safe-h" className="flex items-center text-[15px] font-semibold">
          Safe to spend
          <InfoTip label="How is this worked out?">Your bank, cash and wallet balances minus the bills, EMIs, SIPs and card bills due before {s.until_label ? `your next ${s.until_label} payment` : until}, spread over the days until then. Nothing is invented: every bill it reserves is listed below.</InfoTip>
        </h2>
        <span className="text-xs text-ink-faint">until {until}{s.until_label ? ` (${s.until_label})` : ""}, {s.days_left} {s.days_left === 1 ? "day" : "days"}</span>
      </div>
      {s.shortfall ? (
        <p className="mt-2 text-sm text-debit">Bills due before {until} are <span className="num font-semibold">{formatMoney(s.obligations_total, s.currency)}</span>, more than the <span className="num">{formatMoney(s.liquid_balance, s.currency)}</span> in your accounts.</p>
      ) : (
        <div className="mt-2 flex flex-wrap items-end gap-x-6 gap-y-2">
          <p><span className="display text-[2.3rem] font-medium leading-none"><Amount value={s.per_day} currency={s.currency} colored={false} signed={false} /></span><span className="ml-1.5 text-sm text-ink-soft">a day</span></p>
          <p className="pb-1 text-sm text-ink-soft"><span className="num font-medium text-ink">{formatMoney(s.safe_total, s.currency)}</span> in total after <span className="num">{formatMoney(s.obligations_total, s.currency)}</span> of bills</p>
        </div>
      )}
      <details className="mt-3 text-sm">
        <summary className="flex items-center gap-1.5 text-ink-soft"><svg aria-hidden viewBox="0 0 20 20" className="chev size-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M8 5l5 5-5 5" /></svg>How this is worked out</summary>
        <div className="mt-2 flex flex-col gap-1 pl-5">
          <div className="flex justify-between gap-3"><span>Bank, cash and wallets</span><Amount value={s.liquid_balance} currency={s.currency} colored={false} signed={false} /></div>
          {s.obligations.map((o, i) => (
            <div key={i} className="flex justify-between gap-3 text-ink-soft"><span className="truncate">{formatDate(o.date, false)} · {o.label}</span><Amount value={negate(o.amount)} currency={s.currency} colored={false} /></div>
          ))}
          <div className="flex justify-between gap-3 border-t border-rule pt-1 font-medium"><span>Left to spend</span><Amount value={s.safe_total} currency={s.currency} colored={false} signed={false} /></div>
          {s.assumptions.length ? <ul className="mt-1 list-disc pl-4 text-xs text-ink-faint">{s.assumptions.map((a) => <li key={a}>{a}</li>)}</ul> : null}
        </div>
      </details>
    </section>
  );
}

function Bills({ bills }: { bills: Bill[] }) {
  return (
    <Panel title="Coming up" action={<Link href="/recurring" className="underline-offset-4 hover:underline">All bills</Link>}>
      {bills.length ? (
        <ul className="-my-1 flex flex-col">
          {bills.slice(0, 6).map((b) => (
            <li key={`${b.kind}:${b.id}:${b.date}`} className="border-b border-rule last:border-0">
              <Link href={b.href} className="-mx-2 flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-sunken/60">
                <span className={cx("flex w-11 shrink-0 flex-col items-center rounded-lg border py-0.5 text-center leading-tight", b.overdue ? "border-debit/40 text-debit" : "border-rule text-ink-soft")}>
                  <span className="text-[10px] uppercase tracking-wide">{new Date(`${b.date}T00:00:00Z`).toLocaleDateString("en-IN", { month: "short", timeZone: "UTC" })}</span>
                  <span className="num text-base font-semibold">{Number(b.date.slice(8))}</span>
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{b.label}</span>
                  <span className="block truncate text-xs text-ink-faint">{b.detail}</span>
                </span>
                <span className="text-right">
                  <Amount value={negate(b.amount)} currency={b.currency} colored={false} className="text-sm font-medium" />
                  {b.estimated ? <span className="block text-[11px] text-ink-faint">about</span> : null}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      ) : <p className="text-sm text-ink-soft">No bills, EMIs or card payments expected in the next three weeks.</p>}
    </Panel>
  );
}

function Budgets({ budgets, month }: { budgets: Budget[]; month: string }) {
  if (!budgets.length) {
    return (
      <Panel title="Budgets" action={<Link href="/budgets" className="underline-offset-4 hover:underline">Set up</Link>}>
        <p className="text-sm text-ink-soft">Set a monthly amount for the categories you want to watch. OneLedger can suggest amounts from your last three months.</p>
      </Panel>
    );
  }
  const sorted = [...budgets].sort((a, b) => Number(b.percent) - Number(a.percent)).slice(0, 5);
  return (
    <Panel title="Budgets" action={<Link href={`/budgets?month=${month}`} className="underline-offset-4 hover:underline">All budgets</Link>}>
      <ul className="flex flex-col gap-3">
        {sorted.map((b) => (
          <li key={b.id}>
            <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
              <span className="truncate font-medium">{b.name}</span>
              <span className={cx("num shrink-0 text-xs", b.over ? "font-semibold text-debit" : "text-ink-soft")}>{formatMoney(b.spent, b.currency, { decimals: false })} of {formatMoney(b.available, b.currency, { decimals: false })}</span>
            </div>
            <Meter value={Number(b.spent)} max={Number(b.available)} tone={b.over ? "debit" : Number(b.percent) >= 90 ? "review" : "credit"} label={`${b.name}: ${b.percent}% used`} />
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function HomeInner() {
  const params = useSearchParams();
  const month = params.get("month");
  const { data, error, isLoading, mutate } = useApi<Dashboard>(`/analytics/dashboard${month ? `?month=${month}` : ""}`);
  const { data: ai } = useApi<{ auto_categorize_error: { message: string } | null }>("/ai/settings");
  const add = useQuickAdd();

  if (isLoading && !data) return <Loading />;
  if (error || !data) return <ErrorNote error={error} onRetry={() => mutate()} />;

  const s = data.summary.data;
  const p = data.previous_summary.data;
  const range = `start_date=${data.period.start}&end_date_exclusive=${data.period.end_exclusive}`;
  const categories = data.categories.data.categories.slice(0, 8).map((c) => ({ key: c.category_id ?? "none", code: c.code, label: c.name, amount: c.amount, share: c.share, href: `/transactions?category_id=${c.category_id ?? ""}&${range}` }));
  if (Number(s.unclassified_outflow) > 0) {
    categories.push({ key: "uncategorised", code: null, label: "Uncategorised", amount: s.unclassified_outflow, share: "", href: `/transactions?uncategorized=1&${range}` });
  }
  const accounts = data.balances.data.accounts;
  const nw = data.net_worth.data.totals.INR;
  const reviewTotal = Object.values(data.review_counts).reduce((a, b) => a + b, 0);
  const changes = Object.entries(data.net_worth.data.changes).filter(([, c]) => c.available && c.delta.INR);
  const currentMonth = todayISO().slice(0, 7);
  // Today's cards (safe to spend, bills) belong to the default view, even when it falls back to the
  // latest month with data; they are hidden only when you pick another month yourself.
  const viewingPast = !!month && month !== currentMonth;

  async function dismiss(key: string) {
    await api("/insights/alerts/dismiss", { method: "POST", json: { key } });
    await mutate();
  }

  if (accounts.length === 0) {
    return (
      <Empty title="Start by adding an account" action={<ButtonLink href="/accounts?new=1" variant="primary">Add an account</ButtonLink>}>
        Add a bank account or card with its current balance, then import a statement. Nothing here is filled in with sample data.
      </Empty>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {ai?.auto_categorize_error ? (
        <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-debit/30 bg-debit-wash px-4 py-2.5 text-sm">
          <span className="font-medium text-debit">AI categorisation stopped.</span>
          <span className="min-w-0 flex-1 text-ink">{ai.auto_categorize_error.message}</span>
          <Link href="/settings#ai" className="font-medium text-debit underline-offset-4 hover:underline">Fix in Settings</Link>
        </div>
      ) : null}
      {reviewTotal ? (
        <Link href="/review" className="group flex items-center gap-3 rounded-xl border border-review/30 bg-review-wash px-4 py-2.5 text-sm text-review">
          <span className="num inline-flex h-6 min-w-6 items-center justify-center rounded-full bg-review px-1.5 text-xs font-semibold text-surface">{reviewTotal}</span>
          <span className="flex-1">{reviewTotal === 1 ? "item needs" : "items need"} your review. Totals may change until you confirm them.</span>
          <span className="font-medium underline-offset-4 group-hover:underline">Review</span>
        </Link>
      ) : null}

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-6">
          <section aria-labelledby="month-h" className="relative overflow-hidden rounded-2xl border border-rule bg-surface shadow-sheet">
            <div aria-hidden className="h-1 bg-[repeating-linear-gradient(90deg,var(--credit)_0_18px,transparent_18px_24px)] opacity-45" />
            <div className="px-4 pb-5 pt-3 sm:px-7 sm:pb-6 sm:pt-4">
              <div className="flex items-center justify-between gap-2">
                <MonthSwitch start={data.period.start} dataRange={data.data_range} current={currentMonth} />
                <div className="flex items-center gap-1">
                  {month ? <Link href="/" className="rounded-lg px-2.5 py-1.5 text-sm text-ink-soft hover:bg-sunken hover:text-ink">Latest</Link> : null}
                  <a href="/api/bff/export/transactions.csv" download aria-label="Download all transactions as CSV" title="Download all transactions (CSV)"
                    className="inline-flex size-9 items-center justify-center rounded-lg text-ink-soft hover:bg-sunken hover:text-ink">
                    <Icon name="download" className="size-5" />
                  </a>
                </div>
              </div>
              {data.period.note ? <p className="mt-0.5 pl-10 text-xs text-ink-faint">{data.period.note}</p> : null}
              <div className="mt-4 grid grid-cols-1 divide-y divide-rule border-t border-rule sm:mt-5 sm:grid-cols-2 sm:divide-x sm:divide-y-0 sm:border-t-0 [&>*:last-child]:sm:pl-7">
                <Figure label="Money in" tone="credit" value={moneyIn(s)} cur={moneyIn(s)} prev={moneyIn(p)}
                  info={<>Income you received this month, such as salary, interest and dividends, plus money received that isn&apos;t categorised yet. Money moved in from your own accounts, loans you take, repayments from friends and things you sell are not counted. Refunds lower Spent instead.</>} />
                <Figure label="Spent" tone="debit" value={spent(s)} cur={spent(s)} prev={spent(p)}
                  info={<>What you spent this month minus refunds, plus money that went out and isn&apos;t categorised yet. Transfers between your own accounts, cash moved into your wallet, credit-card bill payments (the card purchases are already counted), investments, friends&apos; shares of a bill and the principal part of loan EMIs are not counted.</>} />
              </div>
              <div className="border-t border-dashed border-rule pt-1 sm:mt-5">
                {s.unclassified_count > 0 ? (
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg bg-review-wash px-3.5 py-2.5">
                    <p className="text-sm text-ink">
                      <span className="num font-semibold">{s.unclassified_count}</span> of <span className="num">{data.summary.provenance.transaction_count}</span> transactions aren&apos;t categorised yet
                      <span className="block text-xs text-ink-soft">They&apos;re in the figures above, but not in Spending by category until you pick one.</span>
                    </p>
                    <ButtonLink href={`/transactions?uncategorized=1&${range}`} className="min-h-8 px-3 text-sm">Categorise</ButtonLink>
                  </div>
                ) : null}
                <Provenance p={data.summary.provenance} showWarnings hidePartial={s.unclassified_count > 0} hideWarning={(w) => /unclassified/i.test(w)} />
              </div>
            </div>
          </section>

          {!viewingPast ? <SafeToSpend s={data.safe_to_spend} /> : null}

          <Panel title="Spending by category" action={<Link href={`/stats?kind=month&anchor=${data.period.start}`} className="underline-offset-4 hover:underline">Stats</Link>}>
            {categories.length ? (
              <div className="grid items-center gap-6 sm:grid-cols-[200px_minmax(0,1fr)]">
                <div className="mx-auto w-full max-w-[200px]"><Donut slices={categories} total={spent(s)} caption="Spent" /></div>
                <ShareBars items={categories} hrefFor={(k) => categories.find((c) => c.key === k)!.href} />
              </div>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm text-ink-soft">No spending recorded in {formatMonth(data.period.start)} yet.</p>
                <Button size="sm" onClick={() => add({ kind: "expense" })}><Icon name="plus" className="size-4" />Add an expense</Button>
              </div>
            )}
          </Panel>
        </div>

        <div className="flex min-w-0 flex-col gap-6">
          {data.alerts.length ? (
            <Panel title={<span className="flex items-center gap-2"><Icon name="bell" className="size-4 text-ink-soft" />Needs attention</span>}>
              <div className="-my-2"><AlertList alerts={data.alerts.slice(0, 4)} onDismiss={dismiss} compact /></div>
            </Panel>
          ) : null}
          {!viewingPast ? <Bills bills={data.bills} /> : null}
          <Budgets budgets={data.budgets} month={data.period.start.slice(0, 7)} />

          <Panel title="Net worth" action={<Link href="/net-worth" className="underline-offset-4 hover:underline">Details</Link>}>
            {nw ? (
              <>
                <p className="display text-[2.1rem] font-medium leading-none"><Amount value={nw.net_worth} colored={false} signed={false} /></p>
                <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                  <div className="rounded-lg bg-sunken/60 px-3 py-2"><dt className="text-xs text-ink-faint">You own</dt><dd className="num mt-0.5 font-medium"><Amount value={nw.assets} colored={false} signed={false} /></dd></div>
                  <div className="rounded-lg bg-sunken/60 px-3 py-2"><dt className="text-xs text-ink-faint">You owe</dt><dd className="num mt-0.5 font-medium"><Amount value={nw.liabilities} colored={false} signed={false} /></dd></div>
                </dl>
                {changes.length ? (
                  <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-soft">
                    {changes.map(([k, c]) => <li key={k}>{k}: <Amount value={c.delta.INR} /></li>)}
                  </ul>
                ) : null}
                {data.net_worth.provenance.partial ? <p className="mt-3 text-xs text-review">Incomplete: some balances are unknown, so this is not your full net worth.</p> : null}
              </>
            ) : (
              <p className="text-sm text-ink-soft">Add balances to see your net worth.</p>
            )}
          </Panel>

          <Panel title="Accounts" action={<Link href="/accounts" className="underline-offset-4 hover:underline">All accounts</Link>}>
            <ul className="-my-1 flex flex-col">
              {accounts.slice(0, 6).map((a) => (
                <li key={a.account_id} className="border-b border-rule last:border-0">
                  <Link href={`/accounts/${a.account_id}`} className="-mx-2 flex items-center justify-between gap-3 rounded-lg px-2 py-2.5 hover:bg-sunken/60">
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{a.name}</span>
                      <span className="text-xs text-ink-faint">{KIND_LABEL[a.kind]}{a.derived_through ? `, as of ${formatDate(a.derived_through, false)}` : ""}</span>
                    </span>
                    {a.balance === null ? <span className="shrink-0 text-sm text-review">Balance unknown</span>
                      : <Amount className="shrink-0" value={a.nature === "LIABILITY" ? negate(a.balance) : a.balance} currency={a.currency} colored={a.nature === "LIABILITY"} signed={false} />}
                  </Link>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="Last six months">
            <MonthlyChart months={data.monthly.data.months} />
          </Panel>
        </div>
      </div>
    </div>
  );
}

export default function Home() {
  return <Suspense fallback={<Loading />}><HomeInner /></Suspense>;
}
