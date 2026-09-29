"use client";

import { use, useMemo, useState, type FormEvent } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Loan } from "@/lib/types";
import type { Txn } from "@/components/transactions";
import { ledgerChanged } from "@/components/quick-add";
import { Amount, Button, cx, ErrorNote, Field, Input, LedgerLine, Loading, Meter, PageHeader, Panel, Select } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { compactINR, formatDate, formatMoney } from "@/lib/format";

type Payment = { id: string; date: string; principal: string; interest: string; fees: string; prepayment: string; actual: boolean; source: string; transaction_id: string | null };
type Detail = Loan & { payments: Payment[]; rate_history: { effective_date: string; annual_rate_percent: string }[] };
type Sched = { rows: { period: number; due_date: string; emi: string; interest: string; principal: string; closing: string }[]; assumptions: string[]; total_interest: string };
type Sim = { interest_saved: string; months_saved: number; scenario_emi: string; baseline_emi: string; scenario_payments: number; assumptions: string[] };

const SOURCE: Record<string, string> = { USER: "Lender figures", ESTIMATE: "Estimated", AUTO_ESTIMATE: "Matched automatically, estimated" };

function YearlySplit({ rows }: { rows: Sched["rows"] }) {
  const data = useMemo(() => {
    const by: Record<string, { year: string; interest: number; principal: number }> = {};
    for (const r of rows) {
      const y = r.due_date.slice(0, 4);
      by[y] ??= { year: y, interest: 0, principal: 0 };
      by[y].interest += Number(r.interest);
      by[y].principal += Number(r.principal);
    }
    return Object.values(by);
  }, [rows]);
  return (
    <div className="h-48" aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ left: -6, right: 0, top: 4, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--rule)" strokeDasharray="2 4" />
          <XAxis dataKey="year" tickLine={false} axisLine={false} tick={{ fill: "var(--ink-faint)", fontSize: 11 }} interval="preserveStartEnd" minTickGap={16} />
          <YAxis tickFormatter={compactINR} tickLine={false} axisLine={false} width={52} tick={{ fill: "var(--ink-faint)", fontSize: 11 }} />
          <Tooltip cursor={{ fill: "var(--sunken)" }} contentStyle={{ background: "var(--surface)", border: "1px solid var(--rule)", borderRadius: 10, color: "var(--ink)", fontSize: 13 }}
            formatter={(v, n) => [formatMoney(Number(v).toFixed(2)), n === "interest" ? "Interest" : "Principal"]} />
          <Bar dataKey="principal" stackId="a" fill="var(--credit)" isAnimationActive={false} />
          <Bar dataKey="interest" stackId="a" fill="var(--debit)" fillOpacity={0.8} radius={[3, 3, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function LenderFigures({ loanId, p, onDone }: { loanId: string; p: Payment; onDone: () => void }) {
  const [principal, setPrincipal] = useState("");
  const [interest, setInterest] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <form className="mt-2 grid grid-cols-[1fr_1fr_auto] items-end gap-2" onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true);
      setErr(null);
      try {
        await api(`/loans/${loanId}/payments`, { method: "POST", json: { transaction_id: p.transaction_id, principal, interest } });
        onDone();
      } catch (x) { setErr(x); } finally { setBusy(false); }
    }}>
      <Field label="Principal">{(fid) => <Input id={fid} required inputMode="decimal" value={principal} onChange={(e) => setPrincipal(e.target.value.replace(/[^0-9.]/g, ""))} />}</Field>
      <Field label="Interest">{(fid) => <Input id={fid} required inputMode="decimal" value={interest} onChange={(e) => setInterest(e.target.value.replace(/[^0-9.]/g, ""))} />}</Field>
      <Button type="submit" size="sm" className="min-h-10" busy={busy}>Save</Button>
      {err ? <div className="col-span-3"><ErrorNote error={err} /></div> : null}
    </form>
  );
}

export default function LoanDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: l, error, mutate } = useApi<Detail>(`/loans/${id}`);
  const { data: sched, mutate: mutateSched } = useApi<Sched>(l?.outstanding_principal ? `/loans/${id}/schedule` : null);
  const { data: debits } = useApi<{ items: Txn[] }>(l ? `/transactions?direction=debit&limit=60&min_amount=${Number(l.emi_amount) * 0.5}` : null);
  const [pay, setPay] = useState({ transaction_id: "", principal: "", interest: "", prepayment: false });
  const [sim, setSim] = useState({ amount: "", payment_date: "", strategy: "REDUCE_TENURE" });
  const [simResult, setSimResult] = useState<Sim | null>(null);
  const [rate, setRate] = useState({ annual_rate_percent: "", effective_date: "" });
  const [err, setErr] = useState<unknown>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [matchMsg, setMatchMsg] = useState<string | null>(null);
  if (error) return <ErrorNote error={error} />;
  if (!l) return <Loading />;

  const refresh = () => { mutate(); mutateSched(); ledgerChanged(); };
  const recorded = new Set(l.payments.map((p) => p.transaction_id));
  const candidates = (debits?.items ?? []).filter((t) => !recorded.has(t.id) && !t.is_split && t.account.kind !== "LOAN");
  const repaid = Number(l.original_principal) - Number(l.outstanding_principal ?? l.original_principal);

  async function recordPayment(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      await api(`/loans/${id}/payments`, { method: "POST", json: { transaction_id: pay.transaction_id, principal: pay.principal || null, interest: pay.interest || null, prepayment: pay.prepayment } });
      setPay({ transaction_id: "", principal: "", interest: "", prepayment: false });
      refresh();
    } catch (e2) { setErr(e2); }
  }

  async function findEmis() {
    setErr(null);
    try {
      const r = await api<{ recorded: number; suggested: number }>(`/loans/${id}/match-payments`, { method: "POST" });
      setMatchMsg(r.recorded || r.suggested ? `${r.recorded} EMI${r.recorded === 1 ? "" : "s"} recorded${r.suggested ? `, ${r.suggested} to confirm in Review` : ""}.` : "No new EMI payments found.");
      refresh();
    } catch (e2) { setErr(e2); }
  }

  return (
    <>
      <PageHeader title={`${l.lender} ${l.loan_type}`} description={`${l.interest_rate_percent}% a year, EMI ${formatMoney(l.emi_amount, l.currency)}, over ${l.tenure_months} months. Only the interest part of each EMI counts as spending.`}
        actions={<Button onClick={findEmis}>Look for EMI payments</Button>} />
      {matchMsg ? <p role="status" className="mb-4 rounded-lg bg-credit-wash px-3.5 py-2.5 text-sm text-credit">{matchMsg}</p> : null}
      {err ? <div className="mb-4"><ErrorNote error={err} /></div> : null}

      <div className="mb-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <Panel>
          <p className="text-sm text-ink-soft">Outstanding principal{l.outstanding_as_of ? `, as of ${formatDate(l.outstanding_as_of)}` : ""}</p>
          <p className="display mt-1 text-[2.3rem] font-medium leading-none"><Amount value={l.outstanding_principal} currency={l.currency} colored={false} signed={false} /></p>
          <div className="mt-4"><Meter value={repaid} max={Number(l.original_principal)} tone="credit" label="Share of the loan repaid" /></div>
          <p className="mt-1.5 text-xs text-ink-faint">{Math.round((repaid / Number(l.original_principal)) * 100)}% of {formatMoney(l.original_principal, l.currency)} repaid</p>
          <div className="mt-4 border-t border-rule pt-2 text-sm">
            {l.projection?.next_emi_date ? <LedgerLine label="Next EMI" value={l.emi_amount} currency={l.currency} sub={formatDate(l.projection.next_emi_date)} /> : null}
            {l.projection?.remaining_emis ? (
              <div className="py-2">
                <div className="flex items-baseline gap-3"><span className="text-ink-soft">EMIs left (estimate)</span><span className="leader" aria-hidden /><span className="num">{l.projection.remaining_emis}</span></div>
                <div className="mt-0.5 text-xs text-ink-faint">Last one around {formatDate(l.projection.projected_payoff_date)}</div>
              </div>
            ) : null}
            {l.projection?.projected_interest ? <LedgerLine label="Interest still to pay (estimate)" value={l.projection.projected_interest} currency={l.currency} /> : null}
            <LedgerLine label="Interest paid (recorded)" value={l.interest_paid} currency={l.currency} />
          </div>
        </Panel>
        <Panel title="Principal and interest by year">
          {sched?.rows.length ? <><YearlySplit rows={sched.rows} /><p className="mt-2 flex gap-4 text-xs text-ink-soft"><span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-full bg-credit" />Principal</span><span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-full bg-debit" />Interest</span></p></> : <p className="text-sm text-ink-soft">Needs the outstanding balance to project the schedule.</p>}
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel title="Payments recorded" action={<span>{l.payments.length}</span>}>
          {l.payments.length === 0 ? <p className="text-sm text-ink-soft">None yet. EMIs on your statements are matched automatically when the amount and date fit; use &ldquo;Look for EMI payments&rdquo; after changing the EMI.</p> : (
            <ul className="flex flex-col divide-y divide-rule text-sm">
              {l.payments.map((p) => {
                const prepaid = Number(p.prepayment) > 0;
                return (
                  <li key={p.id} className="py-2.5">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="font-medium">{formatDate(p.date)}</span>
                      <span className="num text-right">{prepaid ? <>{formatMoney(p.prepayment)} prepaid</> : <>{formatMoney(p.principal)} <span className="text-ink-faint">principal</span> · {formatMoney(p.interest)} <span className="text-ink-faint">interest</span></>}</span>
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                      <span className={cx(p.actual ? "text-credit" : "text-review")}>{SOURCE[p.source] ?? (p.actual ? "Lender figures" : "Estimated")}</span>
                      {!p.actual && p.transaction_id ? <button className="text-ink-soft underline underline-offset-2 hover:text-ink" onClick={() => setEditing(editing === p.id ? null : p.id)}>Enter lender figures</button> : null}
                      <button className="text-ink-soft underline underline-offset-2 hover:text-debit" onClick={async () => { await api(`/loans/${id}/payments/${p.id}`, { method: "DELETE" }).catch(setErr); refresh(); }}>Not this loan</button>
                    </div>
                    {editing === p.id ? <LenderFigures loanId={id} p={p} onDone={() => { setEditing(null); refresh(); }} /> : null}
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>
        <Panel title="Record a payment by hand">
          <form onSubmit={recordPayment} className="flex flex-col gap-3">
            <Field label="Bank debit that paid it">{(fid) => (
              <Select id={fid} required value={pay.transaction_id} onChange={(e) => setPay({ ...pay, transaction_id: e.target.value })}>
                <option value="">Choose a transaction…</option>
                {candidates.map((t) => <option key={t.id} value={t.id}>{formatDate(t.transaction_date)} · {t.merchant ?? t.description} ({formatMoney(t.amount.replace("-", ""))})</option>)}
              </Select>
            )}</Field>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={pay.prepayment} onChange={(e) => setPay({ ...pay, prepayment: e.target.checked })} />This was a prepayment (all principal)</label>
            {!pay.prepayment ? (
              <div className="grid grid-cols-2 gap-2">
                <Field label="Principal" hint="From your lender statement">{(fid, d) => <Input id={fid} aria-describedby={d} inputMode="decimal" value={pay.principal} onChange={(e) => setPay({ ...pay, principal: e.target.value })} />}</Field>
                <Field label="Interest">{(fid) => <Input id={fid} inputMode="decimal" value={pay.interest} onChange={(e) => setPay({ ...pay, interest: e.target.value })} />}</Field>
              </div>
            ) : null}
            <p className="text-xs text-ink-faint">Leave principal and interest blank to estimate them from the outstanding balance and rate. Estimates are labelled as such.</p>
            <Button type="submit" variant="primary" disabled={!pay.transaction_id}>Record payment</Button>
          </form>
        </Panel>
        <Panel title="What if I prepay?">
          <form className="grid grid-cols-2 gap-2" onSubmit={async (e) => { e.preventDefault(); setErr(null); try { setSimResult(await api<Sim>(`/loans/${id}/simulate-prepayment`, { method: "POST", json: sim })); } catch (e2) { setErr(e2); } }}>
            <Field label="Amount">{(fid) => <Input id={fid} required inputMode="decimal" value={sim.amount} onChange={(e) => setSim({ ...sim, amount: e.target.value })} />}</Field>
            <Field label="On">{(fid) => <Input id={fid} required type="date" value={sim.payment_date} onChange={(e) => setSim({ ...sim, payment_date: e.target.value })} />}</Field>
            <Field label="Then">{(fid) => (
              <Select id={fid} value={sim.strategy} onChange={(e) => setSim({ ...sim, strategy: e.target.value })}>
                <option value="REDUCE_TENURE">Keep EMI, finish sooner</option><option value="REDUCE_EMI">Keep tenure, lower EMI</option>
              </Select>
            )}</Field>
            <div className="flex items-end"><Button type="submit">Simulate</Button></div>
          </form>
          {simResult ? (
            <div className="mt-4 rounded-lg bg-credit-wash p-3 text-sm">
              <p className="text-credit">Saves <Amount value={simResult.interest_saved} colored={false} signed={false} className="font-semibold" /> in interest{simResult.months_saved ? <> and ends <span className="font-semibold">{simResult.months_saved} months</span> sooner</> : <>; EMI becomes <span className="num font-semibold">{formatMoney(simResult.scenario_emi)}</span> (was {formatMoney(simResult.baseline_emi)})</>}.</p>
              <p className="mt-1 text-xs text-ink-soft">A scenario only; your recorded balance is unchanged. {simResult.assumptions.join(" ")}</p>
            </div>
          ) : null}
        </Panel>
        <Panel title="Interest rate changes">
          <ul className="text-sm">{l.rate_history.map((r) => <li key={r.effective_date} className="flex justify-between border-b border-rule py-1"><span>From {formatDate(r.effective_date)}</span><span className="num">{r.annual_rate_percent}%</span></li>)}</ul>
          <form className="mt-3 grid grid-cols-[1fr_1fr_auto] items-end gap-2" onSubmit={async (e) => { e.preventDefault(); await api(`/loans/${id}/rate-changes`, { method: "POST", json: rate }).catch(setErr); refresh(); }}>
            <Field label="New rate %">{(fid) => <Input id={fid} required inputMode="decimal" value={rate.annual_rate_percent} onChange={(e) => setRate({ ...rate, annual_rate_percent: e.target.value })} />}</Field>
            <Field label="From">{(fid) => <Input id={fid} required type="date" value={rate.effective_date} onChange={(e) => setRate({ ...rate, effective_date: e.target.value })} />}</Field>
            <Button type="submit">Add</Button>
          </form>
        </Panel>
      </div>
      {sched ? (
        <Panel title="Projected schedule" className="mt-6" action={<span>{sched.rows.length} EMIs, {formatMoney(sched.total_interest, l.currency, { decimals: false })} interest</span>}>
          <p className="mb-2 text-xs text-ink-faint">{sched.assumptions.join(" ")}</p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-sm">
              <thead className="text-left text-ink-faint"><tr><th className="font-normal">#</th><th className="font-normal">Due</th><th className="text-right font-normal">EMI</th><th className="text-right font-normal">Interest</th><th className="text-right font-normal">Principal</th><th className="text-right font-normal">Left</th></tr></thead>
              <tbody>{(showAll ? sched.rows : sched.rows.slice(0, 12)).map((r) => (
                <tr key={r.period} className="border-t border-rule"><td className="num py-2 text-ink-faint">{r.period}</td><td>{formatDate(r.due_date)}</td><td className="num text-right">{formatMoney(r.emi)}</td><td className="num text-right">{formatMoney(r.interest)}</td><td className="num text-right">{formatMoney(r.principal)}</td><td className="num text-right font-medium">{formatMoney(r.closing)}</td></tr>
              ))}</tbody>
            </table>
          </div>
          {sched.rows.length > 12 ? <div className="mt-3 flex justify-center"><Button size="sm" variant="ghost" onClick={() => setShowAll(!showAll)}>{showAll ? "Show the next 12 only" : `Show all ${sched.rows.length} EMIs`}</Button></div> : null}
        </Panel>
      ) : null}
    </>
  );
}
