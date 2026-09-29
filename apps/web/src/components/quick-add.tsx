"use client";

import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { mutate as mutateAll } from "swr";
import { api, useApi } from "@/lib/api";
import { evaluateAmount, isExpression } from "@/lib/calc";
import { formatMoney, todayISO } from "@/lib/format";
import { Icon } from "./icons";
import { CategoryOptions, useCategories, type Category } from "./transactions";
import { Button, cx, ErrorNote, Field, Input, Select, Sheet } from "./ui";

type Kind = "expense" | "income" | "transfer";
type Account = { id: string; name: string; kind: string; currency: string; status: string };
type Template = { id: string; name: string; kind: Kind; account_id: string | null; to_account_id: string | null; amount: string | null; category_id: string | null; description: string; use_count: number };

export type QuickAddPreset = Partial<{ kind: Kind; account_id: string; to_account_id: string; amount: string; description: string; category_id: string }>;

const KINDS: { key: Kind; label: string }[] = [
  { key: "expense", label: "Expense" },
  { key: "income", label: "Income" },
  { key: "transfer", label: "Transfer" },
];

function yesterday(): string {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

/** Tell every open list and total that the ledger changed. */
export function ledgerChanged() {
  void mutateAll(() => true, undefined, { revalidate: true });
  if (typeof window !== "undefined") window.dispatchEvent(new Event("ol:ledger-changed"));
}

function categoriesFor(cats: Category[], kind: Kind): Category[] {
  const want = kind === "income" ? "income" : "expense";
  const roots = new Set(cats.filter((c) => !c.parent_id && c.effect === want).map((c) => c.id));
  return cats.filter((c) => (c.parent_id ? roots.has(c.parent_id) : roots.has(c.id)));
}

export function QuickAdd({ open, onClose, preset }: { open: boolean; onClose: () => void; preset?: QuickAddPreset }) {
  const { data: accountsAll } = useApi<Account[]>(open ? "/accounts" : null);
  const { data: cats } = useCategories();
  const { data: templates, mutate: mutateTemplates } = useApi<Template[]>(open ? "/templates" : null);
  const accounts = useMemo(() => (accountsAll ?? []).filter((a) => a.status === "ACTIVE" && a.kind !== "LOAN"), [accountsAll]);
  const [kind, setKind] = useState<Kind>("expense");
  const [account, setAccount] = useState("");
  const [toAccount, setToAccount] = useState("");
  const [amountText, setAmountText] = useState("");
  const [date, setDate] = useState(todayISO());
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("");
  const [saveTemplate, setSaveTemplate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const amountRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setKind(preset?.kind ?? "expense");
    setAccount(preset?.account_id ?? "");
    setToAccount(preset?.to_account_id ?? "");
    setAmountText(preset?.amount ?? "");
    setDescription(preset?.description ?? "");
    setCategory(preset?.category_id ?? "");
    setDate(todayISO());
    setErr(null);
    setSaved(null);
    setSaveTemplate(false);
  }, [open, preset]);

  // Default to the account you use for cash, then the first bank account.
  useEffect(() => {
    if (!open || account || !accounts.length) return;
    const cash = accounts.find((a) => a.kind === "CASH");
    setAccount((cash ?? accounts[0])!.id);
  }, [open, account, accounts]);

  const amount = evaluateAmount(amountText);
  const expression = amountText && isExpression(amountText);
  const currency = accounts.find((a) => a.id === account)?.currency ?? "INR";
  const catChoices = cats ? categoriesFor(cats, kind) : [];
  const valid = !!amount && !!account && (kind === "transfer" ? !!toAccount && toAccount !== account : !!description.trim());

  function applyTemplate(t: Template) {
    setKind(t.kind);
    if (t.account_id) setAccount(t.account_id);
    setToAccount(t.to_account_id ?? "");
    setAmountText(t.amount ?? "");
    setCategory(t.category_id ?? "");
    setDescription(t.description);
    void api(`/templates/${t.id}/used`, { method: "POST" }).then(() => mutateTemplates());
    setTimeout(() => amountRef.current?.focus(), 0);
  }

  async function save(e: FormEvent | null, again: boolean) {
    e?.preventDefault();
    if (!valid || !amount) return;
    setBusy(true);
    setErr(null);
    setSaved(null);
    try {
      if (kind === "transfer") {
        await api("/transfers/manual", {
          method: "POST",
          headers: { "idempotency-key": crypto.randomUUID() },
          json: { from_account_id: account, to_account_id: toAccount, amount, transaction_date: date, description: description.trim() },
        });
      } else {
        await api("/transactions", {
          method: "POST",
          headers: { "idempotency-key": crypto.randomUUID() },
          json: { account_id: account, amount: kind === "expense" ? `-${amount}` : amount, transaction_date: date, description: description.trim(), category_id: category || null },
        });
      }
      if (saveTemplate) {
        await api("/templates", {
          method: "POST",
          json: { name: (description.trim() || (kind === "transfer" ? "Transfer" : "Entry")).slice(0, 80), kind, account_id: account, to_account_id: kind === "transfer" ? toAccount : null, amount, category_id: kind === "transfer" ? null : category || null, description: description.trim() },
        });
        void mutateTemplates();
      }
      ledgerChanged();
      if (again) {
        setSaved(`Saved ${formatMoney(amount, currency)}. Add the next one.`);
        setAmountText("");
        setDescription("");
        setSaveTemplate(false);
        amountRef.current?.focus();
      } else {
        onClose();
      }
    } catch (x) {
      setErr(x);
    } finally {
      setBusy(false);
    }
  }

  function press(op: string) {
    setAmountText((t) => (op === "=" ? evaluateAmount(t) ?? t : `${t}${op}`));
    amountRef.current?.focus();
  }

  return (
    <Sheet open={open} onClose={onClose} title="Add">
      <form onSubmit={(e) => save(e, false)} className="flex flex-col gap-4">
        <div role="tablist" aria-label="What are you adding?" className="grid grid-cols-3 gap-1 rounded-xl bg-sunken p-1 text-sm">
          {KINDS.map((k) => (
            <button key={k.key} type="button" role="tab" aria-selected={kind === k.key} onClick={() => { setKind(k.key); setCategory(""); }}
              className={cx("rounded-lg py-2 font-medium transition-colors", kind === k.key ? cx("bg-surface shadow-sm", k.key === "expense" ? "text-debit" : k.key === "income" ? "text-credit" : "text-ink") : "text-ink-soft hover:text-ink")}>
              {k.label}
            </button>
          ))}
        </div>

        {templates && templates.length ? (
          <div>
            <p className="mb-1.5 text-xs font-medium text-ink-faint">Saved</p>
            <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1 [scrollbar-width:thin]">
              {templates.slice(0, 12).map((t) => (
                <button key={t.id} type="button" onClick={() => applyTemplate(t)}
                  className="shrink-0 rounded-full border border-rule bg-surface px-3 py-1 text-sm text-ink-soft hover:border-ink-faint hover:text-ink">
                  {t.name}{t.amount ? <span className="num ml-1.5 text-ink-faint">{formatMoney(t.amount, "INR", { decimals: false })}</span> : null}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        <Field label="Amount" hint={expression ? (amount ? <>= <span className="num font-medium text-ink">{formatMoney(amount, currency)}</span></> : "Check the sum") : "You can type a sum, like 120+45"}>
          {(id, d) => (
            <div>
              <Input id={id} ref={amountRef} aria-describedby={d} required inputMode="decimal" autoComplete="off" value={amountText}
                onChange={(e) => setAmountText(e.target.value.replace(/[^0-9.+\-*/() ,]/g, ""))}
                onBlur={() => { if (expression && amount) setAmountText(amount); }}
                className="display h-14 text-[1.7rem]" placeholder="0" />
              <div className="mt-1.5 grid grid-cols-5 gap-1 sm:hidden" aria-label="Calculator keys">
                {["+", "-", "*", "/", "="].map((op) => (
                  <button key={op} type="button" onClick={() => press(op)} className="rounded-lg bg-sunken py-2 text-lg font-medium text-ink-soft active:bg-rule" aria-label={{ "+": "Plus", "-": "Minus", "*": "Times", "/": "Divided by", "=": "Work out" }[op]}>
                    {{ "*": "×", "/": "÷", "-": "−" }[op] ?? op}
                  </button>
                ))}
              </div>
            </div>
          )}
        </Field>

        {kind === "transfer" ? (
          <div className="grid grid-cols-2 gap-3">
            <Field label="From">{(id) => (
              <Select id={id} required value={account} onChange={(e) => setAccount(e.target.value)}>
                <option value="">Choose…</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            )}</Field>
            <Field label="To" error={toAccount && toAccount === account ? "Choose a different account" : null}>{(id) => (
              <Select id={id} required value={toAccount} onChange={(e) => setToAccount(e.target.value)}>
                <option value="">Choose…</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            )}</Field>
          </div>
        ) : (
          <Field label={kind === "expense" ? "Paid from" : "Received in"}>{(id) => (
            <Select id={id} required value={account} onChange={(e) => setAccount(e.target.value)}>
              <option value="">Choose…</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          )}</Field>
        )}

        <div className="flex flex-col gap-1">
          <label htmlFor="qa-date" className="text-sm font-medium text-ink-soft">Date</label>
          <div className="flex gap-2">
            <Input id="qa-date" type="date" required value={date} max={todayISO()} onChange={(e) => setDate(e.target.value)} className="flex-1" />
            <Button type="button" size="sm" variant={date === todayISO() ? "secondary" : "ghost"} className="min-h-10" onClick={() => setDate(todayISO())}>Today</Button>
            <Button type="button" size="sm" variant={date === yesterday() ? "secondary" : "ghost"} className="min-h-10" onClick={() => setDate(yesterday())}>Yesterday</Button>
          </div>
        </div>

        <Field label={kind === "transfer" ? "Note (optional)" : "What was it?"}>
          {(id) => <Input id={id} required={kind !== "transfer"} maxLength={500} value={description} onChange={(e) => setDescription(e.target.value)} placeholder={kind === "expense" ? "e.g. Vegetables" : kind === "income" ? "e.g. Freelance payment" : "e.g. Cash for the week"} />}
        </Field>

        {kind !== "transfer" ? (
          <Field label="Category">{(id) => (
            <Select id={id} value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">Categorise automatically</option>
              {cats ? <CategoryOptions cats={catChoices} /> : null}
            </Select>
          )}</Field>
        ) : <p className="-mt-1 text-xs text-ink-faint">Moving money between your own accounts is never counted as income or spending.</p>}

        <label className="flex items-center gap-2 text-sm text-ink-soft">
          <input type="checkbox" checked={saveTemplate} onChange={(e) => setSaveTemplate(e.target.checked)} />
          Save for next time
        </label>

        {saved ? <p role="status" className="rounded-lg bg-credit-wash px-3.5 py-2 text-sm text-credit">{saved}</p> : null}
        {err ? <ErrorNote error={err} /> : null}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" busy={busy} disabled={!valid} className="flex-1">
            <Icon name="plus" className="size-4" />Save
          </Button>
          <Button type="button" busy={busy} disabled={!valid} onClick={() => save(null, true)}>Save and add another</Button>
        </div>
      </form>
    </Sheet>
  );
}
