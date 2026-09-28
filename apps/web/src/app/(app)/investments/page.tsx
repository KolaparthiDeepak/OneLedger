"use client";

import { useState, type FormEvent } from "react";
import { Amount, Button, Empty, ErrorNote, Field, Input, Loading, PageHeader, Panel, Select, Sheet } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { formatDate, todayISO } from "@/lib/format";

type Holding = { id: string; name: string; instrument_type: string; currency: string; valuation_mode: string; units: string | null; net_contributions: string; value: string | null; valued_on: string | null; unrealized_gain: string | null; gain_note: string | null; income: string };
type Resp = { items: Holding[]; totals: Record<string, { value: string; net_contributions: string }>; unvalued_count: number; note: string };
const TYPES: Record<string, string> = { STOCK: "Stocks", MUTUAL_FUND: "Mutual fund", ETF: "ETF", PPF: "PPF", NPS: "NPS", FIXED_DEPOSIT: "Fixed deposit", GOLD: "Gold", BOND: "Bond", REAL_ESTATE: "Property", OTHER: "Other" };

export default function InvestmentsPage() {
  const { data, error, mutate } = useApi<Resp>("/investments");
  const [adding, setAdding] = useState(false);
  const [valueFor, setValueFor] = useState<Holding | null>(null);
  const [txnFor, setTxnFor] = useState<Holding | null>(null);
  if (error) return <ErrorNote error={error} />;
  if (!data) return <Loading />;
  const t = data.totals.INR;
  return (
    <>
      <PageHeader title="Investments" description={data.note} actions={<Button variant="primary" onClick={() => setAdding(true)}>Add holding</Button>} />
      {t ? (
        <Panel className="mb-6">
          <div className="flex flex-wrap gap-8">
            <div><p className="text-sm text-ink-soft">Latest value</p><Amount value={t.value} colored={false} signed={false} className="text-3xl font-semibold" /></div>
            <div><p className="text-sm text-ink-soft">Net amount put in</p><Amount value={t.net_contributions} colored={false} signed={false} className="text-3xl font-semibold" /></div>
          </div>
          {data.unvalued_count ? <p className="mt-2 text-sm text-review">{data.unvalued_count} holding(s) have no valuation yet and are not in the total.</p> : null}
        </Panel>
      ) : null}
      {data.items.length === 0 ? <Empty title="No holdings yet" action={<Button variant="primary" onClick={() => setAdding(true)}>Add holding</Button>}>Add mutual funds, stocks, PPF, NPS, FDs or gold and record their value when you check it.</Empty> : (
        <Panel>
          <ul className="divide-y divide-rule">
            {data.items.map((h) => (
              <li key={h.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="font-medium">{h.name}</p>
                  <p className="text-xs text-ink-faint">{TYPES[h.instrument_type]}{h.units ? `, ${h.units} units` : ""}{h.valued_on ? `, valued ${formatDate(h.valued_on)}` : ", no valuation yet"}</p>
                </div>
                <div className="text-right">
                  {h.value ? <Amount value={h.value} currency={h.currency} colored={false} signed={false} className="font-medium" /> : <span className="text-review">Not valued</span>}
                  <p className="text-xs">{h.unrealized_gain ? <>Gain <Amount value={h.unrealized_gain} /></> : <span className="text-ink-faint">Gain unknown</span>}</p>
                </div>
                <div className="flex w-full gap-2 sm:w-auto">
                  <Button size="sm" onClick={() => setValueFor(h)}>Update value</Button>
                  <Button size="sm" variant="ghost" onClick={() => setTxnFor(h)}>Add purchase or sale</Button>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      )}
      <Sheet open={adding} onClose={() => setAdding(false)} title="Add holding"><HoldingForm onDone={() => { setAdding(false); mutate(); }} /></Sheet>
      <Sheet open={!!valueFor} onClose={() => setValueFor(null)} title={`Value of ${valueFor?.name ?? ""}`}>{valueFor ? <ValueForm h={valueFor} onDone={() => { setValueFor(null); mutate(); }} /> : null}</Sheet>
      <Sheet open={!!txnFor} onClose={() => setTxnFor(null)} title={`${txnFor?.name ?? ""}`}>{txnFor ? <TxnForm h={txnFor} onDone={() => { setTxnFor(null); mutate(); }} /> : null}</Sheet>
    </>
  );
}

function HoldingForm({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ name: "", instrument_type: "MUTUAL_FUND", identifier: "", valuation_mode: "MANUAL_TOTAL" });
  const [err, setErr] = useState<unknown>(null);
  async function submit(e: FormEvent) { e.preventDefault(); try { await api("/investments", { method: "POST", json: { ...f, identifier: f.identifier || null } }); onDone(); } catch (e2) { setErr(e2); } }
  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <Field label="Name">{(id) => <Input id={id} required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Parag Parikh Flexi Cap" />}</Field>
      <Field label="Type">{(id) => <Select id={id} value={f.instrument_type} onChange={(e) => setF({ ...f, instrument_type: e.target.value })}>{Object.entries(TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>}</Field>
      <Field label="ISIN, scheme code or ticker (optional)">{(id) => <Input id={id} value={f.identifier} onChange={(e) => setF({ ...f, identifier: e.target.value })} />}</Field>
      <Field label="How do you track it?">{(id) => <Select id={id} value={f.valuation_mode} onChange={(e) => setF({ ...f, valuation_mode: e.target.value })}><option value="MANUAL_TOTAL">By total value</option><option value="UNITS">By units and price</option></Select>}</Field>
      {err ? <ErrorNote error={err} /> : null}
      <Button variant="primary" type="submit">Add holding</Button>
    </form>
  );
}

function ValueForm({ h, onDone }: { h: Holding; onDone: () => void }) {
  const [f, setF] = useState({ valuation_date: todayISO(), total_value: "", unit_price: "" });
  const [err, setErr] = useState<unknown>(null);
  return (
    <form className="flex flex-col gap-3" onSubmit={async (e) => { e.preventDefault(); try { await api(`/investments/${h.id}/valuations`, { method: "POST", json: { valuation_date: f.valuation_date, total_value: f.total_value || null, unit_price: f.unit_price || null } }); onDone(); } catch (e2) { setErr(e2); } }}>
      <Field label="Date">{(id) => <Input id={id} type="date" max={todayISO()} value={f.valuation_date} onChange={(e) => setF({ ...f, valuation_date: e.target.value })} />}</Field>
      {h.valuation_mode === "UNITS" ? <Field label="Price per unit (NAV)">{(id) => <Input id={id} inputMode="decimal" value={f.unit_price} onChange={(e) => setF({ ...f, unit_price: e.target.value })} />}</Field> : null}
      <Field label="Total value">{(id) => <Input id={id} inputMode="decimal" value={f.total_value} onChange={(e) => setF({ ...f, total_value: e.target.value })} />}</Field>
      {err ? <ErrorNote error={err} /> : null}
      <Button variant="primary" type="submit">Save value</Button>
    </form>
  );
}

function TxnForm({ h, onDone }: { h: Holding; onDone: () => void }) {
  const [f, setF] = useState({ action: "BUY", trade_date: todayISO(), units: "", gross_amount: "", fees: "0", ledger_transaction_id: "" });
  const { data: debits } = useApi<{ items: { id: string; transaction_date: string; description: string; amount: string }[] }>(`/transactions?limit=30&start_date=${f.trade_date.slice(0, 8)}01`);
  const [err, setErr] = useState<unknown>(null);
  return (
    <form className="grid grid-cols-1 gap-3 sm:grid-cols-2" onSubmit={async (e) => { e.preventDefault(); try { await api(`/investments/${h.id}/transactions`, { method: "POST", json: { ...f, units: f.units || null, ledger_transaction_id: f.ledger_transaction_id || null } }); onDone(); } catch (e2) { setErr(e2); } }}>
      <Field label="What happened">{(id) => <Select id={id} value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })}><option value="BUY">Bought</option><option value="CONTRIBUTION">Contributed</option><option value="SELL">Sold</option><option value="WITHDRAWAL">Withdrew</option><option value="DIVIDEND">Dividend</option><option value="INTEREST">Interest</option></Select>}</Field>
      <Field label="Date">{(id) => <Input id={id} type="date" value={f.trade_date} onChange={(e) => setF({ ...f, trade_date: e.target.value })} />}</Field>
      <Field label="Amount">{(id) => <Input id={id} required inputMode="decimal" value={f.gross_amount} onChange={(e) => setF({ ...f, gross_amount: e.target.value })} />}</Field>
      {h.valuation_mode === "UNITS" ? <Field label="Units">{(id) => <Input id={id} inputMode="decimal" value={f.units} onChange={(e) => setF({ ...f, units: e.target.value })} />}</Field> : null}
      <Field label="Linked bank transaction (optional)" hint="Marks that movement as an investment, not spending.">{(id, d) => (
        <Select id={id} aria-describedby={d} value={f.ledger_transaction_id} onChange={(e) => setF({ ...f, ledger_transaction_id: e.target.value })}>
          <option value="">None</option>{debits?.items.map((t) => <option key={t.id} value={t.id}>{t.transaction_date} {t.description} ({t.amount})</option>)}
        </Select>
      )}</Field>
      {err ? <div className="sm:col-span-2"><ErrorNote error={err} /></div> : null}
      <div className="sm:col-span-2"><Button variant="primary" type="submit">Save</Button></div>
    </form>
  );
}
