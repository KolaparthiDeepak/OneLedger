"use client";

import { useState, type FormEvent } from "react";
import { api } from "@/lib/api";
import { KIND_LABEL, todayISO } from "@/lib/format";
import { Button, ErrorNote, Field, Input, Select } from "./ui";

const LIABILITY = new Set(["CREDIT_CARD", "LOAN", "OTHER_LIABILITY"]);

export function AccountForm({ onDone, submitLabel = "Add account" }: { onDone: (id: string) => void; submitLabel?: string }) {
  const [f, setF] = useState({ name: "", kind: "BANK_SAVINGS", institution_name: "", account_number: "", balance: "", as_of: todayISO(), currency: "INR", statement_day: "", payment_due_days: "20", credit_limit: "" });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const liability = LIABILITY.has(f.kind);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const body: Record<string, unknown> = { name: f.name, kind: f.kind, currency: f.currency, institution_name: f.institution_name || null, account_number: f.account_number || null };
      if (f.balance !== "") Object.assign(body, { opening_balance: f.balance, opening_date: f.as_of });
      const acct = await api<{ id: string }>("/accounts", { method: "POST", json: body });
      if (f.kind === "CREDIT_CARD") {
        // Billing dates let OneLedger estimate the next bill and send a reminder before it's due.
        await api(`/accounts/${acct.id}/card-details`, { method: "POST", json: {
          name: f.name, issuer: f.institution_name || f.name, credit_limit: f.credit_limit || null,
          statement_day: f.statement_day ? Number(f.statement_day) : null,
          payment_due_days: f.statement_day && f.payment_due_days ? Number(f.payment_due_days) : null,
        } });
      }
      onDone(acct.id);
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <Field label="Name">{(id) => <Input id={id} required maxLength={120} placeholder="HDFC Savings" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
      <Field label="Type">{(id) => (
        <Select id={id} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
          {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
      )}</Field>
      <Field label="Bank or institution">{(id) => <Input id={id} maxLength={120} value={f.institution_name} onChange={(e) => setF({ ...f, institution_name: e.target.value })} />}</Field>
      <Field label="Account or card number" hint="Only the last four digits are kept.">{(id, d) => <Input id={id} aria-describedby={d} inputMode="numeric" autoComplete="off" maxLength={25} value={f.account_number} onChange={(e) => setF({ ...f, account_number: e.target.value.replace(/[^0-9Xx*-]/g, "") })} />}</Field>
      <Field label={liability ? "Amount owed" : "Balance"} hint={liability ? "Enter what you owe as a positive number." : "Leave blank if you will import a statement with balances."}>
        {(id, d) => <Input id={id} aria-describedby={d} inputMode="decimal" value={f.balance} onChange={(e) => setF({ ...f, balance: e.target.value.replace(/[^0-9.-]/g, "") })} />}
      </Field>
      <Field label="Balance at the end of">{(id) => <Input id={id} type="date" value={f.as_of} max={todayISO()} onChange={(e) => setF({ ...f, as_of: e.target.value })} />}</Field>
      {f.kind === "CREDIT_CARD" ? (
        <>
          <Field label="Statement day of month" hint="From any statement: the day the billing cycle closes. Used for bill reminders.">{(id, d) => <Input id={id} aria-describedby={d} inputMode="numeric" maxLength={2} placeholder="e.g. 12" value={f.statement_day} onChange={(e) => setF({ ...f, statement_day: e.target.value.replace(/[^0-9]/g, "") })} />}</Field>
          <Field label="Days to pay after statement" hint="Usually 18 to 20.">{(id, d) => <Input id={id} aria-describedby={d} inputMode="numeric" maxLength={2} value={f.payment_due_days} onChange={(e) => setF({ ...f, payment_due_days: e.target.value.replace(/[^0-9]/g, "") })} />}</Field>
          <Field label="Credit limit (optional)">{(id) => <Input id={id} inputMode="decimal" value={f.credit_limit} onChange={(e) => setF({ ...f, credit_limit: e.target.value.replace(/[^0-9.]/g, "") })} />}</Field>
        </>
      ) : null}
      {err ? <div className="sm:col-span-2"><ErrorNote error={err} /></div> : null}
      <div className="sm:col-span-2"><Button type="submit" variant="primary" busy={busy}>{submitLabel}</Button></div>
    </form>
  );
}
