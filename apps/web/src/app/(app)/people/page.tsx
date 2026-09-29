"use client";

import { useState, type FormEvent } from "react";
import { ledgerChanged } from "@/components/quick-add";
import { CategoryOptions, TxnList, TxnSheet, useCategories, type Txn } from "@/components/transactions";
import { Icon } from "@/components/icons";
import { Amount, Button, cx, Empty, ErrorNote, Field, Input, Loading, PageHeader, Panel, Select, Sheet } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { evaluateAmount } from "@/lib/calc";
import { formatDate, formatMoney, todayISO } from "@/lib/format";

type Person = { id: string; name: string; email: string | null; balance: string; owes_you: string; you_owe: string; currency: string; last_activity: string | null; archived: boolean };
type Detail = Person & { activity: Txn[] };
type Account = { id: string; name: string; kind: string; status: string };

function Standing({ p, big }: { p: Person; big?: boolean }) {
  const cls = big ? "display text-[1.8rem] font-medium leading-none" : "font-medium";
  if (Number(p.owes_you) > 0) return <span className={cx(cls, "text-credit")}>owes you <Amount value={p.owes_you} currency={p.currency} colored={false} signed={false} /></span>;
  if (Number(p.you_owe) > 0) return <span className={cx(cls, "text-debit")}>you owe <Amount value={p.you_owe} currency={p.currency} colored={false} signed={false} /></span>;
  return <span className={cx(cls, "text-ink-faint")}>settled up</span>;
}

export default function PeoplePage() {
  const { data, error, mutate } = useApi<Person[]>("/people");
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  if (error) return <ErrorNote error={error} onRetry={() => mutate()} />;
  if (!data) return <Loading />;
  const owed = data.reduce((a, p) => a + Number(p.owes_you), 0);
  const owe = data.reduce((a, p) => a + Number(p.you_owe), 0);
  return (
    <>
      <PageHeader title="Shared with people" description="Split a bill from any transaction. Their share is owed to you instead of counted as your spending, and settling up is never income." actions={<Button variant="primary" onClick={() => setAdding(true)}><Icon name="plus" className="size-4" />Add person</Button>} />
      {data.length === 0 ? (
        <Empty title="No one yet" action={<Button variant="primary" onClick={() => setAdding(true)}>Add someone</Button>}>
          Add the people you split bills with. Then open a transaction and choose <em>Split with people</em>. Everything stays in your own ledger.
        </Empty>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
          <Panel>
            <ul className="-my-2 flex flex-col divide-y divide-rule">
              {data.map((p) => (
                <li key={p.id}>
                  <button type="button" onClick={() => setOpen(p.id)} className="-mx-2 flex w-[calc(100%+1rem)] items-center gap-3 rounded-lg px-2 py-3 text-left hover:bg-sunken/60">
                    <span aria-hidden className="inline-flex size-10 shrink-0 items-center justify-center rounded-full bg-accent-wash font-semibold">{p.name.slice(0, 1).toUpperCase()}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{p.name}</span>
                      <span className="text-xs text-ink-faint">{p.last_activity ? `Last activity ${formatDate(p.last_activity)}` : "Nothing shared yet"}</span>
                    </span>
                    <span className="text-right text-sm"><Standing p={p} /></span>
                  </button>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel title="Overall">
            <dl className="flex flex-col gap-3 text-sm">
              <div className="flex justify-between"><dt className="text-ink-soft">Owed to you</dt><dd className="num font-medium text-credit">{formatMoney(owed.toFixed(2))}</dd></div>
              <div className="flex justify-between"><dt className="text-ink-soft">You owe</dt><dd className="num font-medium text-debit">{formatMoney(owe.toFixed(2))}</dd></div>
            </dl>
            <p className="mt-4 text-xs text-ink-faint">What people owe you counts in your net worth, like money in the bank.</p>
          </Panel>
        </div>
      )}
      <AddPerson open={adding} onClose={() => setAdding(false)} onDone={() => { setAdding(false); mutate(); }} />
      <PersonSheet id={open} onClose={() => setOpen(null)} onChanged={() => mutate()} />
    </>
  );
}

function AddPerson({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api("/people", { method: "POST", json: { name } });
      setName("");
      onDone();
    } catch (x) { setErr(x); } finally { setBusy(false); }
  }
  return (
    <Sheet open={open} onClose={onClose} title="Add a person">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field label="Name" hint="Only you see this. They are not invited or told anything.">{(id, d) => <Input id={id} aria-describedby={d} required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        {err ? <ErrorNote error={err} /> : null}
        <Button type="submit" variant="primary" busy={busy}>Add</Button>
      </form>
    </Sheet>
  );
}

function PersonSheet({ id, onClose, onChanged }: { id: string | null; onClose: () => void; onChanged: () => void }) {
  const { data: p, mutate } = useApi<Detail>(id ? `/people/${id}` : null);
  const { data: accounts } = useApi<Account[]>(id ? "/accounts" : null);
  const { data: cats } = useCategories();
  const [mode, setMode] = useState<"view" | "settle" | "paid">("view");
  const [amount, setAmount] = useState("");
  const [account, setAccount] = useState("");
  const [date, setDate] = useState(todayISO());
  const [direction, setDirection] = useState<"in" | "out">("in");
  const [desc, setDesc] = useState("");
  const [cat, setCat] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [txn, setTxn] = useState<string | null>(null);
  const liquid = (accounts ?? []).filter((a) => ["BANK_SAVINGS", "BANK_CURRENT", "CASH", "WALLET"].includes(a.kind) && a.status === "ACTIVE");

  function start(m: "settle" | "paid") {
    setMode(m);
    setErr(null);
    setDate(todayISO());
    if (m === "settle" && p) {
      setDirection(Number(p.you_owe) > 0 ? "out" : "in");
      setAmount(Number(p.owes_you) > 0 ? p.owes_you : Number(p.you_owe) > 0 ? p.you_owe : "");
      setAccount(liquid[0]?.id ?? "");
    } else {
      setAmount("");
      setDesc("");
      setCat("");
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const v = evaluateAmount(amount);
    if (!p || !v) return;
    setBusy(true);
    setErr(null);
    try {
      if (mode === "settle") {
        await api(`/people/${p.id}/settle`, { method: "POST", json: { account_id: account, amount: direction === "in" ? v : `-${v}`, transaction_date: date } });
      } else {
        await api(`/people/${p.id}/they-paid`, { method: "POST", json: { amount: v, transaction_date: date, description: desc, category_id: cat || null } });
      }
      await mutate();
      onChanged();
      ledgerChanged();
      setMode("view");
    } catch (x) { setErr(x); } finally { setBusy(false); }
  }

  return (
    <Sheet open={!!id} onClose={() => { setMode("view"); onClose(); }} title={p?.name ?? "Person"} wide>
      {!p ? <Loading /> : (
        <div className="flex flex-col gap-5">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <Standing p={p} big />
            {mode === "view" ? (
              <div className="flex gap-2">
                <Button size="sm" variant="primary" onClick={() => start("settle")}>Settle up</Button>
                <Button size="sm" onClick={() => start("paid")}>{p.name} paid for me</Button>
              </div>
            ) : null}
          </div>
          {mode !== "view" ? (
            <form onSubmit={submit} className="grid gap-4 rounded-xl border border-rule bg-raised p-4 sm:grid-cols-2">
              {mode === "settle" ? (
                <>
                  <fieldset className="grid grid-cols-2 gap-1 rounded-xl bg-sunken p-1 text-sm sm:col-span-2">
                    <legend className="sr-only">Who paid whom</legend>
                    {(["in", "out"] as const).map((d) => (
                      <label key={d} className="flex cursor-pointer items-center justify-center rounded-lg py-2 font-medium text-ink-soft has-[:checked]:bg-surface has-[:checked]:text-ink has-[:checked]:shadow-sm">
                        <input className="sr-only" type="radio" checked={direction === d} onChange={() => setDirection(d)} />{d === "in" ? `${p.name} paid me` : `I paid ${p.name}`}
                      </label>
                    ))}
                  </fieldset>
                  <Field label={direction === "in" ? "Into" : "From"}>{(fid) => (
                    <Select id={fid} required value={account} onChange={(e) => setAccount(e.target.value)}>
                      <option value="">Choose…</option>{liquid.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                    </Select>
                  )}</Field>
                </>
              ) : (
                <>
                  <Field label="What for">{(fid) => <Input id={fid} required maxLength={500} value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="e.g. Movie tickets" />}</Field>
                  <Field label="Category">{(fid) => (
                    <Select id={fid} value={cat} onChange={(e) => setCat(e.target.value)}>
                      <option value="">Choose…</option>{cats ? <CategoryOptions cats={cats.filter((c) => c.effect === "expense")} /> : null}
                    </Select>
                  )}</Field>
                </>
              )}
              <Field label={mode === "paid" ? "Your share" : "Amount"}>{(fid) => <Input id={fid} required inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^0-9.+\-*/()]/g, ""))} />}</Field>
              <Field label="Date">{(fid) => <Input id={fid} type="date" required max={todayISO()} value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
              {mode === "settle" ? <p className="text-xs text-ink-faint sm:col-span-2">Already on your bank statement? Open that transaction instead and use Split with people, or link it later; settling here adds the payment by hand.</p> : null}
              {err ? <div className="sm:col-span-2"><ErrorNote error={err} /></div> : null}
              <div className="flex gap-2 sm:col-span-2">
                <Button type="submit" variant="primary" busy={busy} disabled={!evaluateAmount(amount) || (mode === "settle" && !account)}>Save</Button>
                <Button type="button" variant="ghost" onClick={() => setMode("view")}>Cancel</Button>
              </div>
            </form>
          ) : null}
          <section>
            <h3 className="mb-1 text-sm font-semibold">Activity</h3>
            {p.activity.length ? (
              <div className="rounded-xl border border-rule px-4"><TxnList items={p.activity} compact onOpen={(t) => setTxn(t.id)} /></div>
            ) : <p className="text-sm text-ink-soft">Nothing shared with {p.name} yet.</p>}
          </section>
          <TxnSheet id={txn} onClose={() => setTxn(null)} onChanged={() => { mutate(); onChanged(); }} />
        </div>
      )}
    </Sheet>
  );
}
