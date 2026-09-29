"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Donut } from "@/components/charts";
import { ledgerChanged } from "@/components/quick-add";
import { Amount, Button, cx, Empty, ErrorNote, Field, Input, Loading, PageHeader, Panel, Select, Sheet } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { formatDate, formatMoney, todayISO } from "@/lib/format";
import { addMoney } from "@/lib/money";

type Holding = { id: string; name: string; instrument_type: string; currency: string; valuation_mode: string; units: string | null; net_contributions: string; value: string | null; valued_on: string | null; valuation_source: string | null; unrealized_gain: string | null; gain_note: string | null; income: string; xirr_percent: string | null; instrument_identifier: string | null; funded_by_recurring: boolean };
type Recurring = { id: string; label: string; type: string; typical_amount: string; cadence: string; state: string; tracked_as_holding: boolean; account_name: string | null };
const TYPE_CODE: Record<string, string> = { MUTUAL_FUND: "FOOD", STOCK: "HOUSING", ETF: "SHOPPING", PPF: "TRANSPORT", NPS: "TRAVEL", FIXED_DEPOSIT: "ENTERTAINMENT", GOLD: "HEALTHCARE", BOND: "SUBSCRIPTIONS", REAL_ESTATE: "LOANS", OTHER: "OTHER" };

function SipOffers({ onDone }: { onDone: () => void }) {
  const { data } = useApi<Recurring[]>("/recurring");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const offers = (data ?? []).filter((r) => r.type === "SIP" && !r.tracked_as_holding && r.state !== "DISMISSED" && r.typical_amount.startsWith("-"));
  if (!offers.length) return null;
  return (
    <Panel className="mb-6" title="Regular investments found in your statements">
      <ul className="flex flex-col divide-y divide-rule">
        {offers.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
            <span className="min-w-0">
              <span className="block font-medium">{r.label}</span>
              <span className="text-xs text-ink-faint"><span className="num">{formatMoney(r.typical_amount.replace("-", ""))}</span> {r.cadence.toLowerCase()}{r.account_name ? ` from ${r.account_name}` : ""}</span>
            </span>
            <Button size="sm" busy={busy === r.id} onClick={async () => {
              setBusy(r.id);
              setErr(null);
              try { await api("/investments/from-recurring", { method: "POST", json: { recurring_id: r.id } }); ledgerChanged(); onDone(); } catch (x) { setErr(x); } finally { setBusy(null); }
            }}>Track as a holding</Button>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-ink-faint">Each payment, past and future, becomes a contribution to the holding and stops counting as spending.</p>
      {err ? <div className="mt-2"><ErrorNote error={err} /></div> : null}
    </Panel>
  );
}
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
  const byType: Record<string, string> = {};
  for (const h of data.items) if (h.value) byType[h.instrument_type] = addMoney(byType[h.instrument_type], h.value);
  const slices = Object.entries(byType).map(([k, v]) => ({ key: k, code: TYPE_CODE[k] ?? "OTHER", label: TYPES[k] ?? k, amount: v }));
  return (
    <>
      <PageHeader title="Investments" description="Mutual funds can be priced from AMFI's public daily NAV list; everything else uses the values you record." actions={<Button variant="primary" onClick={() => setAdding(true)}>Add holding</Button>} />
      <SipOffers onDone={() => mutate()} />
      {t ? (
        <Panel className="mb-6">
          <div className="grid items-center gap-6 sm:grid-cols-[minmax(0,1fr)_180px]">
            <div>
              <div className="flex flex-wrap gap-8">
                <div><p className="text-sm text-ink-soft">Latest value</p><p className="display text-[2.1rem] font-medium leading-tight"><Amount value={t.value} colored={false} signed={false} /></p></div>
                <div><p className="text-sm text-ink-soft">Net amount put in</p><p className="display text-[2.1rem] font-medium leading-tight"><Amount value={t.net_contributions} colored={false} signed={false} /></p></div>
              </div>
              {data.unvalued_count ? <p className="mt-2 text-sm text-review">{data.unvalued_count === 1 ? "1 holding has" : `${data.unvalued_count} holdings have`} no value yet and {data.unvalued_count === 1 ? "is" : "are"} not in the total.</p> : null}
            </div>
            {slices.length > 1 ? <div className="mx-auto w-full max-w-[180px]"><Donut slices={slices} total={t.value} caption="Allocation" /></div> : null}
          </div>
        </Panel>
      ) : null}
      {data.items.length === 0 ? <Empty title="No holdings yet" action={<Button variant="primary" onClick={() => setAdding(true)}>Add holding</Button>}>Add mutual funds, stocks, PPF, NPS, FDs or gold and record their value when you check it.</Empty> : (
        <Panel>
          <ul className="divide-y divide-rule">
            {data.items.map((h) => (
              <li key={h.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="font-medium">{h.name}</p>
                  <p className="text-xs text-ink-faint">
                    {TYPES[h.instrument_type]}{h.units ? `, ${Number(h.units).toLocaleString("en-IN", { maximumFractionDigits: 4 })} units` : ""}
                    {h.valued_on ? `, valued ${formatDate(h.valued_on)}${h.valuation_source === "AMFI" ? " at AMFI NAV" : ""}` : ", no value yet"}
                    {h.funded_by_recurring ? ", funded by a regular payment" : ""}
                  </p>
                </div>
                <div className="text-right">
                  {h.value ? <Amount value={h.value} currency={h.currency} colored={false} signed={false} className="font-medium" /> : <span className="text-review">Not valued</span>}
                  <p className="text-xs">
                    {h.unrealized_gain ? <>Gain <Amount value={h.unrealized_gain} /></> : <span className="text-ink-faint">Gain unknown</span>}
                    {h.xirr_percent ? <span className={cx("ml-2", h.xirr_percent.startsWith("-") ? "text-debit" : "text-credit")} title="Annualised return (XIRR)">{h.xirr_percent}% a year</span> : null}
                  </p>
                </div>
                <div className="flex w-full flex-wrap gap-2 sm:w-auto">
                  {h.instrument_type === "MUTUAL_FUND" && h.instrument_identifier && h.valuation_mode === "UNITS" ? <PriceButton h={h} onDone={() => mutate()} /> : null}
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

function PriceButton({ h, onDone }: { h: Holding; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <span className="inline-flex flex-col">
      <Button size="sm" variant="primary" busy={busy} onClick={async () => {
        setBusy(true);
        setErr(null);
        try { await api(`/investments/${h.id}/refresh-price`, { method: "POST" }); ledgerChanged(); onDone(); } catch (x) { setErr(x instanceof Error ? x.message : "Could not update"); } finally { setBusy(false); }
      }}>Update price</Button>
      {err ? <span className="mt-1 max-w-[16rem] text-xs text-debit">{err}</span> : null}
    </span>
  );
}

function FundSearch({ onPick }: { onPick: (f: { scheme_code: string; name: string }) => void }) {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setTerm(q.trim()), 350);
    return () => clearTimeout(t);
  }, [q]);
  const { data, error, isLoading } = useApi<{ scheme_code: string; name: string; nav: string; nav_date: string }[]>(term.length >= 3 ? `/investments/fund-search?q=${encodeURIComponent(term)}` : null);
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="fund-q" className="text-sm font-medium text-ink-soft">Find the fund (AMFI list)</label>
      <Input id="fund-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. parag parikh flexi direct growth" />
      <p className="text-xs text-ink-faint">Downloads AMFI&apos;s public NAV list. Nothing about you is sent.</p>
      {error ? <ErrorNote error={error} /> : isLoading ? <p className="text-xs text-ink-faint">Searching…</p> : data && term.length >= 3 ? (
        data.length ? (
          <ul className="max-h-48 overflow-y-auto rounded-lg border border-rule">
            {data.map((f) => (
              <li key={f.scheme_code}><button type="button" onClick={() => onPick(f)} className="w-full border-b border-rule px-3 py-2 text-left text-sm last:border-0 hover:bg-sunken">
                {f.name}<span className="block text-xs text-ink-faint">Scheme {f.scheme_code}, NAV {f.nav} on {f.nav_date}</span>
              </button></li>
            ))}
          </ul>
        ) : <p className="text-xs text-ink-faint">No fund matches that name.</p>
      ) : null}
    </div>
  );
}

function HoldingForm({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ name: "", instrument_type: "MUTUAL_FUND", identifier: "", valuation_mode: "UNITS" });
  const [err, setErr] = useState<unknown>(null);
  async function submit(e: FormEvent) { e.preventDefault(); try { await api("/investments", { method: "POST", json: { ...f, identifier: f.identifier || null } }); onDone(); } catch (e2) { setErr(e2); } }
  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <Field label="Type">{(id) => <Select id={id} value={f.instrument_type} onChange={(e) => setF({ ...f, instrument_type: e.target.value, valuation_mode: e.target.value === "MUTUAL_FUND" ? "UNITS" : "MANUAL_TOTAL" })}>{Object.entries(TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>}</Field>
      {f.instrument_type === "MUTUAL_FUND" ? <FundSearch onPick={(x) => setF({ ...f, name: x.name.slice(0, 160), identifier: x.scheme_code })} /> : null}
      <Field label="Name">{(id) => <Input id={id} required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Parag Parikh Flexi Cap" />}</Field>
      <Field label="ISIN, scheme code or ticker (optional)" hint={f.instrument_type === "MUTUAL_FUND" ? "Needed to update the price from AMFI." : undefined}>{(id, d) => <Input id={id} aria-describedby={d} value={f.identifier} onChange={(e) => setF({ ...f, identifier: e.target.value })} />}</Field>
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
