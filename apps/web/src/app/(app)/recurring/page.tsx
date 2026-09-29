"use client";

import Link from "next/link";
import { useState } from "react";
import { ledgerChanged } from "@/components/quick-add";
import { Badge, Button, cx, Empty, ErrorNote, Input, Loading, PageHeader, Panel } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { formatDate, formatMoney } from "@/lib/format";

type Rec = { id: string; label: string; account_name: string; type: string; cadence: string; typical_amount: string; currency: string; amount_variable: boolean; occurrences: number; last_date: string; expected_next_date: string; state: string; overdue: boolean; monthly_equivalent: string; tracked_as_holding?: boolean };
type Forecast = { data: { available: boolean; message?: string; inflow?: string; outflow?: string; net?: string; starting_liquid_balance?: string | null; projected_liquid_balance?: string | null; items?: { label: string; date: string; amount: string }[] }; assumptions: string[] };
type Bill = { kind: string; id: string; label: string; date: string; amount: string; currency: string; estimated: boolean; overdue: boolean; confirmed?: boolean; href: string; detail: string };
const TYPE: Record<string, string> = { SALARY: "Salary", RENT: "Rent", EMI: "EMI", SUBSCRIPTION: "Subscription", INSURANCE: "Insurance", SIP: "SIP", TRANSFER: "Transfer", BILL: "Bill", OTHER: "Other" };

function weekLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`).getTime();
  const t = new Date(new Date().toISOString().slice(0, 10) + "T00:00:00Z").getTime();
  const days = Math.round((d - t) / 86_400_000);
  if (days < 7) return "This week";
  if (days < 14) return "Next week";
  return "Later";
}

function Row({ r, onDecide, onChanged }: { r: Rec; onDecide: (id: string, state: string, label?: string) => Promise<void>; onChanged: () => void }) {
  const [renaming, setRenaming] = useState(false);
  const [label, setLabel] = useState(r.label);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  return (
    <li className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 py-3.5">
      <div className="min-w-0">
        {renaming ? (
          <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); await onDecide(r.id, r.state, label); setRenaming(false); }}>
            <Input aria-label="Name" value={label} maxLength={160} onChange={(e) => setLabel(e.target.value)} className="min-h-8 w-56" />
            <Button size="sm" type="submit">Save</Button>
          </form>
        ) : (
          <p className="flex flex-wrap items-center gap-2 font-medium">{r.label}<Badge>{TYPE[r.type]}</Badge>{r.tracked_as_holding ? <Badge tone="credit">Tracked as a holding</Badge> : null}</p>
        )}
        <p className="mt-0.5 text-xs text-ink-faint">{r.cadence.charAt(0) + r.cadence.slice(1).toLowerCase()}, seen {r.occurrences} times, last on {formatDate(r.last_date)}, {r.account_name}</p>
        <p className={r.overdue ? "text-xs text-review" : "text-xs text-ink-faint"}>{r.overdue ? "Expected by" : "Next around"} {formatDate(r.expected_next_date)}{r.amount_variable ? ", amount varies" : ""}</p>
      </div>
      <div className="text-right">
        <p className={r.typical_amount.startsWith("-") ? "num font-medium" : "num font-medium text-credit"}>{formatMoney(r.typical_amount, r.currency, { signed: true })}</p>
        <p className="num text-xs text-ink-faint">{formatMoney(r.monthly_equivalent, r.currency)} a month</p>
      </div>
      <div className="flex w-full flex-wrap gap-1.5">
        {r.state === "SUGGESTED" ? <Button size="sm" onClick={() => onDecide(r.id, "ACCEPTED")}>Confirm</Button> : null}
        {r.state !== "DISMISSED" ? <Button size="sm" variant="ghost" onClick={() => onDecide(r.id, "DISMISSED")}>Not recurring</Button> : <Button size="sm" onClick={() => onDecide(r.id, "SUGGESTED")}>Restore</Button>}
        <Button size="sm" variant="ghost" onClick={() => setRenaming(!renaming)}>Rename</Button>
        {r.type === "SIP" && !r.tracked_as_holding && r.typical_amount.startsWith("-") ? (
          <Button size="sm" variant="ghost" busy={busy} onClick={async () => {
            setBusy(true);
            setErr(null);
            try { await api("/investments/from-recurring", { method: "POST", json: { recurring_id: r.id } }); ledgerChanged(); onChanged(); } catch (x) { setErr(x); } finally { setBusy(false); }
          }}>Track as a holding</Button>
        ) : null}
      </div>
      {err ? <div className="w-full"><ErrorNote error={err} /></div> : null}
    </li>
  );
}

export default function RecurringPage() {
  const [showDismissed, setShowDismissed] = useState(false);
  const { data, error, mutate } = useApi<Rec[]>(`/recurring${showDismissed ? "?include_dismissed=true" : ""}`);
  const { data: fc, mutate: mutFc } = useApi<Forecast>("/forecast?days=30");
  const { data: bills, mutate: mutBills } = useApi<Bill[]>("/insights/upcoming?days=45");
  const [busyAll, setBusyAll] = useState(false);
  if (error) return <ErrorNote error={error} />;
  if (!data) return <Loading />;
  const refresh = () => { mutate(); mutFc(); mutBills(); };
  const decide = async (id: string, state: string, label?: string) => { await api(`/recurring/${id}`, { method: "POST", json: { state, label } }); refresh(); };
  const suggested = data.filter((r) => r.state === "SUGGESTED");
  const accepted = data.filter((r) => r.state === "ACCEPTED");
  const dismissed = data.filter((r) => r.state === "DISMISSED");
  const groups = (bills ?? []).reduce<Record<string, Bill[]>>((acc, b) => { (acc[weekLabel(b.date)] ??= []).push(b); return acc; }, {});
  return (
    <>
      <PageHeader title="Recurring & bills" description="Detected from at least three similar payments at a regular interval, plus card bills and loan EMIs. Missed payments are only flagged, never added." />
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[1.3fr_1fr]">
        <div className="flex flex-col gap-6">
          {data.length === 0 ? <Empty title="Nothing recurring found yet">Import at least three months of statements.</Empty> : null}
          {suggested.length ? (
            <Panel title={`Detected (${suggested.length})`} action={
              <Button size="sm" busy={busyAll} onClick={async () => { setBusyAll(true); for (const r of suggested) await api(`/recurring/${r.id}`, { method: "POST", json: { state: "ACCEPTED" } }); setBusyAll(false); refresh(); }}>Confirm all</Button>
            }>
              <p className="-mt-2 mb-1 text-sm text-ink-soft">Confirm the ones that are real, so forecasts and alerts can use them.</p>
              <ul className="divide-y divide-rule">{suggested.map((r) => <Row key={r.id} r={r} onDecide={decide} onChanged={refresh} />)}</ul>
            </Panel>
          ) : null}
          {accepted.length ? <Panel title={`Confirmed (${accepted.length})`}><ul className="divide-y divide-rule">{accepted.map((r) => <Row key={r.id} r={r} onDecide={decide} onChanged={refresh} />)}</ul></Panel> : null}
          {showDismissed && dismissed.length ? <Panel title={`Not recurring (${dismissed.length})`}><ul className="divide-y divide-rule">{dismissed.map((r) => <Row key={r.id} r={r} onDecide={decide} onChanged={refresh} />)}</ul></Panel> : null}
          <button className="self-start text-sm text-ink-soft underline underline-offset-2 hover:text-ink" onClick={() => setShowDismissed(!showDismissed)}>{showDismissed ? "Hide the ones marked not recurring" : "Show the ones marked not recurring"}</button>
        </div>
        <div className="flex flex-col gap-6 lg:sticky lg:top-6">
          <Panel title="Bills coming up">
            {!bills ? <Loading /> : bills.length === 0 ? <p className="text-sm text-ink-soft">Nothing expected in the next 45 days.</p> : (
              <div className="flex flex-col gap-4">
                {Object.entries(groups).map(([g, items]) => (
                  <section key={g}>
                    <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">{g}</h3>
                    <ul className="text-sm">
                      {items.map((b) => (
                        <li key={`${b.kind}:${b.id}:${b.date}`} className="flex items-center justify-between gap-3 border-t border-rule py-2">
                          <Link href={b.href} className="min-w-0 hover:underline">
                            <span className="block truncate">{formatDate(b.date, false)} · {b.label}</span>
                            <span className={cx("block text-xs", b.overdue ? "text-debit" : "text-ink-faint")}>{b.detail}</span>
                          </Link>
                          <span className="num shrink-0">{b.estimated ? "≈ " : ""}{formatMoney(b.amount, b.currency)}</span>
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            )}
          </Panel>
          <Panel title="Next 30 days">
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
      </div>
    </>
  );
}
