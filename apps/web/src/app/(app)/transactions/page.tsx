"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState, type FormEvent } from "react";
import useSWRInfinite from "swr/infinite";
import { CategoryOptions, TxnList, TxnSheet, useCategories, type Txn } from "@/components/transactions";
import { Icon } from "@/components/icons";
import { Button, cx, Empty, ErrorNote, Field, Input, Loading, PageHeader, Select, Sheet } from "@/components/ui";
import { api, ApiError, useApi } from "@/lib/api";
import { todayISO } from "@/lib/format";

type Page = { items: Txn[]; next_cursor: string | null; evidence: { kind: string; ledger_changed_since: boolean; params: Record<string, unknown> } | null };
type Account = { id: string; name: string; currency: string };

const FILTER_KEYS = ["q", "start_date", "end_date_exclusive", "account_id", "category_id", "direction", "effect", "min_amount", "max_amount", "merchant", "tag_id", "transfer", "recurring", "source", "uncategorized", "needs_review", "query_id", "sort"] as const;

function Explorer() {
  const router = useRouter();
  const params = useSearchParams();
  const { data: cats } = useCategories();
  const { data: accounts } = useApi<Account[]>("/accounts");
  const { data: tags } = useApi<{ id: string; name: string }[]>("/tags");
  const [open, setOpen] = useState<string | null>(params.get("open"));
  const [adding, setAdding] = useState(false);
  const [showFilters, setShowFilters] = useState(false);

  const filterQuery = useMemo(() => {
    const u = new URLSearchParams();
    for (const k of FILTER_KEYS) {
      const v = params.get(k);
      if (v) u.set(k, k === "uncategorized" || k === "needs_review" ? "true" : v);
    }
    return u.toString();
  }, [params]);

  const { data, error, size, setSize, mutate, isLoading } = useSWRInfinite<Page, ApiError>(
    (i: number, prev: Page | null) => (prev && !prev.next_cursor ? null : `/transactions?limit=50&${filterQuery}${prev?.next_cursor ? `&cursor=${encodeURIComponent(prev.next_cursor)}` : ""}`),
    (p: string) => api<Page>(p),
    { revalidateOnFocus: false },
  );
  const items = data?.flatMap((p) => p.items) ?? [];
  const evidence = data?.[0]?.evidence;
  const hasMore = !!data?.[data.length - 1]?.next_cursor;

  function setFilter(k: string, v: string) {
    const u = new URLSearchParams(params.toString());
    if (v) u.set(k, v);
    else u.delete(k);
    u.delete("open");
    u.delete("query_id");
    router.replace(`/transactions?${u.toString()}`);
  }

  const [q, setQ] = useState(params.get("q") ?? "");
  useEffect(() => setQ(params.get("q") ?? ""), [params]);
  function search(e: FormEvent) {
    e.preventDefault();
    setFilter("q", q.trim());
  }
  const active = FILTER_KEYS.filter((k) => k !== "sort" && k !== "q" && params.get(k)).length;
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const { data: aiSettings } = useApi<{ auto_categorize_ready: boolean }>("/ai/settings");
  const onlyUncategorised = params.get("uncategorized") === "1" || params.get("uncategorized") === "true";

  const QUICK: { label: string; set: Record<string, string> }[] = [
    { label: "Everything", set: {} },
    { label: "Money out", set: { direction: "debit" } },
    { label: "Money in", set: { direction: "credit" } },
    { label: "Not categorised", set: { uncategorized: "1" } },
    { label: "Needs review", set: { needs_review: "1" } },
    { label: "Transfers", set: { transfer: "true" } },
  ];
  const QUICK_KEYS = ["direction", "uncategorized", "needs_review", "transfer"];
  const quickActive = (set: Record<string, string>) =>
    QUICK_KEYS.every((k) => (set[k] ?? "") === (params.get(k) === "true" && k !== "transfer" ? "1" : params.get(k) ?? ""));
  function applyQuick(set: Record<string, string>) {
    const u = new URLSearchParams(params.toString());
    for (const k of QUICK_KEYS) u.delete(k);
    for (const [k, v] of Object.entries(set)) u.set(k, v);
    u.delete("open");
    u.delete("query_id");
    router.replace(`/transactions?${u.toString()}`);
  }

  return (
    <>
      <PageHeader title="Transactions" description="Every movement across your accounts. Open one to categorise, split or link it." actions={<Button variant="primary" onClick={() => setAdding(true)}><Icon name="plus" className="size-4" />Add transaction</Button>} />
      {evidence ? (
        <div className="mb-4 rounded-xl border border-rule bg-accent-wash px-4 py-2.5 text-sm text-ink-soft">
          Showing the transactions behind a report ({evidence.kind.replace("_", " ")}).
          {evidence.ledger_changed_since ? " The ledger has changed since that report was produced, so totals may differ now." : ""}
          <button className="ml-2 underline" onClick={() => router.replace("/transactions")}>Clear</button>
        </div>
      ) : null}
      <form onSubmit={search} className="mb-4 flex gap-2" role="search">
        <div className="relative flex-1">
          <Icon name="search" className="pointer-events-none absolute left-3 top-1/2 size-[18px] -translate-y-1/2 text-ink-faint" />
          <Input aria-label="Search descriptions, merchants and notes" className="pl-10" placeholder="Search descriptions, merchants, notes" value={q} onChange={(e) => setQ(e.target.value)} maxLength={100} />
        </div>
        <Button type="submit" className="hidden sm:inline-flex">Search</Button>
        <Button type="button" variant={showFilters || active ? "secondary" : "ghost"} onClick={() => setShowFilters(!showFilters)} aria-expanded={showFilters} className={cx(showFilters && "border-ink")}>
          <Icon name="filter" className="size-4" />Filters{active ? <span className="num inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-accent px-1 text-[11px] font-semibold text-accent-ink">{active}</span> : null}
        </Button>
      </form>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Quick filters" className="flex flex-wrap gap-1.5">
          {QUICK.map((f) => {
            const on = quickActive(f.set);
            return (
              <button key={f.label} type="button" aria-pressed={on} onClick={() => applyQuick(f.set)}
                className={cx("rounded-full border px-3 py-1 text-sm transition-colors", on ? "border-ink bg-accent font-medium text-accent-ink" : "border-rule bg-surface text-ink-soft hover:border-ink-faint hover:text-ink")}>
                {f.label}
              </button>
            );
          })}
        </div>
        <div className="ml-auto flex gap-2">
          {onlyUncategorised && aiSettings?.auto_categorize_ready ? <AiCategorise onQueued={() => setTimeout(() => mutate(), 8000)} /> : null}
          <Button size="sm" variant={picked ? "secondary" : "ghost"} onClick={() => setPicked(picked ? null : new Set())} aria-pressed={!!picked}>{picked ? "Done selecting" : "Select"}</Button>
        </div>
      </div>
      {showFilters ? (
        <div className="mb-5 grid grid-cols-2 gap-x-3 gap-y-4 rounded-xl border border-rule bg-raised p-5 sm:grid-cols-3 lg:grid-cols-4">
          <Field label="From">{(id) => <Input id={id} type="date" value={params.get("start_date") ?? ""} onChange={(e) => setFilter("start_date", e.target.value)} />}</Field>
          <Field label="Before (exclusive)">{(id) => <Input id={id} type="date" value={params.get("end_date_exclusive") ?? ""} onChange={(e) => setFilter("end_date_exclusive", e.target.value)} />}</Field>
          <Field label="Account">{(id) => (
            <Select id={id} value={params.get("account_id") ?? ""} onChange={(e) => setFilter("account_id", e.target.value)}>
              <option value="">All accounts</option>
              {accounts?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          )}</Field>
          <Field label="Category">{(id) => (
            <Select id={id} value={params.get("category_id") ?? ""} onChange={(e) => setFilter("category_id", e.target.value)}>
              <option value="">All categories</option>
              {cats ? <CategoryOptions cats={cats} /> : null}
            </Select>
          )}</Field>
          <Field label="Direction">{(id) => (
            <Select id={id} value={params.get("direction") ?? ""} onChange={(e) => setFilter("direction", e.target.value)}>
              <option value="">Money in and out</option><option value="debit">Money out</option><option value="credit">Money in</option>
            </Select>
          )}</Field>
          <Field label="Type">{(id) => (
            <Select id={id} value={params.get("effect") ?? ""} onChange={(e) => setFilter("effect", e.target.value)}>
              <option value="">Any</option><option value="expense">Expense</option><option value="income">Income</option><option value="transfer">Transfer</option>
              <option value="investment">Investment</option><option value="loan_principal">Loan principal</option><option value="unclassified">Not categorised</option>
            </Select>
          )}</Field>
          <Field label="Min amount">{(id) => <Input id={id} inputMode="decimal" value={params.get("min_amount") ?? ""} onChange={(e) => setFilter("min_amount", e.target.value.replace(/[^0-9.]/g, ""))} />}</Field>
          <Field label="Max amount">{(id) => <Input id={id} inputMode="decimal" value={params.get("max_amount") ?? ""} onChange={(e) => setFilter("max_amount", e.target.value.replace(/[^0-9.]/g, ""))} />}</Field>
          <Field label="Merchant">{(id) => <Input id={id} value={params.get("merchant") ?? ""} onChange={(e) => setFilter("merchant", e.target.value)} />}</Field>
          <Field label="Tag">{(id) => (
            <Select id={id} value={params.get("tag_id") ?? ""} onChange={(e) => setFilter("tag_id", e.target.value)}>
              <option value="">Any</option>{tags?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </Select>
          )}</Field>
          <Field label="Transfers">{(id) => (
            <Select id={id} value={params.get("transfer") ?? ""} onChange={(e) => setFilter("transfer", e.target.value)}>
              <option value="">Include</option><option value="false">Exclude</option><option value="true">Only transfers</option>
            </Select>
          )}</Field>
          <Field label="Recurring">{(id) => (
            <Select id={id} value={params.get("recurring") ?? ""} onChange={(e) => setFilter("recurring", e.target.value)}>
              <option value="">Any</option><option value="true">Recurring only</option><option value="false">One-off only</option>
            </Select>
          )}</Field>
          <Field label="Source">{(id) => (
            <Select id={id} value={params.get("source") ?? ""} onChange={(e) => setFilter("source", e.target.value)}>
              <option value="">Any</option><option value="IMPORT">Statement import</option><option value="MANUAL">Manual</option>
            </Select>
          )}</Field>
          <Field label="Sort">{(id) => (
            <Select id={id} value={params.get("sort") ?? "date_desc"} onChange={(e) => setFilter("sort", e.target.value)}>
              <option value="date_desc">Newest first</option><option value="date_asc">Oldest first</option>
              <option value="amount_asc">Largest money out</option><option value="amount_desc">Largest money in</option>
            </Select>
          )}</Field>
          <div className="col-span-full flex justify-end border-t border-rule pt-3"><Button variant="ghost" size="sm" onClick={() => router.replace("/transactions")}>Clear all filters</Button></div>
        </div>
      ) : null}

      {error ? <ErrorNote error={error} onRetry={() => mutate()} /> : isLoading ? <Loading /> : items.length === 0 ? (
        <Empty title="No transactions match">Change the filters, or import a statement to add transactions.</Empty>
      ) : (
        <div className={cx("overflow-clip rounded-xl border border-rule bg-surface px-5 sm:px-6", picked && "mb-40")}>
          <TxnList items={items} onOpen={(t) => setOpen(t.id)} selected={picked ?? undefined}
            onToggle={picked ? (t) => setPicked((cur) => { const n = new Set(cur); if (n.has(t.id)) n.delete(t.id); else n.add(t.id); return n; }) : undefined} />
        </div>
      )}
      {hasMore ? <div className="mt-5 flex justify-center"><Button onClick={() => setSize(size + 1)}>Show older transactions</Button></div> : null}

      {picked && picked.size ? <BulkBar ids={[...picked]} onDone={() => { setPicked(null); mutate(); }} onClear={() => setPicked(new Set())} /> : null}
      <TxnSheet id={open} onClose={() => setOpen(null)} onChanged={() => mutate()} />
      <AddTxn open={adding} onClose={() => setAdding(false)} accounts={accounts ?? []} onDone={() => { setAdding(false); mutate(); }} />
    </>
  );
}

function BulkBar({ ids, onDone, onClear }: { ids: string[]; onDone: () => void; onClear: () => void }) {
  const { data: cats } = useCategories();
  const [cat, setCat] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  async function apply() {
    setBusy(true);
    setErr(null);
    try {
      await api("/transactions/classify-bulk", { method: "POST", json: { transaction_ids: ids, category_id: cat } });
      onDone();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  return (
    <div role="region" aria-label="Selected transactions" className="fixed inset-x-3 bottom-[4.5rem] z-30 mx-auto max-w-2xl rounded-2xl border border-rule bg-surface p-3 shadow-sheet lg:bottom-6 lg:left-[272px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="px-1 text-sm font-medium"><span className="num">{ids.length}</span> selected</span>
        <Select aria-label="Category for the selected transactions" value={cat} onChange={(e) => setCat(e.target.value)} className="min-w-0 flex-1 sm:max-w-xs">
          <option value="">Choose a category…</option>
          {cats ? <CategoryOptions cats={cats} /> : null}
        </Select>
        <Button variant="primary" size="sm" busy={busy} disabled={!cat} onClick={apply}>Set category</Button>
        <Button variant="ghost" size="sm" onClick={onClear}>Clear</Button>
      </div>
      {err ? <div className="mt-2"><ErrorNote error={err} /></div> : null}
      <p className="mt-1.5 px-1 text-xs text-ink-faint">Split transactions and confirmed transfers are left as they are.</p>
    </div>
  );
}

function AiCategorise({ onQueued }: { onQueued: () => void }) {
  const [state, setState] = useState<"idle" | "busy" | "queued" | "error">("idle");
  return (
    <Button size="sm" busy={state === "busy"} disabled={state === "queued"}
      onClick={async () => { setState("busy"); try { await api("/ai/categorize-now", { method: "POST" }); setState("queued"); onQueued(); } catch { setState("error"); } }}>
      {state === "queued" ? "AI is categorising…" : state === "error" ? "Try AI again" : "Categorise with AI"}
    </Button>
  );
}

function AddTxn({ open, onClose, accounts, onDone }: { open: boolean; onClose: () => void; accounts: Account[]; onDone: () => void }) {
  const { data: cats } = useCategories();
  const [f, setF] = useState({ account_id: "", direction: "out", amount: "", date: todayISO(), description: "", category_id: "" });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api("/transactions", {
        method: "POST",
        headers: { "idempotency-key": crypto.randomUUID() },
        json: { account_id: f.account_id, amount: f.direction === "out" ? `-${f.amount}` : f.amount, transaction_date: f.date, description: f.description, category_id: f.category_id || null },
      });
      setF({ ...f, amount: "", description: "" });
      onDone();
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Sheet open={open} onClose={onClose} title="Add a transaction">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <p className="text-sm text-ink-soft">For cash and anything not on a statement. Imported transactions should come from statements so they can be de-duplicated.</p>
        <Field label="Account">{(id) => (
          <Select id={id} required value={f.account_id} onChange={(e) => setF({ ...f, account_id: e.target.value })}>
            <option value="">Choose…</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        )}</Field>
        <fieldset className="grid grid-cols-2 gap-1 rounded-xl bg-sunken p-1 text-sm">
          <legend className="sr-only">Direction</legend>
          <label className="flex cursor-pointer items-center justify-center rounded-lg py-2 font-medium text-ink-soft has-[:checked]:bg-surface has-[:checked]:text-debit has-[:checked]:shadow-sm"><input className="sr-only" type="radio" checked={f.direction === "out"} onChange={() => setF({ ...f, direction: "out" })} />Money out</label>
          <label className="flex cursor-pointer items-center justify-center rounded-lg py-2 font-medium text-ink-soft has-[:checked]:bg-surface has-[:checked]:text-credit has-[:checked]:shadow-sm"><input className="sr-only" type="radio" checked={f.direction === "in"} onChange={() => setF({ ...f, direction: "in" })} />Money in</label>
        </fieldset>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Amount">{(id) => <Input id={id} required inputMode="decimal" pattern="\d+(\.\d{1,2})?" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value.replace(/[^0-9.]/g, "") })} />}</Field>
          <Field label="Date">{(id) => <Input id={id} type="date" required value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} />}</Field>
        </div>
        <Field label="Description">{(id) => <Input id={id} required maxLength={500} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />}</Field>
        <Field label="Category (optional)">{(id) => (
          <Select id={id} value={f.category_id} onChange={(e) => setF({ ...f, category_id: e.target.value })}>
            <option value="">Categorise automatically</option>{cats ? <CategoryOptions cats={cats} /> : null}
          </Select>
        )}</Field>
        {err ? <ErrorNote error={err} /> : null}
        <Button type="submit" variant="primary" busy={busy}>Add transaction</Button>
      </form>
    </Sheet>
  );
}

export default function TransactionsPage() {
  return <Suspense fallback={<Loading />}><Explorer /></Suspense>;
}
