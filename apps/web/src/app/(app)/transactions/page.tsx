"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState, type FormEvent } from "react";
import useSWRInfinite from "swr/infinite";
import { CategoryOptions, TxnList, TxnSheet, useCategories, type DayTotal, type Txn } from "@/components/transactions";
import { Icon } from "@/components/icons";
import { useQuickAdd } from "@/components/quick-add-context";
import { Amount, Button, cx, Empty, ErrorNote, Field, Input, Loading, PageHeader, Select } from "@/components/ui";
import { api, ApiError, useApi } from "@/lib/api";
import { formatDate, formatMoney, formatMonth, shortINR, todayISO } from "@/lib/format";
import { moneyIn, spent } from "@/lib/money";
import { daysOf, periodFor, shiftMonth } from "@/lib/period";

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
  const add = useQuickAdd();
  const [showFilters, setShowFilters] = useState(false);
  const view = params.get("view") === "calendar" ? "calendar" : "list";
  // The installed app's "Add a transaction" shortcut lands here with ?add=1.
  useEffect(() => {
    if (params.get("add") !== "1") return;
    add();
    const u = new URLSearchParams(params.toString());
    u.delete("add");
    router.replace(`/transactions${u.toString() ? `?${u}` : ""}`);
  }, [params, add, router]);

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
  useEffect(() => {
    const refresh = () => void mutate();
    window.addEventListener("ol:ledger-changed", refresh);
    return () => window.removeEventListener("ol:ledger-changed", refresh);
  }, [mutate]);
  const items = data?.flatMap((p) => p.items) ?? [];
  // Day subtotals use the same definitions as the monthly summary; only shown when the list is not
  // narrowed by category, amount or other filters (the subtotal would then not match the rows).
  const narrowing = FILTER_KEYS.filter((k) => !["sort", "start_date", "end_date_exclusive", "account_id"].includes(k) && params.get(k)).length > 0;
  const dayRange = items.length ? { start: items.reduce((m, t) => (t.transaction_date < m ? t.transaction_date : m), items[0]!.transaction_date), end: items.reduce((m, t) => (t.transaction_date > m ? t.transaction_date : m), items[0]!.transaction_date) } : null;
  const accountParam = params.get("account_id");
  const { data: daily } = useApi<{ days: ({ date: string } & DayTotal)[] }>(
    !narrowing && dayRange ? `/analytics/daily?start_date=${dayRange.start}&end_date_exclusive=${nextDay(dayRange.end)}${accountParam ? `&account_id=${accountParam}` : ""}` : null,
  );
  const dayTotals = useMemo(() => Object.fromEntries((daily?.days ?? []).map((d) => [d.date, d])), [daily]);
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
  // The month bar shows the dates, so they don't count as a filter here.
  const active = FILTER_KEYS.filter((k) => !["sort", "q", "start_date", "end_date_exclusive"].includes(k) && params.get(k)).length;
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
      <PageHeader title="Transactions" description="Every movement across your accounts. Open one to categorise, split, share or attach a receipt." actions={
        <>
          <div role="tablist" aria-label="View" className="inline-flex rounded-lg border border-rule bg-surface p-0.5">
            {(["list", "calendar"] as const).map((v) => (
              <button key={v} role="tab" type="button" aria-selected={view === v} onClick={() => setFilter("view", v === "list" ? "" : v)}
                className={cx("inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-sm font-medium", view === v ? "bg-accent text-accent-ink" : "text-ink-soft hover:text-ink")}>
                <Icon name={v} className="size-4" />{v === "list" ? "List" : "Calendar"}
              </button>
            ))}
          </div>
        </>
      } />
      <MonthBar params={params} setRange={(start, end) => { const u = new URLSearchParams(params.toString()); if (start) { u.set("start_date", start); u.set("end_date_exclusive", end!); } else { u.delete("start_date"); u.delete("end_date_exclusive"); } u.delete("open"); u.delete("query_id"); router.replace(`/transactions?${u.toString()}`); }} />
      {evidence ? (
        <div className="mb-4 rounded-xl border border-rule bg-accent-wash px-4 py-2.5 text-sm text-ink-soft">
          Showing the transactions behind a report ({evidence.kind.replace("_", " ")}).
          {evidence.ledger_changed_since ? " The ledger has changed since that report was produced, so totals may differ now." : ""}
          <button className="ml-2 underline" onClick={() => router.replace("/transactions")}>Clear</button>
        </div>
      ) : null}
      {view === "list" ? <>
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
        <div role="group" aria-label="Quick filters" className="-mx-4 flex min-w-0 flex-1 gap-1.5 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:mx-0 sm:flex-wrap sm:overflow-visible sm:px-0 sm:pb-0">
          {QUICK.map((f) => {
            const on = quickActive(f.set);
            return (
              <button key={f.label} type="button" aria-pressed={on} onClick={() => applyQuick(f.set)}
                className={cx("shrink-0 rounded-full border px-3 py-1 text-sm transition-colors", on ? "border-ink bg-accent font-medium text-accent-ink" : "border-rule bg-surface text-ink-soft hover:border-ink-faint hover:text-ink")}>
                {f.label}
              </button>
            );
          })}
        </div>
        <div className="ml-auto flex shrink-0 gap-2">
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

      </> : null}
      {view === "calendar" ? (
        <CalendarView params={params} onOpen={(id) => setOpen(id)} />
      ) : error ? <ErrorNote error={error} onRetry={() => mutate()} /> : isLoading ? <Loading /> : items.length === 0 ? (
        <Empty title="No transactions match" action={<Button onClick={() => add()}><Icon name="plus" className="size-4" />Add one by hand</Button>}>Change the filters, or import a statement to add transactions.</Empty>
      ) : (
        <div className={cx("overflow-clip rounded-xl border border-rule bg-surface px-4 sm:px-6", picked && "mb-40")}>
          <TxnList items={items} onOpen={(t) => setOpen(t.id)} selected={picked ?? undefined} dayTotals={narrowing ? undefined : dayTotals}
            onToggle={picked ? (t) => setPicked((cur) => { const n = new Set(cur); if (n.has(t.id)) n.delete(t.id); else n.add(t.id); return n; }) : undefined} />
        </div>
      )}
      {view === "list" && hasMore ? <div className="mt-5 flex justify-center"><Button onClick={() => setSize(size + 1)}>Show older transactions</Button></div> : null}

      {picked && picked.size ? <BulkBar ids={[...picked]} onDone={() => { setPicked(null); mutate(); }} onClear={() => setPicked(new Set())} /> : null}
      <TxnSheet id={open} onClose={() => setOpen(null)} onChanged={() => mutate()} />
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

function nextDay(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

function monthOf(params: URLSearchParams): string | null {
  const s = params.get("start_date");
  const e = params.get("end_date_exclusive");
  if (!s || !e || s.slice(8) !== "01" || e.slice(8) !== "01" || shiftMonth(s.slice(0, 7), 1) !== e.slice(0, 7)) return null;
  return s.slice(0, 7);
}

/** Month navigation with that month's totals (same definitions as Home). */
function MonthBar({ params, setRange }: { params: URLSearchParams; setRange: (start: string | null, end?: string) => void }) {
  const month = monthOf(params);
  const account = params.get("account_id");
  const { data } = useApi<{ data: { income: string; net_expenses: string; unclassified_inflow: string; unclassified_outflow: string } }>(
    month ? `/analytics/summary?start_date=${month}-01&end_date_exclusive=${shiftMonth(month, 1)}-01${account ? `&account_id=${account}` : ""}` : null,
  );
  const go = (m: string) => setRange(`${m}-01`, `${shiftMonth(m, 1)}-01`);
  const btn = "inline-flex size-9 items-center justify-center rounded-lg text-ink-soft hover:bg-sunken hover:text-ink disabled:opacity-30";
  if (!month) {
    return (
      <div className="mb-4 flex items-center gap-2 text-sm text-ink-soft">
        <span>{params.get("start_date") || params.get("end_date_exclusive") ? "Custom dates" : "All dates"}</span>
        <button type="button" onClick={() => go(todayISO().slice(0, 7))} className="rounded-full border border-rule bg-surface px-3 py-1 hover:border-ink-faint hover:text-ink">By month</button>
      </div>
    );
  }
  const d = data?.data;
  const inn = d ? moneyIn(d) : null;
  const out = d ? spent(d) : null;
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl border border-rule bg-surface px-2 py-1.5 sm:px-3">
      <div className="flex items-center">
        <button type="button" className={btn} onClick={() => go(shiftMonth(month, -1))} aria-label="Previous month"><Icon name="left" className="size-5" /></button>
        <span className="display min-w-[8.5rem] text-center text-lg font-medium">{formatMonth(`${month}-01`)}</span>
        <button type="button" className={btn} disabled={month >= todayISO().slice(0, 7)} onClick={() => go(shiftMonth(month, 1))} aria-label="Next month"><Icon name="right" className="size-5" /></button>
        <button type="button" onClick={() => setRange(null)} className="ml-1 rounded-md px-2 py-1 text-xs text-ink-faint hover:bg-sunken hover:text-ink">All dates</button>
      </div>
      {d ? (
        <dl className="num flex gap-4 px-2 text-sm">
          <div><dt className="text-[11px] text-ink-faint">In</dt><dd className="font-medium text-credit">{formatMoney(inn, "INR", { decimals: false })}</dd></div>
          <div><dt className="text-[11px] text-ink-faint">Out</dt><dd className="font-medium text-debit">{formatMoney(out, "INR", { decimals: false })}</dd></div>
          <div><dt className="text-[11px] text-ink-faint">Net</dt><dd className="font-medium"><Amount value={subtract(inn!, out!)} decimals={false} /></dd></div>
        </dl>
      ) : null}
    </div>
  );
}

function subtract(a: string, b: string): string {
  // Display-only: both are exact server strings with at most 8 decimals.
  const scale = 100_000_000n;
  const toUnits = (v: string) => {
    const neg = v.startsWith("-");
    const [i, f = ""] = v.replace(/^[-+]/, "").split(".");
    const u = BigInt(i || "0") * scale + BigInt((f + "00000000").slice(0, 8));
    return neg ? -u : u;
  };
  const r = toUnits(a) - toUnits(b);
  const neg = r < 0n;
  const abs = neg ? -r : r;
  return `${neg ? "-" : ""}${abs / scale}.${(abs % scale).toString().padStart(8, "0")}`;
}

/** Month grid: each day shows money in and out; pick a day to see its transactions. */
function CalendarView({ params, onOpen }: { params: URLSearchParams; onOpen: (id: string) => void }) {
  const router = useRouter();
  const month = monthOf(params) ?? todayISO().slice(0, 7);
  const account = params.get("account_id");
  const p = periodFor("month", `${month}-01`);
  const { data } = useApi<{ days: ({ date: string; transaction_count: number } & DayTotal)[] }>(`/analytics/daily?start_date=${p.start}&end_date_exclusive=${p.endExclusive}${account ? `&account_id=${account}` : ""}`);
  const [day, setDay] = useState<string | null>(null);
  const { data: dayTx } = useApi<Page>(day ? `/transactions?limit=100&start_date=${day}&end_date_exclusive=${nextDay(day)}${account ? `&account_id=${account}` : ""}` : null);
  useEffect(() => {
    if (!monthOf(params)) {
      const u = new URLSearchParams(params.toString());
      u.set("start_date", p.start);
      u.set("end_date_exclusive", p.endExclusive);
      router.replace(`/transactions?${u.toString()}`);
    }
  }, [params, p.start, p.endExclusive, router]);
  const byDay = Object.fromEntries((data?.days ?? []).map((d) => [d.date, d]));
  const days = daysOf(p.start, p.endExclusive);
  const lead = (new Date(`${p.start}T00:00:00Z`).getUTCDay() + 6) % 7;
  const today = todayISO();
  return (
    <div className="flex flex-col gap-5">
      <div className="overflow-hidden rounded-xl border border-rule bg-surface">
        <div className="grid grid-cols-7 border-b border-rule bg-raised text-center text-[11px] font-medium uppercase tracking-wide text-ink-faint">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="py-1.5">{d}</div>)}
        </div>
        <div className="grid grid-cols-7">
          {Array.from({ length: lead }).map((_, i) => <div key={`l${i}`} className="min-h-16 border-b border-r border-rule bg-sunken/30 sm:min-h-20" />)}
          {days.map((d) => {
            const t = byDay[d];
            const sel = d === day;
            return (
              <button key={d} type="button" onClick={() => setDay(sel ? null : d)} aria-pressed={sel}
                aria-label={`${formatDate(d)}${t ? `: in ${formatMoney(t.money_in)}, out ${formatMoney(t.spent)}` : ", nothing recorded"}`}
                className={cx("flex min-h-16 flex-col items-stretch border-b border-r border-rule p-1 text-left transition-colors sm:min-h-20 sm:p-1.5 [&:nth-child(7n)]:border-r-0", sel ? "bg-accent-wash" : "hover:bg-sunken/60", d > today && "opacity-50")}>
                <span className={cx("num text-xs", d === today ? "inline-flex size-5 items-center justify-center self-start rounded-full bg-accent font-semibold text-accent-ink" : "text-ink-soft")}>{Number(d.slice(8))}</span>
                {t ? (
                  <span className="num mt-auto flex flex-col text-right text-[10.5px] leading-tight sm:text-xs">
                    {Number(t.money_in) ? <CellAmount value={t.money_in} className="text-credit" /> : null}
                    {Number(t.spent) ? <CellAmount value={t.spent} className="text-debit" /> : null}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>
      {day ? (
        <section aria-label={`Transactions on ${formatDate(day)}`} className="rounded-xl border border-rule bg-surface px-4 sm:px-6">
          <h2 className="pt-4 text-sm font-semibold">{formatDate(day)}</h2>
          {!dayTx ? <Loading /> : dayTx.items.length ? <TxnList items={dayTx.items} onOpen={(t) => onOpen(t.id)} compact /> : <p className="py-4 text-sm text-ink-soft">Nothing recorded on this day.</p>}
        </section>
      ) : <p className="text-center text-sm text-ink-faint">Choose a day to see its transactions.</p>}
    </div>
  );
}

export default function TransactionsPage() {
  return <Suspense fallback={<Loading />}><Explorer /></Suspense>;
}

/** A day's total in a calendar cell: the full figure where it fits, a short one (1.25L, 45.8k) on phones. */
function CellAmount({ value, className }: { value: string; className: string }) {
  const full = formatMoney(value, "INR", { decimals: false }).replace("₹", "");
  if (Math.abs(Number(value)) < 1000) return <span className={cx("truncate", className)}>{full}</span>;
  return (
    <span className={cx("truncate", className)}>
      <span className="sm:hidden">{shortINR(Number(value)).replace("₹", "")}</span>
      <span className="hidden sm:inline">{full}</span>
    </span>
  );
}
