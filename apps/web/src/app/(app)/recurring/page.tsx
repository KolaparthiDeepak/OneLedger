"use client";

import { Badge, Button, Empty, ErrorNote, Loading, PageHeader, Panel } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { formatDate, formatMoney } from "@/lib/format";

type Rec = { id: string; label: string; account_name: string; type: string; cadence: string; typical_amount: string; currency: string; amount_variable: boolean; occurrences: number; last_date: string; expected_next_date: string; state: string; overdue: boolean; monthly_equivalent: string };
type Forecast = { data: { available: boolean; message?: string; inflow?: string; outflow?: string; net?: string; starting_liquid_balance?: string | null; projected_liquid_balance?: string | null; items?: { label: string; date: string; amount: string }[] }; assumptions: string[] };
const TYPE: Record<string, string> = { SALARY: "Salary", RENT: "Rent", EMI: "EMI", SUBSCRIPTION: "Subscription", INSURANCE: "Insurance", SIP: "SIP", TRANSFER: "Transfer", BILL: "Bill", OTHER: "Other" };

export default function RecurringPage() {
  const { data, error, mutate } = useApi<Rec[]>("/recurring");
  const { data: fc, mutate: mutFc } = useApi<Forecast>("/forecast?days=30");
  if (error) return <ErrorNote error={error} />;
  if (!data) return <Loading />;
  const decide = async (id: string, state: string) => { await api(`/recurring/${id}`, { method: "POST", json: { state } }); mutate(); mutFc(); };
  const suggested = data.filter((r) => r.state === "SUGGESTED");
  const accepted = data.filter((r) => r.state === "ACCEPTED");
  const Row = ({ r }: { r: Rec }) => (
    <li className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 py-3.5">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 font-medium">{r.label}<Badge>{TYPE[r.type]}</Badge></p>
        <p className="mt-0.5 text-xs text-ink-faint">{r.cadence.charAt(0) + r.cadence.slice(1).toLowerCase()}, seen {r.occurrences} times, last on {formatDate(r.last_date)}, {r.account_name}</p>
        <p className={r.overdue ? "text-xs text-review" : "text-xs text-ink-faint"}>{r.overdue ? "Expected by" : "Next around"} {formatDate(r.expected_next_date)}{r.amount_variable ? ", amount varies" : ""}</p>
      </div>
      <div className="text-right">
        <p className={r.typical_amount.startsWith("-") ? "num font-medium" : "num font-medium text-credit"}>{formatMoney(r.typical_amount, r.currency, { signed: true })}</p>
        <p className="num text-xs text-ink-faint">{formatMoney(r.monthly_equivalent, r.currency)} a month</p>
      </div>
      {r.state === "SUGGESTED" ? (
        <div className="flex w-full gap-1.5"><Button size="sm" onClick={() => decide(r.id, "ACCEPTED")}>Confirm</Button><Button size="sm" variant="ghost" onClick={() => decide(r.id, "DISMISSED")}>Not recurring</Button></div>
      ) : null}
    </li>
  );
  return (
    <>
      <PageHeader title="Recurring" description="Detected from at least three similar payments at a regular interval. Missed payments are only flagged, never added." />
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[1.3fr_1fr]">
        <div className="flex flex-col gap-6">
          {data.length === 0 ? <Empty title="Nothing recurring found yet">Import at least three months of statements.</Empty> : null}
          {suggested.length ? <Panel title={`Detected (${suggested.length})`}><ul className="divide-y divide-rule">{suggested.map((r) => <Row key={r.id} r={r} />)}</ul></Panel> : null}
          {accepted.length ? <Panel title={`Confirmed (${accepted.length})`}><ul className="divide-y divide-rule">{accepted.map((r) => <Row key={r.id} r={r} />)}</ul></Panel> : null}
        </div>
        <Panel title="Next 30 days" className="lg:sticky lg:top-6">
          {!fc ? <Loading /> : !fc.data.available ? <p className="text-sm text-ink-soft">{fc.data.message}</p> : (
            <>
              <dl className="grid grid-cols-2 gap-3 text-sm">
                <div><dt className="text-xs text-ink-faint">Expected in</dt><dd className="display text-2xl text-credit">{formatMoney(fc.data.inflow)}</dd></div>
                <div><dt className="text-xs text-ink-faint">Expected out</dt><dd className="display text-2xl">{formatMoney(fc.data.outflow)}</dd></div>
                {fc.data.projected_liquid_balance ? <div className="col-span-2"><dt className="text-xs text-ink-faint">Bank and cash balance after these</dt><dd className="display text-2xl">{formatMoney(fc.data.projected_liquid_balance)}</dd></div> : null}
              </dl>
              <ul className="mt-3 text-sm">{fc.data.items?.map((i, n) => <li key={n} className="flex justify-between gap-3 border-t border-rule py-2"><span>{formatDate(i.date, false)} {i.label}</span><span className="num">{formatMoney(i.amount, "INR", { signed: true })}</span></li>)}</ul>
            </>
          )}
          {fc ? <ul className="mt-4 list-disc border-t border-dashed border-rule pl-4 pt-3 text-xs leading-relaxed text-ink-faint">{fc.assumptions.map((a) => <li key={a}>{a}</li>)}</ul> : null}
        </Panel>
      </div>
    </>
  );
}
