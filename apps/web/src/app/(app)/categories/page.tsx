"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { Icon } from "@/components/icons";
import { CategoryOptions, useCategories, type Category } from "@/components/transactions";
import { Amount, Badge, Button, cx, Empty, ErrorNote, Field, Input, Loading, PageHeader, Panel, Select, Sheet } from "@/components/ui";
import { api, ApiError, useApi } from "@/lib/api";
import { EFFECT_LABEL, formatDate, formatMoney } from "@/lib/format";

type Rule = { id: string; name: string; enabled: boolean; priority: number; conditions: Cond[]; category_id: string; effect: string };
type Cond = { field: string; op: string; value: string | string[]; value_to?: string };
type DryRun = { matching_transactions: number; locked_skipped: number; category: string; effect: string; sample: { id: string; date: string; amount: string; description: string }[] };

type Tab = "rules" | "merchants" | "categories" | "tags";
type Merchant = { id: string; name: string; aliases: string[]; category_id: string | null };
type Breakdown = { data: { categories: { category_id: string | null; amount: string; count: number }[] } };

const PIPELINE = [
  { title: "Set by you", text: "Anything you choose by hand is never changed." },
  { title: "Your rules", text: "Matching words, direction or amount." },
  { title: "Your merchants", text: "Merchants you told OneLedger to remember." },
  { title: "Built-in patterns", text: "Common Indian merchants, UPI notes, bills." },
  { title: "AI, if switched on", text: "Only confident answers, labelled by AI." },
];

function daysAgo(n: number) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function CategoriesView() {
  const router = useRouter();
  const params = useSearchParams();
  const tab = (params.get("tab") as Tab | null) ?? "rules";
  const { data: cats, mutate } = useCategories();
  const { data: rules, mutate: mutRules } = useApi<Rule[]>("/rules");
  const { data: merchants, mutate: mutMerchants } = useApi<Merchant[]>("/merchants");
  const { data: tags } = useApi<{ id: string }[]>("/tags");
  const [adding, setAdding] = useState(false);
  if (!cats || !rules) return <Loading />;
  const live = cats.filter((c) => !c.archived);
  const counts: Record<Tab, number> = { rules: rules.length, merchants: merchants?.filter((m) => m.category_id).length ?? 0, categories: live.filter((c) => !c.parent_id).length, tags: tags?.length ?? 0 };
  const TABS: { key: Tab; label: string }[] = [{ key: "rules", label: "Rules" }, { key: "merchants", label: "Merchants" }, { key: "categories", label: "Categories" }, { key: "tags", label: "Tags" }];
  return (
    <>
      <PageHeader title="Categories & rules" description="Decide how transactions are sorted. OneLedger works down this list and stops at the first answer."
        actions={<Recategorize />} />
      <ol aria-label="How OneLedger picks a category" className="-mx-4 mb-7 flex snap-x gap-px overflow-x-auto border-y border-rule bg-rule px-4 sm:mx-0 sm:grid sm:grid-cols-5 sm:overflow-hidden sm:rounded-xl sm:border sm:px-0">
        {PIPELINE.map((p, i) => (
          <li key={p.title} className="w-[62%] shrink-0 snap-start bg-surface px-4 py-3 sm:w-auto">
            <p className="flex items-center gap-2 text-sm font-semibold"><span className="num inline-flex size-5 items-center justify-center rounded-full bg-accent-wash text-[11px] text-ink">{i + 1}</span>{p.title}</p>
            <p className="mt-1 text-xs text-ink-faint">{p.text}</p>
          </li>
        ))}
      </ol>

      <div role="tablist" aria-label="Sections" className="mb-5 flex gap-1 overflow-x-auto border-b border-rule">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} onClick={() => router.replace(`/categories?tab=${t.key}`)}
            className={cx("-mb-px flex shrink-0 items-center gap-1.5 border-b-2 px-2 py-2.5 text-sm transition-colors sm:gap-2 sm:px-3", tab === t.key ? "border-ink font-semibold text-ink" : "border-transparent text-ink-soft hover:text-ink")}>
            {t.label}<span className="num rounded-full bg-sunken px-1.5 text-[11px] font-semibold leading-5 text-ink-soft">{counts[t.key]}</span>
          </button>
        ))}
      </div>

      <div role="tabpanel" className="page-enter" key={tab}>
        {tab === "rules" ? (
          <div className="flex flex-col gap-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="max-w-[65ch] text-sm text-ink-soft">A rule puts every matching transaction in a category, now and on future imports. You see what matches before anything changes.</p>
              <Button variant="primary" onClick={() => setAdding(true)}><Icon name="plus" className="size-4" />New rule</Button>
            </div>
            {rules.length === 0 ? (
              <Empty title="No rules yet" action={<Button variant="primary" onClick={() => setAdding(true)}>Create a rule</Button>}>For example: description contains &quot;BESCOM&quot;, put it in Utilities.</Empty>
            ) : (
              <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {rules.map((r) => <RuleCard key={r.id} rule={r} cats={cats} onChange={() => mutRules()} />)}
              </ul>
            )}
            <AiSuggestions cats={cats} />
          </div>
        ) : tab === "merchants" ? (
          <Merchants merchants={merchants} cats={cats} onChange={() => mutMerchants()} />
        ) : tab === "categories" ? (
          <CategoryGrid cats={cats} onChange={() => mutate()} />
        ) : (
          <Tags />
        )}
      </div>
      <Sheet open={adding} onClose={() => setAdding(false)} title="New rule" wide>
        <RuleEditor cats={cats} onSaved={() => { setAdding(false); mutRules(); }} />
      </Sheet>
    </>
  );
}

export default function CategoriesPage() {
  return <Suspense fallback={<Loading />}><CategoriesView /></Suspense>;
}

function describe(c: Cond) {
  if (c.field === "amount") return c.op === "between" ? `amount ${formatMoney(String(c.value))} to ${formatMoney(String(c.value_to))}` : `amount ${c.op === "gte" ? "≥" : c.op === "lte" ? "≤" : "="} ${formatMoney(String(c.value))}`;
  if (c.field === "direction") return c.value === "debit" ? "money out" : "money in";
  return `${c.field} ${c.op.replace("_", " ")} "${c.value}"`;
}

function Chip({ children }: { children: React.ReactNode }) {
  return <span className="inline-flex items-center rounded-md bg-sunken px-2 py-0.5 text-[13px] text-ink">{children}</span>;
}

function RuleCard({ rule, cats, onChange }: { rule: Rule; cats: Category[]; onChange: () => void }) {
  const [msg, setMsg] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const cat = cats.find((c) => c.id === rule.category_id);
  const parent = cat?.parent_id ? cats.find((c) => c.id === cat.parent_id) : null;
  return (
    <li className="flex flex-col rounded-xl border border-rule bg-surface p-4">
      <p className="font-medium">{rule.name}</p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-sm text-ink-soft">
        <span>When</span>
        {rule.conditions.map((c, i) => (
          <span key={i} className="inline-flex items-center gap-1.5">{i > 0 ? <span>and</span> : null}<Chip>{describe(c)}</Chip></span>
        ))}
        <span aria-hidden>→</span>
        <span className="inline-flex items-center rounded-md bg-accent-wash px-2 py-0.5 text-[13px] font-medium text-ink">{parent ? `${parent.name} / ` : ""}{cat?.name ?? "Deleted category"}</span>
      </div>
      <div className="mt-auto flex flex-wrap items-center gap-2 pt-4">
        <Button size="sm" busy={busy} onClick={async () => {
          setBusy(true);
          try {
            const r = await api<{ changed: number; locked_skipped: number }>(`/rules/${rule.id}/apply`, { method: "POST", json: { confirm: true } });
            setMsg(`${r.changed} transaction${r.changed === 1 ? "" : "s"} updated${r.locked_skipped ? `; ${r.locked_skipped} you set by hand were left alone` : ""}.`);
          } finally { setBusy(false); }
        }}>Apply to past transactions</Button>
        {confirm ? (
          <>
            <Button size="sm" variant="danger" onClick={() => api(`/rules/${rule.id}`, { method: "DELETE" }).then(onChange)}>Delete rule</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>Keep</Button>
          </>
        ) : <Button size="sm" variant="ghost" onClick={() => setConfirm(true)}>Delete</Button>}
      </div>
      {confirm ? <p className="mt-2 text-xs text-ink-faint">Transactions it already categorised keep their category.</p> : null}
      {msg ? <p role="status" className="mt-2 text-sm text-credit">{msg}</p> : null}
    </li>
  );
}

function RuleEditor({ cats, onSaved }: { cats: Category[]; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [direction, setDirection] = useState("");
  const [min, setMin] = useState("");
  const [max, setMax] = useState("");
  const [cat, setCat] = useState("");
  const [preview, setPreview] = useState<DryRun | null>(null);
  const [err, setErr] = useState<unknown>(null);

  function body() {
    const conditions: Cond[] = [];
    if (text) conditions.push({ field: "description", op: "contains", value: text });
    if (direction) conditions.push({ field: "direction", op: "equals", value: direction });
    if (min && max) conditions.push({ field: "amount", op: "between", value: min, value_to: max });
    else if (min) conditions.push({ field: "amount", op: "gte", value: min });
    else if (max) conditions.push({ field: "amount", op: "lte", value: max });
    return { name: name || `${text || "Rule"} → ${cats.find((c) => c.id === cat)?.name ?? ""}`, conditions, category_id: cat };
  }

  async function dry(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      setPreview(await api<DryRun>("/rules/dry-run", { method: "POST", json: body() }));
    } catch (e2) {
      setErr(e2);
    }
  }

  async function save() {
    setErr(null);
    try {
      const r = await api<{ id: string }>("/rules", { method: "POST", json: body() });
      await api(`/rules/${r.id}/apply`, { method: "POST", json: { confirm: true } });
      setPreview(null);
      setText(""); setName(""); setMin(""); setMax(""); setDirection("");
      onSaved();
    } catch (e2) {
      setErr(e2);
    }
  }

  return (
    <div>
      <form onSubmit={dry} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Description contains">{(id) => <Input id={id} maxLength={200} value={text} onChange={(e) => { setText(e.target.value); setPreview(null); }} placeholder="e.g. BESCOM" />}</Field>
        <Field label="Put in category">{(id) => (
          <Select id={id} required value={cat} onChange={(e) => { setCat(e.target.value); setPreview(null); }}>
            <option value="">Choose…</option><CategoryOptions cats={cats} />
          </Select>
        )}</Field>
        <Field label="Direction">{(id) => (
          <Select id={id} value={direction} onChange={(e) => { setDirection(e.target.value); setPreview(null); }}>
            <option value="">Either</option><option value="debit">Money out</option><option value="credit">Money in</option>
          </Select>
        )}</Field>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Min amount">{(id) => <Input id={id} inputMode="decimal" value={min} onChange={(e) => { setMin(e.target.value.replace(/[^0-9.]/g, "")); setPreview(null); }} />}</Field>
          <Field label="Max amount">{(id) => <Input id={id} inputMode="decimal" value={max} onChange={(e) => { setMax(e.target.value.replace(/[^0-9.]/g, "")); setPreview(null); }} />}</Field>
        </div>
        <Field label="Rule name (optional)">{(id) => <Input id={id} maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <div className="flex items-end"><Button type="submit" className="w-full sm:w-auto" disabled={!cat || !(text || direction || min || max)}>Preview matches</Button></div>
      </form>
      {err ? <div className="mt-3"><ErrorNote error={err} /></div> : null}
      {preview ? (
        <div className="mt-5 rounded-xl border border-rule bg-raised p-4">
          <p className="text-sm">
            Matches <span className="num font-medium">{preview.matching_transactions}</span> transactions
            {preview.locked_skipped ? `; ${preview.locked_skipped} you categorised by hand will not change` : ""}. Nothing has been changed yet.
          </p>
          <ul className="mt-2 text-sm">
            {preview.sample.slice(0, 8).map((s) => (
              <li key={s.id} className="flex justify-between gap-3 border-t border-rule py-1"><span className="truncate">{formatDate(s.date)} {s.description}</span><Amount value={s.amount} /></li>
            ))}
          </ul>
          <Button className="mt-3" variant="primary" onClick={save}>Save rule and apply to these</Button>
        </div>
      ) : null}
    </div>
  );
}

function InlineName({ value, label, onSave }: { value: string; label: string; onSave: (v: string) => Promise<unknown> }) {
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState(value);
  if (!editing) {
    return <button type="button" onClick={() => { setV(value); setEditing(true); }} className="rounded px-1 -mx-1 text-left hover:bg-sunken" title={`Rename ${label}`}>{value}</button>;
  }
  return (
    <form className="inline-flex items-center gap-1" onSubmit={async (e) => { e.preventDefault(); if (v.trim() && v.trim() !== value) await onSave(v.trim()); setEditing(false); }}>
      <input autoFocus aria-label={`New name for ${label}`} value={v} maxLength={80} onChange={(e) => setV(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") setEditing(false); }}
        className="w-40 rounded-md border border-ink bg-surface px-2 py-0.5 text-sm outline-none" />
      <button className="rounded-md px-2 py-0.5 text-xs font-medium hover:bg-sunken">Save</button>
    </form>
  );
}

function CategoryGrid({ cats, onChange }: { cats: Category[]; onChange: () => void }) {
  const [name, setName] = useState("");
  const [parent, setParent] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [showArchived, setShowArchived] = useState(false);
  const { data: spend } = useApi<Breakdown>(`/analytics/categories?start_date=${daysAgo(90)}&end_date_exclusive=${daysAgo(-1)}`);
  const spent = Object.fromEntries((spend?.data.categories ?? []).filter((c) => c.category_id).map((c) => [c.category_id!, c]));
  const visible = cats.filter((c) => showArchived || !c.archived);
  const roots = visible.filter((c) => !c.parent_id).sort((a, b) => Number(spent[b.id]?.amount ?? 0) - Number(spent[a.id]?.amount ?? 0) || a.name.localeCompare(b.name));
  const archivedCount = cats.filter((c) => c.archived).length;
  async function patch(id: string, body: Record<string, unknown>) {
    setErr(null);
    try { await api(`/categories/${id}`, { method: "PATCH", json: body }); onChange(); } catch (e2) { setErr(e2); }
  }
  async function add(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      const p = cats.find((c) => c.id === parent);
      await api("/categories", { method: "POST", json: { name, parent_id: parent || null, effect: p?.effect ?? "expense" } });
      setName("");
      onChange();
    } catch (e2) { setErr(e2); }
  }
  return (
    <div className="flex flex-col gap-5">
      <form onSubmit={add} className="grid grid-cols-1 items-end gap-3 rounded-xl border border-rule bg-surface p-4 sm:grid-cols-[1fr_1fr_auto]">
        <Field label="New category">{(id) => <Input id={id} required maxLength={80} placeholder="e.g. Pets" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Inside">{(id) => (
          <Select id={id} value={parent} onChange={(e) => setParent(e.target.value)}>
            <option value="">Top level</option>{roots.filter((r) => !r.archived).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </Select>
        )}</Field>
        <Button type="submit" disabled={!name.trim()}>Add category</Button>
      </form>
      {err ? <ErrorNote error={err} /> : null}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-ink-faint">
        <span>Sorted by spending in the last 90 days. Click a name to rename it. Everything counts as spending unless marked otherwise.</span>
        {archivedCount ? <button className="font-medium text-ink-soft underline-offset-4 hover:underline" onClick={() => setShowArchived(!showArchived)}>{showArchived ? "Hide archived" : `Show archived (${archivedCount})`}</button> : null}
      </div>
      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {roots.map((r) => {
          const subs = visible.filter((c) => c.parent_id === r.id).sort((x, y) => x.name.localeCompare(y.name));
          const sp = spent[r.id];
          return (
            <li key={r.id} className={cx("group flex flex-col rounded-xl border border-rule bg-surface p-4", r.archived && "opacity-60")}>
              <div className="flex items-start justify-between gap-2">
                <span className={cx("font-semibold", r.archived && "line-through")}><InlineName value={r.name} label={r.name} onSave={(v) => patch(r.id, { name: v })} /></span>
                <span className="flex items-center gap-1">
                  <button type="button" onClick={() => patch(r.id, { archived: !r.archived })} className="rounded-md px-1.5 py-0.5 text-xs text-ink-faint opacity-0 transition-opacity hover:bg-sunken hover:text-ink focus:opacity-100 group-hover:opacity-100">
                    {r.archived ? "Restore" : "Archive"}
                  </button>
                  {r.effect !== "expense" ? <Badge>{EFFECT_LABEL[r.effect]}</Badge> : null}
                </span>
              </div>
              <p className="mt-1 text-xs text-ink-faint">
                {sp && Number(sp.amount) ? <><span className="num font-medium text-ink-soft">{formatMoney(sp.amount)}</span> across {sp.count} transaction{sp.count === 1 ? "" : "s"} in 90 days</> : "Nothing in the last 90 days"}
              </p>
              {subs.length ? (
                <ul className="mt-3 flex flex-wrap gap-1.5">
                  {subs.map((c) => (
                    <li key={c.id} className={cx("group/sub inline-flex items-center rounded-md bg-sunken px-2 py-0.5 text-[13px]", c.archived && "line-through opacity-60")}>
                      <InlineName value={c.name} label={c.name} onSave={(v) => patch(c.id, { name: v })} />
                      <button type="button" onClick={() => patch(c.id, { archived: !c.archived })} aria-label={`${c.archived ? "Restore" : "Archive"} ${c.name}`} title={c.archived ? "Restore" : "Archive"}
                        className="ml-1 hidden rounded px-1 text-ink-faint hover:text-ink group-focus-within/sub:inline group-hover/sub:inline">{c.archived ? "↺" : "×"}</button>
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Merchants({ merchants, cats, onChange }: { merchants: Merchant[] | undefined; cats: Category[]; onChange: () => void }) {
  const [err, setErr] = useState<unknown>(null);
  const [q, setQ] = useState("");
  if (!merchants) return <Loading />;
  const list = merchants.filter((m) => m.category_id && m.name.toLowerCase().includes(q.toLowerCase()));
  async function setCat(m: Merchant, category_id: string | null) {
    setErr(null);
    try { await api("/merchants", { method: "POST", json: { name: m.name, category_id } }); onChange(); } catch (e) { setErr(e); }
  }
  return (
    <div className="flex flex-col gap-4">
      <p className="max-w-[70ch] text-sm text-ink-soft">When you tick &quot;Use this category for this merchant from now on&quot; on a transaction, the merchant is remembered here. Future transactions from it get the same category.</p>
      {err ? <ErrorNote error={err} /> : null}
      {merchants.filter((m) => m.category_id).length === 0 ? (
        <Empty title="No remembered merchants yet">Open a transaction, pick a category and tick &quot;Use this category for this merchant from now on&quot;.</Empty>
      ) : (
        <>
          <Input aria-label="Find a merchant" placeholder="Find a merchant" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-sm" />
          <ul className="divide-y divide-rule rounded-xl border border-rule bg-surface px-4">
            {list.map((m) => (
              <li key={m.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2.5 sm:flex-nowrap">
                <span aria-hidden className="inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-sunken text-sm font-semibold text-ink-soft">{m.name.charAt(0).toUpperCase()}</span>
                <span className="min-w-0 flex-1 truncate font-medium">{m.name}</span>
                <div className="flex w-full items-center gap-2 pl-11 sm:w-auto sm:pl-0">
                  <div className="min-w-0 flex-1 sm:w-64 sm:flex-none">
                    <Select aria-label={`Category for ${m.name}`} value={m.category_id ?? ""} onChange={(e) => setCat(m, e.target.value || null)}>
                      <CategoryOptions cats={cats} />
                    </Select>
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => setCat(m, null)}>Forget</Button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function Tags() {
  const { data, mutate } = useApi<{ id: string; name: string }[]>("/tags");
  const [name, setName] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  async function run(fn: () => Promise<unknown>) {
    setErr(null);
    try { await fn(); await mutate(); } catch (e) { setErr(e); }
  }
  return (
    <Panel>
      <p className="mb-4 text-sm text-ink-soft">Tags group transactions across categories, like a trip or a wedding. You can also add them from any transaction.</p>
      {data?.length ? (
        <ul className="flex flex-wrap gap-2">
          {data.map((t) => (
            <li key={t.id} className="group inline-flex items-center gap-0.5 rounded-full border border-rule py-0.5 pl-3 pr-1 text-sm">
              <InlineName value={t.name} label={`tag ${t.name}`} onSave={(v) => run(() => api(`/tags/${t.id}`, { method: "PATCH", json: { name: v } }))} />
              <Link href={`/transactions?tag_id=${t.id}`} className="rounded-full px-1.5 text-xs text-ink-faint hover:bg-sunken hover:text-ink" title={`Transactions tagged ${t.name}`}>View</Link>
              {confirm === t.id ? (
                <button onClick={() => run(() => api(`/tags/${t.id}`, { method: "DELETE" }))} className="rounded-full bg-debit-wash px-2 text-xs font-medium text-debit">Delete tag?</button>
              ) : (
                <button onClick={() => setConfirm(t.id)} aria-label={`Delete tag ${t.name}`} className="rounded-full px-1.5 text-ink-faint hover:bg-sunken hover:text-debit">×</button>
              )}
            </li>
          ))}
        </ul>
      ) : <p className="text-sm text-ink-soft">No tags yet.</p>}
      <form className="mt-4 flex gap-2" onSubmit={(e) => { e.preventDefault(); run(async () => { await api("/tags", { method: "POST", json: { name: name.trim() } }); setName(""); }); }}>
        <Input aria-label="New tag" placeholder="e.g. Goa trip" maxLength={50} value={name} onChange={(e) => setName(e.target.value)} />
        <Button type="submit" disabled={!name.trim()}>Add tag</Button>
      </form>
      {err ? <div className="mt-2"><ErrorNote error={err} /></div> : null}
    </Panel>
  );
}

type Suggestion = { transaction_id: string; date: string; amount: string; text: string; category_id: string; category_name: string; confidence: string };

function AiSuggestions({ cats }: { cats: Category[] }) {
  const { data: settings } = useApi<{ server_enabled: boolean; classification_enabled: boolean; share_descriptions: boolean }>("/ai/settings");
  const [items, setItems] = useState<Suggestion[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!settings?.server_enabled || !settings.classification_enabled || !settings.share_descriptions) return null;
  return (
    <Panel title="AI category suggestions">
      <p className="text-sm text-ink-soft">Sends the text of uncategorised transactions to your AI provider and shows suggestions. Nothing changes until you accept one.</p>
      <Button className="mt-3" size="sm" busy={busy} onClick={async () => {
        setBusy(true); setErr(null);
        try { setItems((await api<{ suggestions: Suggestion[] }>("/ai/suggest-categories", { method: "POST" })).suggestions); }
        catch (e) { setErr(e instanceof ApiError ? e.message : "Failed."); } finally { setBusy(false); }
      }}>Suggest categories</Button>
      {err ? <p className="mt-2 text-sm text-debit">{err}</p> : null}
      {items ? (
        <ul className="mt-3 divide-y divide-rule text-sm">
          {items.length === 0 ? <li className="py-2 text-ink-soft">No suggestions.</li> : items.map((s) => (
            <li key={s.transaction_id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span className="min-w-0"><span className="block truncate">{s.text}</span><span className="text-xs text-ink-faint">{formatDate(s.date)}, {s.confidence} confidence</span></span>
              <Button size="sm" onClick={async () => {
                await api(`/transactions/${s.transaction_id}/classify`, { method: "POST", json: { category_id: s.category_id } });
                setItems(items.filter((x) => x !== s));
              }}>Use {cats.find((c) => c.id === s.category_id)?.name ?? s.category_name}</Button>
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}

function Recategorize() {
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex flex-col items-end gap-1">
      <Button busy={busy} title="Applies your rules, merchants and built-in patterns again to everything you have not categorised by hand" onClick={async () => {
        setBusy(true);
        setMsg(null);
        try { const r = await api<{ checked: number; changed: number }>("/categories/recategorize", { method: "POST" }); setMsg(`Checked ${r.checked}; ${r.changed} changed.`); }
        finally { setBusy(false); }
      }}><Icon name="recurring" className="size-4" />Re-run categorisation</Button>
      {msg ? <p role="status" className="text-xs text-credit">{msg}</p> : null}
    </div>
  );
}
