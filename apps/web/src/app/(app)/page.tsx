"use client";

import Link from "next/link";
import { MonthlyChart, ShareBars } from "@/components/charts";
import { InfoTip } from "@/components/info-tip";
import { Icon } from "@/components/icons";
import { Amount, ButtonLink, Empty, ErrorNote, Loading, Panel, Provenance } from "@/components/ui";
import { useApi } from "@/lib/api";
import { moneyIn, negate, spent } from "@/lib/money";
import { formatMonth, KIND_LABEL, formatDate } from "@/lib/format";

type Prov = { query_id: string; start_date: string | null; end_date_exclusive: string | null; transaction_count: number; partial: boolean; warnings: string[]; evidence_url: string };
type Summary = { data: Record<string, string> & { income: string; net_expenses: string; unclassified_inflow: string; unclassified_outflow: string; unclassified_count: number; savings_provisional: boolean }; provenance: Prov };
type Dashboard = {
  period: { start: string; end_exclusive: string; note: string | null };
  summary: Summary;
  previous_summary: Summary;
  categories: { data: { total: string; categories: { category_id: string | null; name: string; amount: string; share: string }[] }; provenance: Prov };
  monthly: { data: { months: { month: string; income: string; net_expenses: string; savings: string }[] } };
  balances: { data: { accounts: { account_id: string; name: string; kind: string; nature: string; balance: string | null; derived_through: string | null; status: string; currency: string }[]; unknown_count: number }; provenance: Prov };
  net_worth: { data: { totals: Record<string, { net_worth: string; assets: string; liabilities: string }>; changes: Record<string, { available: boolean; delta: Record<string, string> }> }; provenance: Prov };
  recurring: { id: string; label: string; typical_amount: string; currency: string; expected_next_date: string; cadence: string; overdue: boolean; state: string }[];
  review_counts: Record<string, number>;
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

export default function Home() {
  const { data, error, isLoading, mutate } = useApi<Dashboard>("/analytics/dashboard");
  const { data: ai } = useApi<{ auto_categorize_error: { message: string } | null }>("/ai/settings");

  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorNote error={error} onRetry={() => mutate()} />;

  const s = data.summary.data;
  const p = data.previous_summary.data;
  const range = `start_date=${data.period.start}&end_date_exclusive=${data.period.end_exclusive}`;
  const categories = data.categories.data.categories.slice(0, 8).map((c) => ({ key: c.category_id ?? "none", label: c.name, amount: c.amount, share: c.share, href: `/transactions?category_id=${c.category_id ?? ""}&${range}` }));
  if (Number(s.unclassified_outflow) > 0) {
    categories.push({ key: "uncategorised", label: "Uncategorised", amount: s.unclassified_outflow, share: "", href: `/transactions?uncategorized=1&${range}` });
  }
  const accounts = data.balances.data.accounts;
  const nw = data.net_worth.data.totals.INR;
  const reviewTotal = Object.values(data.review_counts).reduce((a, b) => a + b, 0);
  const changes = Object.entries(data.net_worth.data.changes).filter(([, c]) => c.available && c.delta.INR);

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
            <div className="px-5 pb-5 pt-4 sm:px-7 sm:pb-6 sm:pt-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h1 id="month-h" className="display text-[1.7rem] font-medium leading-tight">{formatMonth(data.period.start)}</h1>
                  {data.period.note ? <p className="mt-0.5 text-xs text-ink-faint">{data.period.note}</p> : null}
                </div>
                <a href="/api/bff/export/transactions.csv" download aria-label="Download all transactions as CSV" title="Download all transactions as CSV"
                  className="-mr-2 inline-flex size-9 items-center justify-center rounded-lg text-ink-soft hover:bg-sunken hover:text-ink">
                  <Icon name="download" className="size-5" />
                </a>
              </div>
              <div className="mt-4 grid grid-cols-1 divide-y divide-rule border-t border-rule sm:mt-6 sm:grid-cols-2 sm:divide-x sm:divide-y-0 sm:border-t-0 [&>*:last-child]:sm:pl-7">
                <Figure label="Money in" tone="credit" value={moneyIn(s)} cur={moneyIn(s)} prev={moneyIn(p)}
                  info={<>Income you received this month, such as salary, interest and dividends, plus money received that isn&apos;t categorised yet. Money moved in from your own accounts, loans you take and things you sell are not counted. Refunds lower Spent instead.</>} />
                <Figure label="Spent" tone="debit" value={spent(s)} cur={spent(s)} prev={spent(p)}
                  info={<>What you spent this month minus refunds, plus money that went out and isn&apos;t categorised yet. Transfers between your own accounts, credit-card bill payments (the card purchases are already counted), investments and the principal part of loan EMIs are not counted.</>} />
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

          <Panel title="Spending by category" action={<Link href={`/transactions?${range}`} className="underline-offset-4 hover:underline">Transactions</Link>}>
            {categories.length ? (
              <ShareBars items={categories} hrefFor={(k) => categories.find((c) => c.key === k)!.href} />
            ) : <p className="text-sm text-ink-soft">No spending recorded this month yet.</p>}
          </Panel>
        </div>

        <div className="flex min-w-0 flex-col gap-6">
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
