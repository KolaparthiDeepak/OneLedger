"use client";

import Link from "next/link";
import { useState } from "react";
import { TxnSheet, type Txn } from "@/components/transactions";
import { Amount, Button, Empty, ErrorNote, Loading, PageHeader, Panel } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { formatDate, formatMoney } from "@/lib/format";

type Item = { id: string; kind: string; summary: string; related: Record<string, string>; evidence: Record<string, string>; transactions: Txn[]; created_at: string };

const EXPLAIN: Record<string, string> = {
  TRANSFER_SUGGESTION: "Money left one of your accounts and the same amount arrived in another around the same day. If it's a transfer, it won't count as spending or income.",
  POSSIBLE_DUPLICATE: "Two rows look the same but the statement doesn't prove they're one payment. Merge them if they are, or keep both if you really paid twice.",
  UNMATCHED_TRANSFER: "These look like transfers or card payments, but the other account isn't in OneLedger. Add that account, or confirm the money went to someone else.",
  RECONCILIATION: "The balance on your statement differs from what the transactions add up to. Usually a statement period is missing or imported twice.",
};

const TITLES: Record<string, string> = {
  TRANSFER_SUGGESTION: "Possible transfers between your accounts",
  POSSIBLE_DUPLICATE: "Possible duplicates",
  UNMATCHED_TRANSFER: "Transfers with no matching account",
  RECONCILIATION: "Balances that don't match",
  SOURCE_REVISION: "Bank-reported changes",
  CATEGORIZATION: "Categories to check",
};

function Row({ t, onOpen }: { t: Txn; onOpen: (id: string) => void }) {
  return (
    <button onClick={() => onOpen(t.id)} className="flex w-full items-baseline justify-between gap-3 px-3 py-2 text-left transition-colors hover:bg-sunken/60">
      <span className="min-w-0">
        <span className="block truncate font-medium">{t.merchant ?? t.description}</span>
        <span className="text-xs text-ink-faint">{formatDate(t.transaction_date)}, {t.account.name}</span>
      </span>
      <Amount value={t.amount} currency={t.currency} />
    </button>
  );
}

export default function ReviewPage() {
  const { data, error, mutate } = useApi<Item[]>("/review");
  const { data: uncat } = useApi<{ items: Txn[] }>("/transactions?uncategorized=true&limit=200");
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);

  async function act(id: string, fn: () => Promise<unknown>) {
    setBusy(id);
    setErr(null);
    try {
      await fn();
      await mutate();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  }

  if (error) return <ErrorNote error={error} onRetry={() => mutate()} />;
  if (!data) return <Loading />;
  const kinds = [...new Set(data.map((i) => i.kind))];
  const uncategorised = uncat?.items.length ?? 0;

  return (
    <>
      <PageHeader title="Review" description="OneLedger never guesses on these. Each decision you make here is remembered and changes your totals immediately." />
      {err ? <div className="mb-4"><ErrorNote error={err} /></div> : null}
      {uncategorised ? (
        <Link href="/transactions?uncategorized=1" className="group mb-6 flex items-center gap-4 rounded-xl border border-rule bg-surface px-5 py-4 transition-colors hover:border-ink-faint">
          <span className="display text-3xl font-medium text-review">{uncategorised >= 200 ? "200+" : uncategorised}</span>
          <span className="flex-1">
            <span className="block font-medium">{uncategorised === 1 ? "transaction has" : "transactions have"} no category yet</span>
            <span className="text-sm text-ink-soft">They count as not categorised in your totals until you choose one. On Transactions, use Select to categorise several at once.</span>
          </span>
          <span className="hidden text-sm font-medium text-ink-soft group-hover:text-ink sm:inline">Categorise</span>
        </Link>
      ) : null}
      {data.length === 0 ? (uncategorised ? null : <Empty title="Nothing to review">Everything imported so far is matched and categorised as far as OneLedger can tell.</Empty>) : (
        <div className="flex flex-col gap-6">
          {kinds.map((k) => (
            <Panel key={k} title={<span className="flex items-center gap-2">{TITLES[k] ?? k}<span className="num rounded-full bg-sunken px-2 text-xs font-semibold leading-5 text-ink-soft">{data.filter((i) => i.kind === k).length}</span></span>}>
              {EXPLAIN[k] ? <p className="-mt-1 mb-2 max-w-[75ch] text-sm text-ink-soft">{EXPLAIN[k]}</p> : null}
              <ul className="flex flex-col divide-y divide-rule">
                {data.filter((i) => i.kind === k).map((i) => (
                  <li key={i.id} className="py-4">
                    {k === "UNMATCHED_TRANSFER" || k === "TRANSFER_SUGGESTION" ? null : <p className="mb-2 text-sm text-ink-soft">{i.summary}</p>}
                    <div className="divide-y divide-rule overflow-hidden rounded-lg border border-rule">
                      {i.transactions.map((t) => <Row key={t.id} t={t} onOpen={setOpen} />)}
                    </div>
                    {k === "RECONCILIATION" ? (
                      <p className="mt-2 text-sm">
                        Expected <span className="num">{formatMoney(i.evidence.expected)}</span>, statement says <span className="num">{formatMoney(i.evidence.observed)}</span>,
                        a difference of <span className="num font-medium">{formatMoney(i.evidence.unexplained_delta, "INR", { signed: true })}</span>.
                        {i.related.account_id ? <> <Link className="underline" href={`/accounts/${i.related.account_id}`}>Open account</Link></> : null}
                      </p>
                    ) : null}
                    <div className="mt-3 flex flex-wrap justify-end gap-2">
                      {k === "TRANSFER_SUGGESTION" ? (
                        <>
                          <Button size="sm" variant="primary" busy={busy === i.id} onClick={() => act(i.id, () => api(`/transfers/${i.related.transfer_id}/confirm`, { method: "POST" }))}>Yes, it's a transfer</Button>
                          <Button size="sm" busy={busy === i.id} onClick={() => act(i.id, () => api(`/transfers/${i.related.transfer_id}/reject`, { method: "POST" }))}>Not a transfer</Button>
                        </>
                      ) : k === "POSSIBLE_DUPLICATE" && i.transactions.length === 2 ? (
                        <>
                          <Button size="sm" variant="primary" busy={busy === i.id} onClick={() => act(i.id, () => api("/transactions/merge", { method: "POST", json: { survivor_id: i.transactions[1]!.id, duplicate_id: i.transactions[0]!.id } }))}>Merge into one</Button>
                          <Button size="sm" busy={busy === i.id} onClick={() => act(i.id, () => api(`/review/${i.id}/not-duplicate`, { method: "POST" }))}>Both are real</Button>
                        </>
                      ) : k === "UNMATCHED_TRANSFER" ? (
                        <>
                          <Button size="sm" busy={busy === i.id} onClick={() => act(i.id, () => api(`/review/${i.id}/external`, { method: "POST" }))}>The other side isn't tracked</Button>
                          <Link className="inline-flex min-h-8 items-center rounded-lg px-3 text-sm font-medium text-ink-soft hover:bg-sunken hover:text-ink" href="/accounts?new=1">Add the other account</Link>
                        </>
                      ) : (
                        <Button size="sm" busy={busy === i.id} onClick={() => act(i.id, () => api(`/review/${i.id}/dismiss`, { method: "POST" }))}>Mark as checked</Button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </Panel>
          ))}
        </div>
      )}
      <TxnSheet id={open} onClose={() => setOpen(null)} onChanged={() => mutate()} />
    </>
  );
}
