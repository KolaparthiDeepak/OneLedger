"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ApiError, useApi } from "@/lib/api";
import { categoryColor } from "@/lib/categories";
import { EFFECT_LABEL, formatDate, formatMoney } from "@/lib/format";
import { Icon } from "./icons";
import { Amount, Badge, Button, cx, ErrorNote, Field, Input, Select, Sheet } from "./ui";

export type Allocation = {
  id: string;
  amount: string;
  effect: string;
  category: { id: string; code: string; name: string; parent_name: string | null } | null;
  classification_source: string;
  confidence: string | null;
  locked: boolean;
  rule?: string | null;
  model?: string | null;
  note: string | null;
  transfer_id: string | null;
};

/** Plain-language reason for a category, e.g. 'By your rule "Power bill"'. */
export function categoryReason(a: Allocation): string {
  switch (a.classification_source) {
    case "USER": return "Set by you";
    case "RULE": return a.rule ? `By your rule "${a.rule}"` : "By one of your rules";
    case "MERCHANT": return "Remembered for this merchant";
    case "PATTERN": return "Recognised from the description";
    case "TRANSFER_MATCH": return "Matched as a transfer between your accounts";
    case "AI": return `Suggested by AI${a.model ? ` (${a.model.split("/").pop()})` : ""}, ${Number(a.confidence ?? 0) >= 0.9 ? "high" : "medium"} confidence`;
    case "FALLBACK": return "Not categorised yet";
    case "SYSTEM": return a.category?.code?.startsWith("LOANS") ? "Split by OneLedger from your loan's EMI schedule (an estimate until you enter the lender's figures)" : "Set by OneLedger";
    default: return "Set by OneLedger";
  }
}

export type Txn = {
  id: string;
  account: { id: string; name: string; kind: string };
  amount: string;
  currency: string;
  direction: string;
  transaction_date: string;
  status: string;
  channel: string;
  source: string;
  description: string;
  merchant: string | null;
  reference: string | null;
  notes: string | null;
  allocations: Allocation[];
  is_split: boolean;
  is_transfer: boolean;
  tags: { id: string; name: string }[];
  recurring: boolean;
  anomaly: boolean;
  needs_review: boolean;
  deleted: boolean;
  version: number;
};

export type Category = { id: string; code: string; name: string; parent_id: string | null; effect: string; archived: boolean };

export function useCategories() {
  return useApi<Category[]>("/categories", { dedupingInterval: 300_000 });
}

export function CategoryOptions({ cats }: { cats: Category[] }) {
  const roots = cats.filter((c) => !c.parent_id && !c.archived).sort((a, b) => a.name.localeCompare(b.name));
  return (
    <>
      {roots.map((r) => (
        <optgroup key={r.id} label={r.name}>
          <option value={r.id}>{r.name}</option>
          {cats.filter((c) => c.parent_id === r.id && !c.archived).map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </optgroup>
      ))}
    </>
  );
}

function categoryLabel(t: Txn): string {
  if (t.is_split) return `Split into ${t.allocations.length}`;
  const a = t.allocations[0];
  if (!a) return "";
  if (a.category) return a.category.parent_name ? `${a.category.parent_name} / ${a.category.name}` : a.category.name;
  return EFFECT_LABEL[a.effect] ?? a.effect;
}

function dayLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

function Monogram({ t }: { t: Txn }) {
  const name = (t.merchant ?? t.description).replace(/[^A-Za-z0-9 ]/g, " ").trim();
  const letter = name ? name[0]!.toUpperCase() : "·";
  const tone = t.is_transfer ? "bg-accent-wash text-ink-soft" : t.amount.startsWith("-") ? "bg-sunken text-ink-soft" : "bg-credit-wash text-credit";
  return <span aria-hidden className={cx("mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold", tone)}>{t.is_transfer ? "⇄" : letter}</span>;
}

export type DayTotal = { money_in: string; spent: string };

export function TxnList({ items, onOpen, compact, selected, onToggle, dayTotals }: { items: Txn[]; onOpen?: (t: Txn) => void; compact?: boolean; selected?: Set<string>; onToggle?: (t: Txn) => void; dayTotals?: Record<string, DayTotal> }) {
  const rows: React.ReactNode[] = [];
  let lastDay = "";
  for (const t of items) {
    if (!compact && t.transaction_date !== lastDay) {
      lastDay = t.transaction_date;
      const tot = dayTotals?.[t.transaction_date];
      rows.push(
        <li key={`d-${t.transaction_date}`} className="sticky top-[49px] z-[1] -mx-4 flex items-baseline justify-between gap-3 bg-raised/95 px-4 py-1.5 text-xs font-medium text-ink-soft backdrop-blur first:rounded-t-xl sm:-mx-6 sm:px-6 lg:top-0">
          <span>{dayLabel(t.transaction_date)}</span>
          {tot ? (
            <span className="num flex gap-3 font-normal">
              {Number(tot.money_in) ? <span className="text-credit">+{formatMoney(tot.money_in, "INR", { decimals: false })}</span> : null}
              {Number(tot.spent) ? <span className="text-debit">−{formatMoney(tot.spent, "INR", { decimals: false })}</span> : null}
            </span>
          ) : null}
        </li>,
      );
    }
    const uncategorised = t.allocations.some((a) => a.effect === "unclassified");
    const picking = !!onToggle;
    const inner = (
      <div className="flex items-start gap-3 py-3">
        {picking ? (
          <span aria-hidden className={cx("mt-1.5 inline-flex size-6 shrink-0 items-center justify-center rounded-md border transition-colors", selected?.has(t.id) ? "border-ink bg-accent text-accent-ink" : "border-rule-strong bg-surface")}>
            {selected?.has(t.id) ? <svg viewBox="0 0 20 20" className="size-4" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 10.5l3.2 3.2L15 7" /></svg> : null}
          </span>
        ) : <Monogram t={t} />}
        <div className="min-w-0 flex-1">
          <p className={cx("line-clamp-2 break-words font-medium sm:line-clamp-1", t.deleted && "text-ink-faint line-through")}>{t.merchant ?? t.description}</p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px] text-ink-faint">
            {compact ? <span>{formatDate(t.transaction_date, false)}</span> : null}
            <span>{t.account.name}</span>
            <span className={cx("inline-flex items-center gap-1.5", uncategorised && "font-medium text-review")}>
              {!uncategorised && !t.is_split ? <span aria-hidden className="inline-block size-2 rounded-full" style={{ background: categoryColor(t.allocations[0]?.category?.code) }} /> : null}
              {uncategorised ? "Not categorised" : categoryLabel(t)}
            </span>
            {t.is_transfer ? <Badge>Transfer</Badge> : null}
            {t.status === "PENDING" ? <Badge tone="review">Pending</Badge> : null}
            {t.needs_review ? <Badge tone="review">Review</Badge> : null}
            {t.anomaly ? <Badge tone="debit">Unusual</Badge> : null}
            {t.recurring && !compact ? <Badge>Recurring</Badge> : null}
            {t.tags.map((g) => <span key={g.id} className="rounded-full border border-rule px-2 text-xs leading-5 text-ink-soft">#{g.name}</span>)}
          </p>
        </div>
        <Amount value={t.amount} currency={t.currency} className="shrink-0 pt-px font-medium" colored={!t.is_transfer} />
      </div>
    );
    rows.push(
      <li key={t.id} className="border-b border-rule last:border-0">
        {onToggle ? (
          <button role="checkbox" aria-checked={selected?.has(t.id) ?? false} aria-label={`Select ${t.merchant ?? t.description}`} className={cx("-mx-2 block w-[calc(100%+1rem)] rounded-lg px-2 text-left transition-colors hover:bg-sunken/60", selected?.has(t.id) && "bg-accent-wash/60")} onClick={() => onToggle(t)}>{inner}</button>
        ) : onOpen ? (
          <button className="-mx-2 block w-[calc(100%+1rem)] rounded-lg px-2 text-left transition-colors hover:bg-sunken/60" onClick={() => onOpen(t)}>{inner}</button>
        ) : (
          <Link href={`/transactions?open=${t.id}`} className="-mx-2 block rounded-lg px-2 transition-colors hover:bg-sunken/60">{inner}</Link>
        )}
      </li>,
    );
  }
  return <ul className="flex flex-col">{rows}</ul>;
}

type Origin = { import_id: string; filename: string; file_deleted: boolean; row_number: number; columns: [string, string][]; balance_after: string | null; imported_at: string };

type Detail = Txn & {
  origin: Origin[];
  sources: { provider: string; has_provider_id: boolean; active?: boolean; created_at: string }[];
  revisions: { revision: number; reason: string; actor: string; created_at: string; before: Record<string, string>; after: Record<string, string> }[];
  relations: { id: string; kind: string; from: string; to: string; amount: string | null; undone: boolean }[];
  merged_into_id: string | null;
};

export function TxnSheet({ id, onClose, onChanged }: { id: string | null; onClose: () => void; onChanged: () => void }) {
  const { data: t, error, mutate } = useApi<Detail>(id ? `/transactions/${id}` : null);
  const { data: cats } = useCategories();
  const { data: tags, mutate: mutateTags } = useApi<{ id: string; name: string }[]>("/tags");
  const [mode, setMode] = useState<"view" | "split" | "correct">("view");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<unknown>, done?: string) {
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      await fn();
      await mutate();
      onChanged();
      if (done) setMsg(done);
      setMode("view");
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet open={!!id} onClose={onClose} title="Transaction" wide>
      {error ? <ErrorNote error={error} /> : !t || !cats ? <p className="text-ink-faint">Loading…</p> : (
        <div className="flex flex-col gap-5">
          <div className="flex items-start gap-4">
            <div className="min-w-0 flex-1">
              <p className="display text-[2.4rem] font-medium leading-none"><Amount value={t.amount} currency={t.currency} colored={!t.is_transfer} /></p>
              <p className="mt-3 break-words font-medium">{t.merchant ?? t.description}</p>
              {t.merchant ? <p className="mt-0.5 break-words text-sm text-ink-soft">{t.description}</p> : null}
              <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-[13px]">
                <div><dt className="sr-only">Date</dt><dd>{formatDate(t.transaction_date)}</dd></div>
                <div><dt className="sr-only">Account</dt><dd className="text-ink-soft">{t.account.name}</dd></div>
                {t.channel !== "OTHER" ? <div><dt className="sr-only">Paid by</dt><dd className="text-ink-soft">{CHANNEL[t.channel] ?? t.channel}</dd></div> : null}
                {t.reference ? <div><dt className="inline text-ink-faint">Ref </dt><dd className="num inline text-ink-soft">{t.reference}</dd></div> : null}
              </dl>
            </div>
          </div>
          {msg ? <p role="status" className="rounded-lg bg-credit-wash px-3.5 py-2.5 text-sm text-credit">{msg}</p> : null}
          {err ? <ErrorNote error={err} /> : null}

          {mode === "view" ? (
            <>
              <div className="rounded-xl border border-rule bg-raised p-4">
                <Classify t={t} cats={cats} busy={busy} onSave={(body, done) => run(() => api(`/transactions/${t.id}/classify`, { method: "POST", json: body }), done)} />
              </div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" onClick={() => setMode("split")}>Split</Button>
                {t.source !== "MANUAL" ? <Button size="sm" onClick={() => setMode("correct")}>Correct details</Button> : null}
                {t.allocations.some((a) => a.locked) ? (
                  <Button size="sm" variant="ghost" onClick={() => run(() => api(`/transactions/${t.id}/unlock`, { method: "POST" }), "Automatic categorisation restored.")}>Let rules decide</Button>
                ) : null}
                {t.source === "MANUAL" || t.source === "MANUAL_DERIVED" ? (
                  t.deleted ? <Button size="sm" onClick={() => run(() => api(`/transactions/${t.id}/restore`, { method: "POST" }), "Restored.")}>Restore</Button>
                    : <Button size="sm" variant="danger" onClick={() => run(() => api(`/transactions/${t.id}`, { method: "DELETE" }), "Deleted. You can restore it from this screen.")}>Delete</Button>
                ) : null}
              </div>
              <div className="divide-y divide-rule rounded-xl border border-rule">
                <Annotate key={t.version} t={t} tags={tags ?? []} busy={busy} onTagCreated={() => mutateTags()} onSave={(body) => run(() => api(`/transactions/${t.id}`, { method: "PATCH", json: body }), "Notes and tags saved.")} />
                <Receipts t={t} />
                <ShareWith t={t} busy={busy} run={run} />
                <StatementRow origin={t.origin ?? []} />
                <Pairing t={t} busy={busy} run={run} />
                <Evidence t={t} />
              </div>
            </>
          ) : mode === "split" ? (
            <Split t={t} cats={cats} busy={busy} onCancel={() => setMode("view")} onSave={(parts) => run(() => api(`/transactions/${t.id}/split`, { method: "POST", json: { version: t.version, parts } }), "Split saved.")} />
          ) : (
            <Correct t={t} busy={busy} onCancel={() => setMode("view")} onSave={(body) => run(() => api(`/transactions/${t.id}/correct`, { method: "POST", json: { version: t.version, ...body } }), "Correction saved with an audit record.")} />
          )}
        </div>
      )}
    </Sheet>
  );
}

function Classify({ t, cats, busy, onSave }: { t: Detail; cats: Category[]; busy: boolean; onSave: (body: unknown, done: string) => void }) {
  const a = t.allocations[0];
  const [cat, setCat] = useState(a?.category?.id ?? "");
  const [merchant, setMerchant] = useState(t.merchant ?? "");
  const [remember, setRemember] = useState(false);
  if (t.is_split) {
    return (
      <div>
        <h3 className="mb-2 text-sm font-medium text-ink-soft">Split into</h3>
        <ul className="text-sm">
          {t.allocations.map((x) => (
            <li key={x.id} className="flex justify-between border-b border-rule py-1.5">
              <span>{x.category?.name ?? EFFECT_LABEL[x.effect]}{x.note ? `, ${x.note}` : ""}</span>
              <Amount value={x.amount} currency={t.currency} colored={false} />
            </li>
          ))}
        </ul>
      </div>
    );
  }
  return (
    <form className="grid grid-cols-1 gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); onSave({ category_id: cat || null, merchant_name: merchant || null, remember_merchant: remember }, "Category saved."); }}>
      <Field label="Category" hint={a ? `${EFFECT_LABEL[a.effect]}. ${categoryReason(a)}${a.locked && a.classification_source !== "USER" ? ", kept as you set it" : ""}${a.transfer_id ? ". Part of a confirmed transfer" : ""}.` : undefined}>
        {(id, d) => (
          <Select id={id} aria-describedby={d} value={cat} onChange={(e) => setCat(e.target.value)} disabled={!!a?.transfer_id}>
            <option value="">Choose…</option>
            <CategoryOptions cats={cats} />
          </Select>
        )}
      </Field>
      <Field label="Merchant">
        {(id) => <Input id={id} value={merchant} maxLength={120} onChange={(e) => setMerchant(e.target.value)} />}
      </Field>
      <label className="flex items-center gap-2 text-sm text-ink-soft sm:col-span-2">
        <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
        Use this category for this merchant from now on
      </label>
      <div className="sm:col-span-2">
        <Button type="submit" variant="primary" busy={busy} disabled={!cat || !!a?.transfer_id}>Save category</Button>
      </div>
    </form>
  );
}

function Annotate({ t, tags, busy, onSave, onTagCreated }: { t: Detail; tags: { id: string; name: string }[]; busy: boolean; onSave: (b: unknown) => void; onTagCreated: () => void }) {
  const [notes, setNotes] = useState(t.notes ?? "");
  const [sel, setSel] = useState<string[]>(t.tags.map((x) => x.id));
  const [newTag, setNewTag] = useState("");
  const [tagErr, setTagErr] = useState<string | null>(null);
  async function addTag() {
    const name = newTag.trim();
    if (!name) return;
    setTagErr(null);
    const existing = tags.find((x) => x.name.toLowerCase() === name.toLowerCase());
    if (existing) { setSel((cur) => (cur.includes(existing.id) ? cur : [...cur, existing.id])); setNewTag(""); return; }
    try {
      const created = await api<{ id: string }>("/tags", { method: "POST", json: { name } });
      setSel((cur) => [...cur, created.id]);
      setNewTag("");
      onTagCreated();
    } catch (e) { setTagErr(errorMessage(e)); }
  }
  const dirty = notes !== (t.notes ?? "") || sel.slice().sort().join() !== t.tags.map((x) => x.id).sort().join();
  return (
    <details className="group px-4" open={t.tags.length > 0 || !!t.notes}>
      <Summary title="Notes and tags" hint={t.notes || t.tags.length ? [t.tags.length ? `${t.tags.length} tag${t.tags.length > 1 ? "s" : ""}` : "", t.notes ? "has a note" : ""].filter(Boolean).join(", ") : undefined} />
      <div className="flex flex-col gap-4 pb-4">
        <fieldset>
          <legend className="mb-2 text-sm font-medium text-ink-soft">Tags</legend>
          <div className="flex flex-wrap items-center gap-2">
            {tags.map((tag) => (
              <label key={tag.id} className="flex cursor-pointer items-center rounded-full border border-rule px-3 py-1 text-sm transition-colors hover:border-ink-faint has-[:checked]:border-ink has-[:checked]:bg-accent-wash has-[:checked]:font-medium">
                <input type="checkbox" className="sr-only" checked={sel.includes(tag.id)} onChange={(e) => setSel(e.target.checked ? [...sel, tag.id] : sel.filter((x) => x !== tag.id))} />
                {tag.name}
              </label>
            ))}
            <span className="inline-flex items-center gap-1 rounded-full border border-dashed border-rule-strong py-0.5 pl-3 pr-1">
              <input aria-label="New tag" placeholder="New tag" maxLength={50} value={newTag} onChange={(e) => setNewTag(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void addTag(); } }}
                className="w-24 bg-transparent text-sm outline-none placeholder:text-ink-faint" />
              <button type="button" onClick={() => void addTag()} disabled={!newTag.trim()} className="rounded-full px-2 py-0.5 text-sm font-medium text-ink-soft hover:bg-sunken disabled:opacity-40">Add</button>
            </span>
          </div>
          {tagErr ? <p className="mt-1 text-xs text-debit">{tagErr}</p> : null}
        </fieldset>
        <Field label="Note">{(id) => <textarea id={id} rows={2} placeholder="Anything you want to remember about this" className="min-h-16 rounded-lg border border-rule-strong/80 bg-surface p-3 focus:border-ink focus:outline-none" value={notes} maxLength={1000} onChange={(e) => setNotes(e.target.value)} />}</Field>
        <Button size="sm" className="self-start" busy={busy} disabled={!dirty} onClick={() => onSave({ version: t.version, notes, tag_ids: sel })}>Save notes and tags</Button>
      </div>
    </details>
  );
}

function StatementRow({ origin }: { origin: Origin[] }) {
  if (!origin.length) return null;
  const o = origin[0]!;
  return (
    <details className="group px-4 text-sm">
      <Summary title="From your statement" hint={`${o.filename}, row ${o.row_number}`} />
      <div className="flex flex-col gap-3 pb-4">
        {origin.map((row) => (
          <div key={`${row.import_id}-${row.row_number}`} className="overflow-hidden rounded-lg border border-rule">
            <dl className="divide-y divide-rule">
              {row.columns.filter(([, v]) => v !== "" && v != null).map(([k, v]) => (
                <div key={k} className="grid grid-cols-[minmax(7rem,35%)_1fr] gap-3 px-3 py-1.5">
                  <dt className="text-ink-faint">{k}</dt>
                  <dd className="num break-words">{String(v)}</dd>
                </div>
              ))}
            </dl>
            <p className="flex flex-wrap justify-between gap-2 bg-raised px-3 py-1.5 text-xs text-ink-faint">
              <span>Row {row.row_number} of <Link className="underline underline-offset-2 hover:text-ink" href={`/imports/${row.import_id}`}>{row.filename}</Link></span>
              <span>Imported {formatDate(String(row.imported_at).slice(0, 10))}</span>
            </p>
          </div>
        ))}
        {origin.length > 1 ? <p className="text-xs text-ink-faint">The same row appeared in {origin.length} files; it was added once and linked to the others.</p> : null}
      </div>
    </details>
  );
}

function Pairing({ t, busy, run }: { t: Detail; busy: boolean; run: (fn: () => Promise<unknown>, done?: string) => void }) {
  const [other, setOther] = useState("");
  const [kind, setKind] = useState<"transfer" | "refund" | "merge">("transfer");
  const { data } = useApi<{ items: Txn[] }>(`/transactions?limit=20&start_date=${shift(t.transaction_date, -45)}&end_date_exclusive=${shift(t.transaction_date, 45)}${kind === "merge" ? `&account_id=${t.account.id}` : ""}`);
  const candidates = (data?.items ?? []).filter((x) => x.id !== t.id && (kind === "merge" ? x.amount === t.amount : kind === "transfer" ? x.amount.replace("-", "") === t.amount.replace("-", "") && x.account.id !== t.account.id : isOpposite(t.amount, x.amount)));
  const action = () => {
    if (kind === "transfer") return run(() => api(`/transactions/${t.id}/mark-transfer`, { method: "POST", json: { counterpart_transaction_id: other } }), "Linked as a transfer between your accounts.");
    if (kind === "merge") return run(() => api("/transactions/merge", { method: "POST", json: { survivor_id: t.id, duplicate_id: other } }), "Duplicate merged. You can undo this from the transaction's history.");
    const [purchase, refund] = t.amount.startsWith("-") ? [t.id, other] : [other, t.id];
    return run(() => api("/transactions/link-refund", { method: "POST", json: { original_id: purchase, related_id: refund } }), "Refund linked.");
  };
  return (
    <details className="group px-4">
      <Summary title="Link to another transaction" hint="Transfer, refund or duplicate" />
      <div className="flex flex-col gap-3 pb-4">
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Link type">
          {(["transfer", "refund", "merge"] as const).map((k) => (
            <label key={k} className="flex items-center gap-1.5 text-sm">
              <input type="radio" name="link" checked={kind === k} onChange={() => { setKind(k); setOther(""); }} />
              {k === "transfer" ? "Transfer between my accounts" : k === "refund" ? "Refund of a purchase" : "Duplicate of this"}
            </label>
          ))}
        </div>
        <Select aria-label="Other transaction" value={other} onChange={(e) => setOther(e.target.value)}>
          <option value="">{candidates.length ? "Choose a transaction…" : "No matching candidates within 45 days"}</option>
          {candidates.map((c) => (
            <option key={c.id} value={c.id}>{formatDate(c.transaction_date)} {c.account.name}: {c.merchant ?? c.description} ({formatMoney(c.amount, c.currency, { signed: true })})</option>
          ))}
        </Select>
        <Button size="sm" className="self-start" busy={busy} disabled={!other} onClick={action}>Link</Button>
      </div>
    </details>
  );
}

const CHANNEL: Record<string, string> = { UPI: "UPI", CARD: "Card", NEFT: "NEFT", IMPS: "IMPS", RTGS: "RTGS", ATM: "ATM", CASH: "Cash", CHEQUE: "Cheque", NACH: "Auto-debit (NACH)", NETBANKING: "Net banking", INTERNAL: "Internal", OTHER: "Other" };

function Summary({ title, hint }: { title: string; hint?: string }) {
  return (
    <summary className="flex min-h-12 items-center gap-2 text-sm font-medium">
      <svg aria-hidden="true" viewBox="0 0 20 20" className="chev size-4 text-ink-faint" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M8 5l5 5-5 5" /></svg>
      <span className="flex-1">{title}</span>
      {hint ? <span className="text-xs font-normal text-ink-faint">{hint}</span> : null}
    </summary>
  );
}

function isOpposite(a: string, b: string) {
  return a.startsWith("-") !== b.startsWith("-");
}

function shift(iso: string, days: number) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function Evidence({ t }: { t: Detail }) {
  return (
    <details className="group px-4 text-sm">
      <Summary title="History" hint={t.revisions.length ? `${t.revisions.length} change${t.revisions.length > 1 ? "s" : ""}` : "No changes"} />
      <ul className="flex flex-col gap-1.5 pb-4 text-ink-soft">
        <li>Added {t.source === "MANUAL" ? "by hand" : t.source === "MANUAL_DERIVED" ? "automatically (the other side of a loan EMI, cash withdrawal or shared bill)" : "from a statement"} on {formatDate((t.sources[0]?.created_at ?? "").slice(0, 10))}</li>
        {t.revisions.map((r) => (
          <li key={r.revision}>Revision {r.revision}: {r.reason} ({Object.keys(r.after).filter((k) => r.after[k] !== r.before[k]).join(", ")})</li>
        ))}
        {t.relations.map((r) => (
          <li key={r.id} className="flex items-center gap-2">
            {r.kind.toLowerCase()} {r.undone ? "(undone)" : ""}
            {r.kind === "MERGE" && !r.undone ? <UndoMerge id={r.id} /> : null}
          </li>
        ))}
      </ul>
    </details>
  );
}

function UndoMerge({ id }: { id: string }) {
  const [state, setState] = useState<"idle" | "done" | "error">("idle");
  return state === "done" ? <span>undone</span> : (
    <button className="underline" onClick={() => api(`/relations/${id}/undo`, { method: "POST" }).then(() => setState("done")).catch(() => setState("error"))}>
      {state === "error" ? "Undo failed" : "Undo merge"}
    </button>
  );
}

function Split({ t, cats, busy, onCancel, onSave }: { t: Detail; cats: Category[]; busy: boolean; onCancel: () => void; onSave: (parts: unknown[]) => void }) {
  const neg = t.amount.startsWith("-");
  const abs = t.amount.replace("-", "");
  const [parts, setParts] = useState(t.allocations.length > 1
    ? t.allocations.map((a) => ({ amount: a.amount.replace("-", ""), category_id: a.category?.id ?? "", note: a.note ?? "" }))
    : [{ amount: abs, category_id: t.allocations[0]?.category?.id ?? "", note: "" }, { amount: "", category_id: "", note: "" }]);
  // Remaining shown for guidance; the server validates the exact decimal sum.
  const cents = (s: string) => Math.round(Number(s || "0") * 100);
  const remaining = (cents(abs) - parts.reduce((a, p) => a + cents(p.amount), 0)) / 100;
  const effectOf = (id: string) => cats.find((c) => c.id === id)?.effect ?? "unclassified";
  return (
    <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); onSave(parts.filter((p) => p.amount).map((p) => ({ amount: neg ? `-${p.amount}` : p.amount, category_id: p.category_id || null, effect: effectOf(p.category_id), note: p.note || null }))); }}>
      {parts.map((p, i) => (
        <div key={i} className="grid grid-cols-[1fr_1.4fr] gap-2 sm:grid-cols-[8rem_1fr_1fr_auto]">
          <Input aria-label={`Amount ${i + 1}`} inputMode="decimal" value={p.amount} onChange={(e) => setParts(parts.map((x, j) => (j === i ? { ...x, amount: e.target.value.replace(/[^0-9.]/g, "") } : x)))} />
          <Select aria-label={`Category ${i + 1}`} value={p.category_id} onChange={(e) => setParts(parts.map((x, j) => (j === i ? { ...x, category_id: e.target.value } : x)))}>
            <option value="">Uncategorised</option>
            <CategoryOptions cats={cats} />
          </Select>
          <Input aria-label={`Note ${i + 1}`} placeholder="Note" value={p.note} className="col-span-2 sm:col-span-1" onChange={(e) => setParts(parts.map((x, j) => (j === i ? { ...x, note: e.target.value } : x)))} />
          <Button type="button" variant="ghost" size="sm" onClick={() => setParts(parts.filter((_, j) => j !== i))} disabled={parts.length <= 1}>Remove</Button>
        </div>
      ))}
      <p className={cx("num text-sm", remaining === 0 ? "text-credit" : "text-review")}>
        {remaining === 0 ? "Splits add up to the transaction amount." : `${remaining > 0 ? "Remaining" : "Over by"}: ${Math.abs(remaining).toFixed(2)}`}
      </p>
      <div className="flex gap-2">
        <Button type="button" size="sm" onClick={() => setParts([...parts, { amount: remaining > 0 ? remaining.toFixed(2) : "", category_id: "", note: "" }])}>Add split</Button>
        <Button type="submit" variant="primary" size="sm" busy={busy} disabled={remaining !== 0}>Save split</Button>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

function Correct({ t, busy, onCancel, onSave }: { t: Detail; busy: boolean; onCancel: () => void; onSave: (b: Record<string, string>) => void }) {
  const [amount, setAmount] = useState(t.amount);
  const [date, setDate] = useState(t.transaction_date);
  const [reason, setReason] = useState("");
  return (
    <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); const b: Record<string, string> = { reason }; if (amount !== t.amount) b.amount = amount; if (date !== t.transaction_date) b.transaction_date = date; onSave(b); }}>
      <p className="text-sm text-ink-soft">Corrections keep the original statement row and record who changed what, and why.</p>
      <Field label="Amount (negative for money out)">{(id) => <Input id={id} value={amount} inputMode="decimal" onChange={(e) => setAmount(e.target.value)} />}</Field>
      <Field label="Date">{(id) => <Input id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
      <Field label="Reason">{(id) => <Input id={id} required minLength={3} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
      <div className="flex gap-2">
        <Button type="submit" variant="primary" size="sm" busy={busy} disabled={reason.length < 3}>Save correction</Button>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

export function errorMessage(e: unknown) {
  return e instanceof ApiError ? e.message : "Something went wrong.";
}


type Attachment = { id: string; filename: string; content_type: string; size_bytes: number; created_at: string };

function Receipts({ t }: { t: Detail }) {
  const { data, mutate } = useApi<Attachment[]>(`/transactions/${t.id}/attachments`);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  async function upload(file: File) {
    setBusy(true);
    setErr(null);
    try {
      const form = new FormData();
      form.append("file", file);
      await api(`/transactions/${t.id}/attachments`, { method: "POST", body: form });
      await mutate();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  async function remove(id: string) {
    await api(`/attachments/${id}`, { method: "DELETE" });
    await mutate();
  }
  const n = data?.length ?? 0;
  return (
    <details className="group px-4 text-sm" open={n > 0}>
      <Summary title="Receipts" hint={n ? `${n} attached` : "Photo or PDF"} />
      <div className="flex flex-col gap-3 pb-4">
        {n ? (
          <ul className="grid grid-cols-3 gap-2 sm:grid-cols-4">
            {data!.map((a) => (
              <li key={a.id} className="group/att relative overflow-hidden rounded-lg border border-rule bg-sunken">
                <a href={`/api/bff/attachments/${a.id}`} target="_blank" rel="noreferrer" className="block aspect-square" title={a.filename}>
                  {a.content_type.startsWith("image/") && a.content_type !== "image/heic" ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={`/api/bff/attachments/${a.id}`} alt={`Receipt ${a.filename}`} className="size-full object-cover" loading="lazy" />
                  ) : (
                    <span className="flex size-full flex-col items-center justify-center gap-1 p-2 text-center text-xs text-ink-soft"><Icon name="paperclip" className="size-5" /><span className="line-clamp-2 break-all">{a.filename}</span></span>
                  )}
                </a>
                <button type="button" onClick={() => remove(a.id)} aria-label={`Remove ${a.filename}`} className="absolute right-1 top-1 inline-flex size-7 items-center justify-center rounded-md bg-surface/90 text-ink-soft shadow-sm hover:text-debit">
                  <Icon name="close" className="size-4" />
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <label className={cx("inline-flex w-fit cursor-pointer items-center gap-2 rounded-lg border border-dashed border-rule-strong px-3 py-2 text-sm text-ink-soft hover:border-ink-faint hover:text-ink", busy && "opacity-60")}>
          <Icon name="paperclip" className="size-4" />{busy ? "Uploading…" : "Attach a receipt"}
          <input type="file" accept="image/jpeg,image/png,image/webp,image/heic,application/pdf" capture="environment" className="sr-only" disabled={busy}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
        </label>
        <p className="text-xs text-ink-faint">Stored encrypted with your ledger, up to 5 MB each.</p>
        {err ? <ErrorNote error={err} /> : null}
      </div>
    </details>
  );
}

type Person = { id: string; name: string; balance: string };

/** Split a payment with people: their shares are owed to you instead of counted as your spending. */
function ShareWith({ t, busy, run }: { t: Detail; busy: boolean; run: (fn: () => Promise<unknown>, done?: string) => void }) {
  const { data: people, mutate: mutatePeople } = useApi<Person[]>("/people");
  const shared = t.allocations.some((a) => a.category?.code === "TRANSFERS_SHARED" && a.amount.startsWith("-"));
  const eligible = t.amount.startsWith("-") && !t.is_transfer && (!t.is_split || shared) && t.allocations.every((a) => a.effect === "expense" || a.effect === "unclassified" || shared);
  const total = t.amount.replace("-", "");
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [includeMe, setIncludeMe] = useState(true);
  const [newName, setNewName] = useState("");
  if (!eligible) return null;
  const ids = Object.keys(picked);
  function evenSplit(next: Record<string, string>, me: boolean) {
    const n = Object.keys(next).length + (me ? 1 : 0);
    if (!n) return next;
    const cents = Math.round(Number(total) * 100);
    const each = Math.floor(cents / n);
    return Object.fromEntries(Object.keys(next).map((k) => [k, (each / 100).toFixed(2)]));
  }
  function toggle(id: string) {
    const next = { ...picked };
    if (id in next) delete next[id];
    else next[id] = "";
    setPicked(evenSplit(next, includeMe));
  }
  async function addPerson() {
    const name = newName.trim();
    if (!name) return;
    const p = await api<Person>("/people", { method: "POST", json: { name } });
    setNewName("");
    await mutatePeople();
    setPicked(evenSplit({ ...picked, [p.id]: "" }, includeMe));
  }
  const sum = ids.reduce((acc, k) => acc + Math.round(Number(picked[k] || 0) * 100), 0);
  const over = sum > Math.round(Number(total) * 100);
  return (
    <details className="group px-4 text-sm">
      <Summary title="Split with people" hint={shared ? "Shared" : "Who owes you for this?"} />
      <div className="flex flex-col gap-3 pb-4">
        {shared ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-ink-soft">This payment is shared. Only your part counts as spending; the rest is owed to you.</p>
            <Button size="sm" variant="ghost" busy={busy} onClick={() => run(() => api(`/transactions/${t.id}/share`, { method: "DELETE" }), "No longer shared.")}>Stop sharing</Button>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap gap-1.5">
              {(people ?? []).map((p) => (
                <button key={p.id} type="button" aria-pressed={p.id in picked} onClick={() => toggle(p.id)}
                  className={cx("rounded-full border px-3 py-1", p.id in picked ? "border-ink bg-accent text-accent-ink" : "border-rule bg-surface text-ink-soft hover:border-ink-faint")}>
                  {p.name}
                </button>
              ))}
              <span className="inline-flex items-center gap-1">
                <input aria-label="Add a person" placeholder="Add a person" maxLength={120} value={newName} onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void addPerson(); } }}
                  className="h-8 w-32 rounded-full border border-rule bg-surface px-3 text-sm focus:border-ink focus:outline-none" />
                {newName.trim() ? <Button size="sm" type="button" onClick={addPerson}>Add</Button> : null}
              </span>
            </div>
            {ids.length ? (
              <>
                <label className="flex items-center gap-2 text-ink-soft">
                  <input type="checkbox" checked={includeMe} onChange={(e) => { setIncludeMe(e.target.checked); setPicked(evenSplit(picked, e.target.checked)); }} />
                  Split equally including me
                </label>
                <ul className="flex flex-col gap-2">
                  {ids.map((id) => (
                    <li key={id} className="flex items-center gap-3">
                      <span className="flex-1">{people?.find((p) => p.id === id)?.name}</span>
                      <Input aria-label={`Share for ${people?.find((p) => p.id === id)?.name}`} inputMode="decimal" className="w-32 text-right" value={picked[id]}
                        onChange={(e) => setPicked({ ...picked, [id]: e.target.value.replace(/[^0-9.]/g, "") })} />
                    </li>
                  ))}
                </ul>
                <p className={cx("text-xs", over ? "text-debit" : "text-ink-faint")}>
                  Your part: {formatMoney((Math.max(0, Math.round(Number(total) * 100) - sum) / 100).toFixed(2), t.currency)} of {formatMoney(total, t.currency)}
                  {over ? ". Shares add up to more than the payment." : ""}
                </p>
                <Button size="sm" variant="primary" busy={busy} disabled={over || sum <= 0}
                  onClick={() => run(() => api(`/transactions/${t.id}/share`, { method: "POST", json: { shares: ids.filter((k) => Number(picked[k]) > 0).map((k) => ({ person_id: k, amount: Number(picked[k]).toFixed(2) })) } }), "Shared. What they owe you is on the People page.")}>
                  Save split
                </Button>
              </>
            ) : <p className="text-xs text-ink-faint">Pick who shared this. Their part is not counted as your spending, and shows as owed to you.</p>}
          </>
        )}
      </div>
    </details>
  );
}
