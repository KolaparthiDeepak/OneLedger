"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { Amount, Button, Empty, ErrorNote, Field, Input, Loading, Meter, PageHeader, Panel, Sheet } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { formatDate, todayISO } from "@/lib/format";
import type { Loan } from "@/lib/types";

export default function LoansPage() {
  const { data, error, mutate } = useApi<Loan[]>("/loans");
  const [adding, setAdding] = useState(false);
  if (error) return <ErrorNote error={error} />;
  if (!data) return <Loading />;
  return (
    <>
      <PageHeader title="Loans" description="Only the interest part of an EMI is an expense. Principal repaid reduces what you owe, so it is tracked separately."
        actions={<Button variant="primary" onClick={() => setAdding(true)}>Add loan</Button>} />
      {data.length === 0 ? <Empty title="No loans" action={<Button variant="primary" onClick={() => setAdding(true)}>Add loan</Button>}>Add a home, car or personal loan to split EMIs into principal and interest.</Empty> : (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          {data.map((l) => (
            <Panel key={l.id} title={`${l.lender} ${l.loan_type}`} action={<Link className="underline-offset-4 hover:underline" href={`/loans/${l.id}`}>Details</Link>}>
              <p className="text-sm text-ink-soft">Outstanding principal</p>
              {l.outstanding_principal === null ? <p className="text-review">Unknown</p> : <p className="display mt-1 text-[2.1rem] font-medium leading-none"><Amount value={l.outstanding_principal} currency={l.currency} colored={false} signed={false} /></p>}
              {l.outstanding_principal !== null && Number(l.original_principal) > 0 ? (
                <div className="mt-4">
                  <Meter label="Share of the loan repaid" tone="credit" value={Number(l.original_principal) - Number(l.outstanding_principal)} max={Number(l.original_principal)} />
                  <p className="mt-1.5 flex justify-between text-xs text-ink-faint">
                    <span>{Math.round(((Number(l.original_principal) - Number(l.outstanding_principal)) / Number(l.original_principal)) * 100)}% repaid</span>
                    <span>of <Amount value={l.original_principal} colored={false} signed={false} /></span>
                  </p>
                </div>
              ) : null}
              <p className="mt-2 text-xs text-ink-faint">From recorded balances{l.outstanding_as_of ? `, as of ${formatDate(l.outstanding_as_of)}` : ""}</p>
              <dl className="mt-5 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-rule pt-4 text-sm">
                <div><dt className="text-xs text-ink-faint">EMI</dt><dd className="font-medium"><Amount value={l.emi_amount} currency={l.currency} colored={false} signed={false} /></dd></div>
                <div><dt className="text-xs text-ink-faint">Interest rate</dt><dd className="num font-medium">{l.interest_rate_percent}% a year</dd></div>
                <div><dt className="text-xs text-ink-faint">Principal repaid (recorded)</dt><dd className="font-medium"><Amount value={l.principal_paid} colored={false} signed={false} /></dd></div>
                <div><dt className="text-xs text-ink-faint">Interest paid (recorded)</dt><dd className="font-medium"><Amount value={l.interest_paid} colored={false} signed={false} /></dd></div>
                {l.projection?.remaining_emis ? (
                  <>
                    <div><dt className="text-xs text-ink-faint">Estimated EMIs left</dt><dd className="num font-medium">{l.projection.remaining_emis}</dd></div>
                    <div><dt className="text-xs text-ink-faint">Estimated interest to come</dt><dd className="font-medium"><Amount value={l.projection.projected_interest} colored={false} signed={false} /></dd></div>
                  </>
                ) : null}
              </dl>
            </Panel>
          ))}
        </div>
      )}
      <Sheet open={adding} onClose={() => setAdding(false)} title="Add loan" wide><LoanForm onDone={() => { setAdding(false); mutate(); }} /></Sheet>
    </>
  );
}

function LoanForm({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ lender: "", loan_type: "Home Loan", original_principal: "", opening_outstanding: "", opening_date: todayISO(), start_date: "", first_emi_date: "", tenure_months: "240", annual_rate_percent: "", emi_amount: "" });
  const [err, setErr] = useState<unknown>(null);
  const up = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api("/loans", { method: "POST", json: { ...f, tenure_months: Number(f.tenure_months), emi_amount: f.emi_amount || null } });
      onDone();
    } catch (e2) { setErr(e2); }
  }
  return (
    <form onSubmit={submit} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <Field label="Lender">{(id) => <Input id={id} required value={f.lender} onChange={up("lender")} />}</Field>
      <Field label="Type">{(id) => <Input id={id} value={f.loan_type} onChange={up("loan_type")} />}</Field>
      <Field label="Original loan amount">{(id) => <Input id={id} required inputMode="decimal" value={f.original_principal} onChange={up("original_principal")} />}</Field>
      <Field label="Interest rate (% per year)">{(id) => <Input id={id} required inputMode="decimal" value={f.annual_rate_percent} onChange={up("annual_rate_percent")} />}</Field>
      <Field label="Outstanding principal now" hint="From your latest lender statement.">{(id, d) => <Input id={id} aria-describedby={d} required inputMode="decimal" value={f.opening_outstanding} onChange={up("opening_outstanding")} />}</Field>
      <Field label="Outstanding as of">{(id) => <Input id={id} type="date" required value={f.opening_date} onChange={up("opening_date")} />}</Field>
      <Field label="Loan start date">{(id) => <Input id={id} type="date" required value={f.start_date} onChange={up("start_date")} />}</Field>
      <Field label="First EMI date">{(id) => <Input id={id} type="date" required value={f.first_emi_date} onChange={up("first_emi_date")} />}</Field>
      <Field label="Tenure (months)">{(id) => <Input id={id} required inputMode="numeric" value={f.tenure_months} onChange={up("tenure_months")} />}</Field>
      <Field label="EMI" hint="Leave blank to calculate from the loan terms.">{(id, d) => <Input id={id} aria-describedby={d} inputMode="decimal" value={f.emi_amount} onChange={up("emi_amount")} />}</Field>
      {err ? <div className="sm:col-span-2"><ErrorNote error={err} /></div> : null}
      <div className="sm:col-span-2"><Button variant="primary" type="submit">Add loan</Button></div>
    </form>
  );
}
