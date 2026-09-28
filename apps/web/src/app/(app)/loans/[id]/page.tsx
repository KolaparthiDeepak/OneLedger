"use client";

import { use, useState, type FormEvent } from "react";
import type { Loan } from "@/lib/types";
import type { Txn } from "@/components/transactions";
import { Amount, Button, ErrorNote, Field, Input, Loading, PageHeader, Panel, Select } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { formatDate, formatMoney } from "@/lib/format";

type Detail = Loan & { payments: { id: string; date: string; principal: string; interest: string; fees: string; prepayment: string; actual: boolean }[]; rate_history: { effective_date: string; annual_rate_percent: string }[] };
type Sched = { rows: { period: number; due_date: string; emi: string; interest: string; principal: string; closing: string }[]; assumptions: string[]; total_interest: string };
type Sim = { interest_saved: string; months_saved: number; scenario_emi: string; baseline_emi: string; scenario_payments: number; assumptions: string[] };

export default function LoanDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: l, error, mutate } = useApi<Detail>(`/loans/${id}`);
  const { data: sched } = useApi<Sched>(l?.outstanding_principal ? `/loans/${id}/schedule` : null);
  const { data: debits } = useApi<{ items: Txn[] }>(`/transactions?direction=debit&limit=40&q=${encodeURIComponent(l?.lender.split(" ")[0] ?? "")}`);
  const [pay, setPay] = useState({ transaction_id: "", principal: "", interest: "", prepayment: false });
  const [sim, setSim] = useState({ amount: "", payment_date: "", strategy: "REDUCE_TENURE" });
  const [simResult, setSimResult] = useState<Sim | null>(null);
  const [rate, setRate] = useState({ annual_rate_percent: "", effective_date: "" });
  const [err, setErr] = useState<unknown>(null);
  if (error) return <ErrorNote error={error} />;
  if (!l) return <Loading />;

  async function recordPayment(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      await api(`/loans/${id}/payments`, { method: "POST", json: { transaction_id: pay.transaction_id, principal: pay.principal || null, interest: pay.interest || null, prepayment: pay.prepayment } });
      setPay({ transaction_id: "", principal: "", interest: "", prepayment: false });
      mutate();
    } catch (e2) { setErr(e2); }
  }

  return (
    <>
      <PageHeader title={`${l.lender} ${l.loan_type}`} description={`${l.interest_rate_percent}% a year, EMI ${formatMoney(l.emi_amount, l.currency)}, over ${l.tenure_months} months`} />
      {err ? <div className="mb-4"><ErrorNote error={err} /></div> : null}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel title="Record an EMI or prepayment">
          <form onSubmit={recordPayment} className="flex flex-col gap-3">
            <Field label="Bank debit that paid it">{(fid) => (
              <Select id={fid} required value={pay.transaction_id} onChange={(e) => setPay({ ...pay, transaction_id: e.target.value })}>
                <option value="">Choose a transaction…</option>
                {debits?.items.map((t) => <option key={t.id} value={t.id}>{formatDate(t.transaction_date)} {t.description} ({t.amount})</option>)}
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
            <div className="mt-3 text-sm">
              <p>Interest saved: <Amount value={simResult.interest_saved} colored={false} signed={false} className="font-semibold" />{simResult.months_saved ? `, ${simResult.months_saved} fewer EMIs` : `, EMI becomes ${simResult.scenario_emi}`}.</p>
              <p className="text-xs text-ink-faint">A scenario only; your recorded balance is unchanged. {simResult.assumptions.join(" ")}</p>
            </div>
          ) : null}
        </Panel>
        <Panel title="Payments recorded">
          {l.payments.length === 0 ? <p className="text-sm text-ink-soft">None yet.</p> : (
            <table className="w-full text-sm">
              <thead className="text-left text-ink-faint"><tr><th className="font-normal">Date</th><th className="text-right font-normal">Principal</th><th className="text-right font-normal">Interest</th><th className="font-normal pl-2">Source</th></tr></thead>
              <tbody>{l.payments.map((p) => (
                <tr key={p.id} className="border-t border-rule"><td className="py-2">{formatDate(p.date)}</td><td className="num text-right">{p.prepayment !== "0.00" && p.prepayment !== "0" ? `${formatMoney(p.prepayment)} (prepaid)` : formatMoney(p.principal)}</td><td className="num text-right">{formatMoney(p.interest)}</td><td className="pl-2 text-ink-faint">{p.actual ? "Lender" : "Estimate"}</td></tr>
              ))}</tbody>
            </table>
          )}
        </Panel>
        <Panel title="Interest rate changes">
          <ul className="text-sm">{l.rate_history.map((r) => <li key={r.effective_date} className="flex justify-between border-b border-rule py-1"><span>From {formatDate(r.effective_date)}</span><span className="num">{r.annual_rate_percent}%</span></li>)}</ul>
          <form className="mt-3 grid grid-cols-[1fr_1fr_auto] items-end gap-2" onSubmit={async (e) => { e.preventDefault(); await api(`/loans/${id}/rate-changes`, { method: "POST", json: rate }).catch(setErr); mutate(); }}>
            <Field label="New rate %">{(fid) => <Input id={fid} required inputMode="decimal" value={rate.annual_rate_percent} onChange={(e) => setRate({ ...rate, annual_rate_percent: e.target.value })} />}</Field>
            <Field label="From">{(fid) => <Input id={fid} required type="date" value={rate.effective_date} onChange={(e) => setRate({ ...rate, effective_date: e.target.value })} />}</Field>
            <Button type="submit">Add</Button>
          </form>
        </Panel>
      </div>
      {sched ? (
        <Panel title="Projected schedule" className="mt-6">
          <p className="mb-2 text-xs text-ink-faint">{sched.assumptions.join(" ")}</p>
          <div className="max-h-96 overflow-auto">
            <table className="w-full min-w-[520px] text-sm">
              <thead className="sticky top-0 bg-surface text-left text-ink-faint"><tr><th className="font-normal">#</th><th className="font-normal">Due</th><th className="text-right font-normal">EMI</th><th className="text-right font-normal">Interest</th><th className="text-right font-normal">Principal</th><th className="text-right font-normal">Left</th></tr></thead>
              <tbody>{sched.rows.map((r) => (
                <tr key={r.period} className="border-t border-rule"><td className="num py-2 text-ink-faint">{r.period}</td><td>{formatDate(r.due_date)}</td><td className="num text-right">{formatMoney(r.emi)}</td><td className="num text-right">{formatMoney(r.interest)}</td><td className="num text-right">{formatMoney(r.principal)}</td><td className="num text-right font-medium">{formatMoney(r.closing)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </Panel>
      ) : null}
    </>
  );
}
