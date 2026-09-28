"use client";

import Link from "next/link";
import { use, useState, type FormEvent } from "react";
import { TxnList, TxnSheet, type Txn } from "@/components/transactions";
import { Amount, Button, ButtonLink, ErrorNote, Field, Input, Loading, PageHeader, Panel, Select } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { formatDate, KIND_LABEL, todayISO } from "@/lib/format";

type Detail = { id: string; name: string; kind: string; nature: string; currency: string; masked_identifier: string | null; institution: string | null; balance: string | null; balance_observed_as_of: string | null; balance_as_of: string | null; transactions_after_snapshot: number; balance_stale: boolean; include_in_net_worth: boolean; status: string; version: number };
type Snapshot = { id: string; amount: string; kind: string; as_of: string; source: string; reconciliation: string };

export default function AccountDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: a, error, mutate } = useApi<Detail>(`/accounts/${id}`);
  const { data: snaps, mutate: mutSnaps } = useApi<Snapshot[]>(`/accounts/${id}/balances`);
  const { data: txns, mutate: mutTx } = useApi<{ items: Txn[] }>(`/transactions?account_id=${id}&limit=30`);
  const [open, setOpen] = useState<string | null>(null);
  const [bal, setBal] = useState({ amount: "", as_of: todayISO(), kind: "CURRENT" });
  const [result, setResult] = useState<{ state: string; unexplained_delta?: string } | null>(null);
  const [err, setErr] = useState<unknown>(null);

  if (error) return <ErrorNote error={error} />;
  if (!a) return <Loading />;

  async function addBalance(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      const r = await api<{ reconciliation: { state: string; unexplained_delta?: string } }>(`/accounts/${id}/balances`, { method: "POST", json: { amount: bal.amount, as_of: bal.as_of, balance_kind: bal.kind } });
      setResult(r.reconciliation);
      setBal({ ...bal, amount: "" });
      mutate();
      mutSnaps();
    } catch (e2) {
      setErr(e2);
    }
  }

  async function toggle(field: "include_in_net_worth" | "status") {
    const body = field === "status" ? { status: a!.status === "ACTIVE" ? "ARCHIVED" : "ACTIVE" } : { include_in_net_worth: !a!.include_in_net_worth };
    await api(`/accounts/${id}`, { method: "PATCH", json: { version: a!.version, ...body } }).catch(setErr);
    mutate();
  }

  const owed = a.nature === "LIABILITY";
  return (
    <>
      <PageHeader title={a.name} description={[KIND_LABEL[a.kind], a.institution, a.masked_identifier].filter(Boolean).join(", ")}
        actions={<ButtonLink href={`/imports?account=${id}`} variant="primary">Import statement</ButtonLink>} />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_1.4fr]">
        <div className="flex flex-col gap-6">
          <Panel title={owed ? "Amount owed" : "Balance"}>
            {a.balance === null ? <p className="text-review">Unknown. Add a balance below or import a statement that has a balance column.</p> : (
              <>
                <p className="display text-[2.4rem] font-medium leading-none"><Amount value={a.balance} currency={a.currency} colored={false} signed={false} /></p>
                <p className="mt-1 text-xs text-ink-faint">
                  Last confirmed balance on {formatDate(a.balance_observed_as_of)}
                  {a.transactions_after_snapshot ? `, plus ${a.transactions_after_snapshot} later transactions through ${formatDate(a.balance_as_of)}` : ""}.
                </p>
              </>
            )}
            <form onSubmit={addBalance} className="mt-4 grid grid-cols-2 gap-2 border-t border-rule pt-4">
              <Field label={owed ? "Owed on statement" : "Balance from statement"}>{(fid) => <Input id={fid} required inputMode="decimal" value={bal.amount} onChange={(e) => setBal({ ...bal, amount: e.target.value.replace(/[^0-9.-]/g, "") })} />}</Field>
              <Field label="At end of">{(fid) => <Input id={fid} type="date" max={todayISO()} value={bal.as_of} onChange={(e) => setBal({ ...bal, as_of: e.target.value })} />}</Field>
              {owed ? (
                <Field label="Kind">{(fid) => <Select id={fid} value={bal.kind} onChange={(e) => setBal({ ...bal, kind: e.target.value })}><option value="CURRENT">Current</option><option value="STATEMENT">Statement</option></Select>}</Field>
              ) : null}
              <div className="col-span-2"><Button type="submit" size="sm">Record balance</Button></div>
            </form>
            {result ? (
              <p role="status" className={result.state === "DISCREPANCY" ? "mt-3 text-sm text-review" : "mt-3 text-sm text-credit"}>
                {result.state === "BALANCED" ? "This matches your transactions." : result.state === "DISCREPANCY" ? `This differs from your transactions by ${result.unexplained_delta}. A review item was created.` : "Recorded as a starting point."}
              </p>
            ) : null}
            {err ? <div className="mt-3"><ErrorNote error={err} /></div> : null}
          </Panel>
          <Panel title="Balance history">
            <ul className="text-sm">
              {snaps?.map((s) => (
                <li key={s.id} className="flex justify-between border-b border-rule py-1.5 last:border-0">
                  <span>{formatDate(s.as_of)} <span className="text-ink-faint">{s.source.toLowerCase()}{s.reconciliation === "DISCREPANCY" ? ", mismatch" : ""}</span></span>
                  <Amount value={s.amount} currency={a.currency} colored={false} signed={false} />
                </li>
              ))}
            </ul>
          </Panel>
          <div className="flex flex-wrap gap-2 text-sm">
            <Button size="sm" variant="ghost" onClick={() => toggle("include_in_net_worth")}>{a.include_in_net_worth ? "Exclude from net worth" : "Include in net worth"}</Button>
            <Button size="sm" variant="ghost" onClick={() => toggle("status")}>{a.status === "ACTIVE" ? "Archive account" : "Unarchive"}</Button>
          </div>
        </div>
        <Panel title="Transactions" action={<Link className="text-sm underline text-ink-soft" href={`/transactions?account_id=${id}`}>All</Link>}>
          {txns?.items.length ? <TxnList items={txns.items} onOpen={(t) => setOpen(t.id)} /> : <p className="text-sm text-ink-soft">No transactions yet.</p>}
        </Panel>
      </div>
      <TxnSheet id={open} onClose={() => setOpen(null)} onChanged={() => { mutTx(); mutate(); }} />
    </>
  );
}
