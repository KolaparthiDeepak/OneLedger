"use client";

import { useState, type FormEvent } from "react";
import { Amount, Badge, Button, cx, Empty, ErrorNote, Field, Input, Loading, Meter, PageHeader, Panel, Sheet } from "@/components/ui";
import { Icon } from "@/components/icons";
import { api, useApi } from "@/lib/api";
import { formatDate, todayISO } from "@/lib/format";

type Stmt = { id: string; period_start: string; period_end: string; statement_balance: string; minimum_due: string | null; due_date: string; paid_since_statement: string; status: string };
type Card = { id: string; account_id: string; name: string; issuer: string; network: string | null; masked_identifier: string | null; currency: string; outstanding: string | null; outstanding_as_of: string | null; credit_limit: string | null; available_credit: string | null; statement_day: number | null; payment_due_days: number | null; latest_statement: Stmt | null };
type Bill = { kind: string; id: string; date: string; amount: string; estimated: boolean; overdue: boolean };

function daysUntil(iso: string): number {
  const d = new Date(`${iso}T00:00:00Z`).getTime();
  const t = new Date(`${todayISO()}T00:00:00Z`).getTime();
  return Math.round((d - t) / 86_400_000);
}

function NextBill({ bill, currency }: { bill: Bill | undefined; currency: string }) {
  if (!bill) return null;
  const n = daysUntil(bill.date);
  const when = n < 0 ? `${-n} days overdue` : n === 0 ? "due today" : n === 1 ? "due tomorrow" : `due in ${n} days`;
  return (
    <div className={cx("mt-4 flex items-center justify-between gap-3 rounded-xl px-3.5 py-2.5 text-sm", n <= 3 ? "bg-review-wash" : "bg-sunken/70")}>
      <span><span className="font-medium">Next bill</span> {bill.estimated ? "about " : ""}<Amount value={bill.amount} currency={currency} colored={false} signed={false} className="font-medium" /></span>
      <span className={cx("text-xs", n <= 3 ? "font-semibold text-review" : "text-ink-soft")}>{formatDate(bill.date, false)}, {when}</span>
    </div>
  );
}

const STATUS: Record<string, { label: string; tone: "credit" | "review" | "debit" | "neutral" }> = {
  PAID: { label: "Paid", tone: "credit" }, MINIMUM_PAID: { label: "Minimum paid", tone: "review" }, PARTIAL: { label: "Partly paid", tone: "review" },
  DUE: { label: "Due", tone: "neutral" }, OVERDUE: { label: "Overdue", tone: "debit" }, PARTIAL_OVERDUE: { label: "Overdue balance", tone: "debit" },
};

export default function CardsPage() {
  const { data, error, mutate } = useApi<Card[]>("/cards");
  const [adding, setAdding] = useState(false);
  const [stmtFor, setStmtFor] = useState<Card | null>(null);
  const [cycleFor, setCycleFor] = useState<Card | null>(null);
  const { data: bills } = useApi<Bill[]>("/insights/upcoming?days=60");
  if (error) return <ErrorNote error={error} />;
  if (!data) return <Loading />;
  return (
    <>
      <PageHeader title="Credit cards" description="Card purchases count as spending. Paying the bill is a transfer from your bank, so it is never counted twice."
        actions={<Button variant="primary" onClick={() => setAdding(true)}>Add card</Button>} />
      {data.length === 0 ? <Empty title="No cards yet" action={<Button variant="primary" onClick={() => setAdding(true)}>Add card</Button>}>Add a card, then import its statement.</Empty> : (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          {data.map((c) => {
            const s = c.latest_statement;
            const st = s ? STATUS[s.status] : undefined;
            return (
              <Panel key={c.id} title={`${c.name}${c.masked_identifier ? ` ${c.masked_identifier.slice(-4)}` : ""}`} action={<a className="underline-offset-4 hover:underline" href={`/accounts/${c.account_id}`}>Account</a>}>
                <p className="text-sm text-ink-soft">Outstanding{c.outstanding_as_of ? <span className="text-ink-faint">, as of {formatDate(c.outstanding_as_of)}</span> : null}</p>
                {c.outstanding === null ? <p className="text-review">Unknown</p> : <p className="display mt-1 text-[2.1rem] font-medium leading-none"><Amount value={c.outstanding} currency={c.currency} colored={false} signed={false} /></p>}
                {c.credit_limit && c.outstanding !== null ? (
                  <div className="mt-4">
                    <Meter label="Share of the credit limit used" tone={Number(c.outstanding) / Number(c.credit_limit) > 0.7 ? "debit" : "ink"} value={Math.max(0, Number(c.outstanding))} max={Number(c.credit_limit)} />
                    <p className="mt-1.5 flex justify-between gap-3 text-xs text-ink-faint">
                      <span>{Math.max(0, Math.round((Number(c.outstanding) / Number(c.credit_limit)) * 100))}% of the limit used</span>
                      <span>Available <Amount value={c.available_credit} colored={false} signed={false} /> of <Amount value={c.credit_limit} colored={false} signed={false} /></span>
                    </p>
                  </div>
                ) : null}
                {s ? (
                  <div className="mt-4 rounded-xl border border-rule bg-raised p-3.5 text-sm">
                    <div className="flex items-center justify-between"><span>Statement to {formatDate(s.period_end)}</span>{st ? <Badge tone={st.tone}>{st.label}</Badge> : null}</div>
                    <p className="mt-1">Balance <Amount value={s.statement_balance} colored={false} signed={false} />{s.minimum_due ? <>, minimum <Amount value={s.minimum_due} colored={false} signed={false} /></> : null}, due {formatDate(s.due_date)}</p>
                    <p className="text-ink-faint">Paid since statement: <Amount value={s.paid_since_statement} colored={false} signed={false} /></p>
                  </div>
                ) : null}
                <NextBill bill={bills?.find((b) => b.kind === "card" && b.id === c.id)} currency={c.currency} />
                <p className="mt-3 text-xs text-ink-faint">
                  {c.statement_day ? <>Statement on day {c.statement_day} of each month{c.payment_due_days ? `, due ${c.payment_due_days} ${c.payment_due_days === 1 ? "day" : "days"} later` : ""}. </> : "No billing dates yet, so the next bill can't be estimated. "}
                  <button type="button" className="underline underline-offset-2 hover:text-ink" onClick={() => setCycleFor(c)}>{c.statement_day ? "Change" : "Set billing dates"}</button>
                </p>
                <Button className="mt-4" size="sm" onClick={() => setStmtFor(c)}><Icon name="plus" className="size-4" />Add statement</Button>
              </Panel>
            );
          })}
        </div>
      )}
      <Sheet open={adding} onClose={() => setAdding(false)} title="Add credit card"><CardForm onDone={() => { setAdding(false); mutate(); }} /></Sheet>
      <Sheet open={!!cycleFor} onClose={() => setCycleFor(null)} title="Billing dates">{cycleFor ? <CycleForm card={cycleFor} onDone={() => { setCycleFor(null); mutate(); }} /> : null}</Sheet>
      <Sheet open={!!stmtFor} onClose={() => setStmtFor(null)} title="Add statement">{stmtFor ? <StatementForm card={stmtFor} onDone={() => { setStmtFor(null); mutate(); }} /> : null}</Sheet>
    </>
  );
}

function CardForm({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ name: "", issuer: "", card_number: "", credit_limit: "", statement_day: "", payment_due_days: "20", outstanding: "", outstanding_as_of: todayISO() });
  const [err, setErr] = useState<unknown>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api("/cards", { method: "POST", json: {
        name: f.name, issuer: f.issuer, card_number: f.card_number || null, credit_limit: f.credit_limit || null,
        statement_day: f.statement_day ? Number(f.statement_day) : null, payment_due_days: f.payment_due_days ? Number(f.payment_due_days) : null,
        outstanding: f.outstanding || null, outstanding_as_of: f.outstanding ? f.outstanding_as_of : null,
      } });
      onDone();
    } catch (e2) { setErr(e2); }
  }
  const up = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <form onSubmit={submit} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <Field label="Name">{(id) => <Input id={id} required value={f.name} onChange={up("name")} placeholder="HDFC Regalia" />}</Field>
      <Field label="Issuer">{(id) => <Input id={id} required value={f.issuer} onChange={up("issuer")} />}</Field>
      <Field label="Card number" hint="Only the last four digits are kept.">{(id, d) => <Input id={id} aria-describedby={d} inputMode="numeric" autoComplete="off" value={f.card_number} onChange={up("card_number")} />}</Field>
      <Field label="Credit limit">{(id) => <Input id={id} inputMode="decimal" value={f.credit_limit} onChange={up("credit_limit")} />}</Field>
      <Field label="Statement day of month">{(id) => <Input id={id} inputMode="numeric" value={f.statement_day} onChange={up("statement_day")} />}</Field>
      <Field label="Days to pay after statement">{(id) => <Input id={id} inputMode="numeric" value={f.payment_due_days} onChange={up("payment_due_days")} />}</Field>
      <Field label="Currently owed">{(id) => <Input id={id} inputMode="decimal" value={f.outstanding} onChange={up("outstanding")} />}</Field>
      <Field label="As of">{(id) => <Input id={id} type="date" value={f.outstanding_as_of} onChange={up("outstanding_as_of")} />}</Field>
      {err ? <div className="sm:col-span-2"><ErrorNote error={err} /></div> : null}
      <div className="sm:col-span-2"><Button variant="primary" type="submit">Add card</Button></div>
    </form>
  );
}

function StatementForm({ card, onDone }: { card: Card; onDone: () => void }) {
  const [f, setF] = useState({ period_start: "", period_end: "", statement_balance: "", minimum_due: "", due_date: "" });
  const [err, setErr] = useState<unknown>(null);
  const up = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <form className="grid grid-cols-1 gap-3 sm:grid-cols-2" onSubmit={async (e) => {
      e.preventDefault();
      try { await api(`/cards/${card.id}/statements`, { method: "POST", json: { ...f, minimum_due: f.minimum_due || null } }); onDone(); } catch (e2) { setErr(e2); }
    }}>
      <Field label="Period from">{(id) => <Input id={id} type="date" required value={f.period_start} onChange={up("period_start")} />}</Field>
      <Field label="Period to">{(id) => <Input id={id} type="date" required value={f.period_end} onChange={up("period_end")} />}</Field>
      <Field label="Statement balance">{(id) => <Input id={id} required inputMode="decimal" value={f.statement_balance} onChange={up("statement_balance")} />}</Field>
      <Field label="Minimum due">{(id) => <Input id={id} inputMode="decimal" value={f.minimum_due} onChange={up("minimum_due")} />}</Field>
      <Field label="Due date">{(id) => <Input id={id} type="date" required value={f.due_date} onChange={up("due_date")} />}</Field>
      {err ? <div className="sm:col-span-2"><ErrorNote error={err} /></div> : null}
      <p className="text-xs text-ink-faint sm:col-span-2">The statement summary records what you owe. It never adds transactions.</p>
      <div className="sm:col-span-2"><Button variant="primary" type="submit">Save statement</Button></div>
    </form>
  );
}


function CycleForm({ card, onDone }: { card: Card; onDone: () => void }) {
  const [day, setDay] = useState(card.statement_day ? String(card.statement_day) : "");
  const [due, setDue] = useState(card.payment_due_days ? String(card.payment_due_days) : "20");
  const [err, setErr] = useState<unknown>(null);
  return (
    <form className="grid grid-cols-2 gap-3" onSubmit={async (e) => {
      e.preventDefault();
      try {
        await api(`/accounts/${card.account_id}/card-details`, { method: "POST", json: { name: card.name, issuer: card.issuer, network: card.network, credit_limit: card.credit_limit, statement_day: Number(day), payment_due_days: Number(due) } });
        onDone();
      } catch (x) { setErr(x); }
    }}>
      <Field label="Statement day of month" hint="1 to 31">{(id, d) => <Input id={id} aria-describedby={d} required inputMode="numeric" value={day} onChange={(e) => setDay(e.target.value.replace(/\D/g, "").slice(0, 2))} />}</Field>
      <Field label="Days to pay after it">{(id) => <Input id={id} required inputMode="numeric" value={due} onChange={(e) => setDue(e.target.value.replace(/\D/g, "").slice(0, 2))} />}</Field>
      {err ? <div className="col-span-2"><ErrorNote error={err} /></div> : null}
      <div className="col-span-2"><Button type="submit" variant="primary">Save</Button></div>
    </form>
  );
}
