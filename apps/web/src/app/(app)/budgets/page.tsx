"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { CategoryOptions, useCategories } from "@/components/transactions";
import { Amount, Button, cx, Empty, ErrorNote, Field, Input, Loading, Meter, PageHeader, Panel, Select, Sheet } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { categoryColor } from "@/lib/categories";
import { formatDate, formatMoney, formatMonth, todayISO } from "@/lib/format";

type Budget = { id: string; name: string; amount: string; carried_over: string; available: string; spent: string; remaining: string; percent: string; over: boolean; transaction_count: number; category_id: string | null; rollover: boolean; version: number; period_start: string; period_end_exclusive: string };
type Goal = { id: string; name: string; target_amount: string; current: string; percent: string; target_date: string | null; progress_mode: string; monthly_needed: string | null; partial: boolean; version: number; contributions: { id: string; date: string; amount: string; note: string | null }[] };
type Suggestion = { category_id: string; name: string; average: string; suggested: string; months_with_spending: number };

function Ring({ pct, label }: { pct: number; label: string }) {
  const r = 26;
  const c = 2 * Math.PI * r;
  const done = Math.min(Math.max(pct, 0), 100);
  return (
    <svg viewBox="0 0 64 64" className="size-16 shrink-0" role="img" aria-label={label}>
      <circle cx="32" cy="32" r={r} fill="none" stroke="var(--sunken)" strokeWidth="7" />
      <circle cx="32" cy="32" r={r} fill="none" stroke={done >= 100 ? "var(--credit)" : "var(--cat-1)"} strokeWidth="7" strokeLinecap="round"
        strokeDasharray={`${(done / 100) * c} ${c}`} transform="rotate(-90 32 32)" />
      <text x="32" y="36" textAnchor="middle" fontSize="13" fontWeight="600" fill="var(--ink)">{Math.round(pct)}%</text>
    </svg>
  );
}

function BudgetRow({ b, code, onChanged }: { b: Budget; code: string | null; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [amount, setAmount] = useState(b.amount);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const pct = Number(b.percent);
  async function patch(body: Record<string, unknown>) {
    setErr(null);
    try { await api(`/budgets/${b.id}`, { method: "PATCH", json: { version: b.version, ...body } }); onChanged(); } catch (x) { setErr(x); }
  }
  return (
    <li className="py-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2 font-medium"><span aria-hidden className="size-2.5 shrink-0 rounded-sm" style={{ background: categoryColor(code) }} /><span className="truncate">{b.name}</span></span>
        <span className="num text-sm"><Amount value={b.spent} colored={false} signed={false} /> <span className="text-ink-faint">of</span> <Amount value={b.available} colored={false} signed={false} /></span>
      </div>
      <div className="mt-1.5"><Meter value={Number(b.spent)} max={Number(b.available)} tone={b.over ? "debit" : pct >= 90 ? "review" : "credit"} label={`${b.name}: ${b.percent}% used`} /></div>
      <p className={cx("mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs", b.over ? "text-debit" : "text-ink-faint")}>
        <span>{b.over ? <>Over by <Amount value={b.remaining.replace("-", "")} colored={false} signed={false} /></> : <><Amount value={b.remaining} colored={false} signed={false} /> left</>}, {b.transaction_count} {b.transaction_count === 1 ? "transaction" : "transactions"}</span>
        {b.rollover && Number(b.carried_over) ? <span className={b.carried_over.startsWith("-") ? "text-debit" : "text-credit"}>{b.carried_over.startsWith("-") ? "−" : "+"}{formatMoney(b.carried_over.replace("-", ""), "INR", { decimals: false })} carried from earlier months</span> : null}
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-soft">
        <label className="inline-flex items-center gap-1.5"><input type="checkbox" checked={b.rollover} onChange={(e) => patch({ rollover: e.target.checked })} />Carry over what&apos;s left</label>
        <button className="underline underline-offset-2 hover:text-ink" onClick={() => setEditing(!editing)}>Change amount</button>
        {confirmRemove ? (
          <span className="inline-flex items-center gap-2">Remove this budget?
            <button className="font-medium text-debit underline" onClick={() => api(`/budgets/${b.id}`, { method: "DELETE" }).then(onChanged)}>Remove</button>
            <button className="underline" onClick={() => setConfirmRemove(false)}>Keep</button>
          </span>
        ) : <button className="underline underline-offset-2 hover:text-debit" onClick={() => setConfirmRemove(true)}>Remove</button>}
      </div>
      {editing ? (
        <form className="mt-2 flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); void patch({ amount }).then(() => setEditing(false)); }}>
          <Field label="Monthly amount">{(id) => <Input id={id} inputMode="decimal" required value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ""))} className="w-36" />}</Field>
          <Button type="submit" size="sm" className="min-h-10">Save</Button>
        </form>
      ) : null}
      {err ? <div className="mt-2"><ErrorNote error={err} /></div> : null}
    </li>
  );
}

function Suggestions({ onAdded }: { onAdded: () => void }) {
  const { data, mutate } = useApi<Suggestion[]>("/budgets/suggestions");
  const [busy, setBusy] = useState<string | null>(null);
  if (!data || !data.length) return null;
  async function addOne(s: Suggestion) {
    await api("/budgets", { method: "POST", json: { name: s.name, category_id: s.category_id, amount: s.suggested, start_date: `${todayISO().slice(0, 7)}-01` } });
  }
  return (
    <Panel title="Suggested from your last three months" className="mb-6" action={
      <Button size="sm" busy={busy === "all"} onClick={async () => { setBusy("all"); for (const s of data.slice(0, 6)) await addOne(s); setBusy(null); mutate(); onAdded(); }}>Add the top {Math.min(6, data.length)}</Button>
    }>
      <ul className="grid gap-x-6 sm:grid-cols-2">
        {data.slice(0, 8).map((s) => (
          <li key={s.category_id} className="flex items-center justify-between gap-3 border-b border-rule py-2 text-sm">
            <span className="min-w-0"><span className="block truncate font-medium">{s.name}</span><span className="text-xs text-ink-faint">averaged <span className="num">{formatMoney(s.average, "INR", { decimals: false })}</span> a month</span></span>
            <Button size="sm" variant="ghost" busy={busy === s.category_id} onClick={async () => { setBusy(s.category_id); await addOne(s); setBusy(null); mutate(); onAdded(); }}>
              Add <span className="num">{formatMoney(s.suggested, "INR", { decimals: false })}</span>
            </Button>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function GoalRow({ g, onChanged, onAdd }: { g: Goal; onChanged: () => void; onAdd: () => void }) {
  const [editing, setEditing] = useState(false);
  const [f, setF] = useState({ name: g.name, target_amount: g.target_amount, target_date: g.target_date ?? "" });
  const [err, setErr] = useState<unknown>(null);
  return (
    <li className="py-3">
      <div className="flex items-center gap-4">
        <Ring pct={Number(g.percent)} label={`${g.name}: ${g.percent}% reached`} />
        <div className="min-w-0 flex-1">
          <p className="font-medium">{g.name}</p>
          <p className="text-sm"><Amount value={g.current} colored={false} signed={false} /> <span className="text-ink-faint">of</span> <Amount value={g.target_amount} colored={false} signed={false} /></p>
          <p className="text-xs text-ink-faint">
            {g.target_date ? `By ${formatDate(g.target_date)}` : "No date set"}
            {g.monthly_needed ? <>, about <Amount value={g.monthly_needed} colored={false} signed={false} /> a month to get there</> : null}
            {g.partial ? ", some balances unknown" : ""}
          </p>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 pl-20 text-xs text-ink-soft">
        {g.progress_mode === "MANUAL" ? <button className="font-medium text-ink underline underline-offset-2" onClick={onAdd}>Add money</button> : <span>Follows the balance of the accounts you chose</span>}
        <button className="underline underline-offset-2 hover:text-ink" onClick={() => setEditing(!editing)}>Edit</button>
        <button className="underline underline-offset-2 hover:text-debit" onClick={() => api(`/goals/${g.id}`, { method: "DELETE" }).then(onChanged)}>Delete</button>
      </div>
      {g.contributions.length ? (
        <ul className="mt-2 pl-20 text-xs text-ink-faint">
          {g.contributions.slice(0, 3).map((c) => <li key={c.id}>{formatDate(c.date)}: <span className="num">{formatMoney(c.amount)}</span>{c.note ? `, ${c.note}` : ""}</li>)}
        </ul>
      ) : null}
      {editing ? (
        <form className="mt-3 grid gap-2 pl-20 sm:grid-cols-3" onSubmit={async (e) => {
          e.preventDefault();
          setErr(null);
          try {
            await api(`/goals/${g.id}`, { method: "PATCH", json: { version: g.version, name: f.name, target_amount: f.target_amount, target_date: f.target_date || null, clear_target_date: !f.target_date } });
            setEditing(false);
            onChanged();
          } catch (x) { setErr(x); }
        }}>
          <Field label="Name">{(id) => <Input id={id} required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
          <Field label="Target">{(id) => <Input id={id} required inputMode="decimal" value={f.target_amount} onChange={(e) => setF({ ...f, target_amount: e.target.value.replace(/[^0-9.]/g, "") })} />}</Field>
          <Field label="By">{(id) => <Input id={id} type="date" value={f.target_date} onChange={(e) => setF({ ...f, target_date: e.target.value })} />}</Field>
          <div className="sm:col-span-3"><Button type="submit" size="sm">Save</Button></div>
          {err ? <div className="sm:col-span-3"><ErrorNote error={err} /></div> : null}
        </form>
      ) : null}
    </li>
  );
}

function BudgetsInner() {
  const params = useSearchParams();
  const router = useRouter();
  const month = params.get("month") ?? todayISO().slice(0, 7);
  const setMonth = (m: string) => router.replace(`/budgets?month=${m}`, { scroll: false });
  const { data, error, mutate } = useApi<{ data: { budgets: Budget[] }; provenance: { warnings: string[] } }>(`/budgets?month=${month}`);
  const { data: goals, mutate: mutGoals } = useApi<Goal[]>("/goals");
  const { data: cats } = useCategories();
  const [adding, setAdding] = useState<"budget" | "goal" | null>(null);
  const [contrib, setContrib] = useState<Goal | null>(null);
  if (error) return <ErrorNote error={error} />;
  if (!data || !goals) return <Loading />;
  const budgets = data.data.budgets;
  const codeOf = (id: string | null) => {
    let c = cats?.find((x) => x.id === id);
    while (c?.parent_id) c = cats?.find((x) => x.id === c!.parent_id);
    return c?.code ?? null;
  };
  const totalAvail = budgets.reduce((a, b) => a + Number(b.available), 0);
  const totalSpent = budgets.reduce((a, b) => a + Number(b.spent), 0);
  return (
    <>
      <PageHeader title="Budgets & goals" description="Budgets count spending net of refunds in each category, including its sub-categories. Turn on carry-over to move what's left into next month."
        actions={<><Button onClick={() => setAdding("goal")}>Add goal</Button><Button variant="primary" onClick={() => setAdding("budget")}>Add budget</Button></>} />
      <Suggestions onAdded={() => mutate()} />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel title={`Budgets for ${formatMonth(`${month}-01`)}`} action={<Input type="month" aria-label="Month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} className="w-auto" />}>
          {budgets.length === 0 ? <Empty title="No budgets yet" action={<Button onClick={() => setAdding("budget")}>Add a budget</Button>}>Start with the categories you want to watch.</Empty> : (
            <>
              <div className="mb-2 rounded-lg bg-sunken/60 px-3 py-2 text-sm">
                <span className="num font-medium">{formatMoney(totalSpent.toFixed(2), "INR", { decimals: false })}</span> spent of <span className="num">{formatMoney(totalAvail.toFixed(2), "INR", { decimals: false })}</span> budgeted
              </div>
              <ul className="flex flex-col divide-y divide-rule">
                {budgets.map((b) => <BudgetRow key={`${b.id}:${b.version}`} b={b} code={codeOf(b.category_id)} onChanged={() => mutate()} />)}
              </ul>
            </>
          )}
          {data.provenance.warnings.map((w) => <p key={w} className="mt-3 text-xs text-review">{w}</p>)}
        </Panel>
        <Panel title="Goals">
          {goals.length === 0 ? <Empty title="No goals yet" action={<Button onClick={() => setAdding("goal")}>Add a goal</Button>}>An emergency fund, a trip, a down payment: set a target and see how much a month gets you there.</Empty> : (
            <ul className="flex flex-col divide-y divide-rule">
              {goals.map((g) => <GoalRow key={`${g.id}:${g.version}`} g={g} onChanged={() => mutGoals()} onAdd={() => setContrib(g)} />)}
            </ul>
          )}
        </Panel>
      </div>
      <Sheet open={adding === "budget"} onClose={() => setAdding(null)} title="Add budget"><BudgetForm onDone={() => { setAdding(null); mutate(); }} /></Sheet>
      <Sheet open={adding === "goal"} onClose={() => setAdding(null)} title="Add goal"><GoalForm onDone={() => { setAdding(null); mutGoals(); }} /></Sheet>
      <Sheet open={!!contrib} onClose={() => setContrib(null)} title={`Add to ${contrib?.name ?? ""}`}>{contrib ? <ContribForm goal={contrib} onDone={() => { setContrib(null); mutGoals(); }} /> : null}</Sheet>
    </>
  );
}

export default function BudgetsPage() {
  return <Suspense fallback={<Loading />}><BudgetsInner /></Suspense>;
}

function BudgetForm({ onDone }: { onDone: () => void }) {
  const { data: cats } = useCategories();
  const [f, setF] = useState({ name: "", category_id: "", amount: "", start: todayISO().slice(0, 7), rollover: false });
  const [err, setErr] = useState<unknown>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    const name = f.name || cats?.find((c) => c.id === f.category_id)?.name || "Budget";
    try { await api("/budgets", { method: "POST", json: { name, category_id: f.category_id || null, amount: f.amount, start_date: `${f.start}-01`, rollover: f.rollover } }); onDone(); } catch (e2) { setErr(e2); }
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <Field label="Category">{(id) => <Select id={id} value={f.category_id} onChange={(e) => setF({ ...f, category_id: e.target.value })}><option value="">All spending</option>{cats ? <CategoryOptions cats={cats.filter((c) => c.effect === "expense")} /> : null}</Select>}</Field>
      <Field label="Monthly amount">{(id) => <Input id={id} required inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />}</Field>
      <Field label="Starting from" hint="Pick an earlier month to see how past months compare.">{(id, d) => <Input id={id} aria-describedby={d} type="month" required value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} />}</Field>
      <label className="flex items-center gap-2 text-sm text-ink-soft"><input type="checkbox" checked={f.rollover} onChange={(e) => setF({ ...f, rollover: e.target.checked })} />Carry what&apos;s left (or overspent) into the next month</label>
      <Field label="Name (optional)">{(id) => <Input id={id} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
      {err ? <ErrorNote error={err} /> : null}
      <Button type="submit" variant="primary">Add budget</Button>
    </form>
  );
}

function GoalForm({ onDone }: { onDone: () => void }) {
  const { data: accounts } = useApi<{ id: string; name: string }[]>("/accounts");
  const [f, setF] = useState({ name: "", target_amount: "", target_date: "", progress_mode: "MANUAL", account_ids: [] as string[] });
  const [err, setErr] = useState<unknown>(null);
  return (
    <form className="flex flex-col gap-3" onSubmit={async (e) => { e.preventDefault(); try { await api("/goals", { method: "POST", json: { ...f, target_date: f.target_date || null } }); onDone(); } catch (e2) { setErr(e2); } }}>
      <Field label="Goal">{(id) => <Input id={id} required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Emergency fund" />}</Field>
      <Field label="Target amount">{(id) => <Input id={id} required inputMode="decimal" value={f.target_amount} onChange={(e) => setF({ ...f, target_amount: e.target.value })} />}</Field>
      <Field label="By (optional)">{(id) => <Input id={id} type="date" value={f.target_date} onChange={(e) => setF({ ...f, target_date: e.target.value })} />}</Field>
      <Field label="Track progress by">{(id) => <Select id={id} value={f.progress_mode} onChange={(e) => setF({ ...f, progress_mode: e.target.value, account_ids: [] })}><option value="MANUAL">Amounts I add</option><option value="ACCOUNTS">Balance of chosen accounts</option></Select>}</Field>
      {f.progress_mode === "ACCOUNTS" ? (
        <fieldset className="flex flex-col gap-1 text-sm"><legend className="mb-1 font-medium">Accounts</legend>
          {accounts?.map((a) => <label key={a.id} className="flex items-center gap-2"><input type="checkbox" checked={f.account_ids.includes(a.id)} onChange={(e) => setF({ ...f, account_ids: e.target.checked ? [...f.account_ids, a.id] : f.account_ids.filter((x) => x !== a.id) })} />{a.name}</label>)}
        </fieldset>
      ) : null}
      {err ? <ErrorNote error={err} /> : null}
      <Button type="submit" variant="primary">Add goal</Button>
    </form>
  );
}

function ContribForm({ goal, onDone }: { goal: Goal; onDone: () => void }) {
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [date, setDate] = useState(todayISO());
  const [err, setErr] = useState<unknown>(null);
  return (
    <form className="flex flex-col gap-3" onSubmit={async (e) => { e.preventDefault(); try { await api(`/goals/${goal.id}/contributions`, { method: "POST", json: { amount, contribution_date: date, note: note || null } }); onDone(); } catch (e2) { setErr(e2); } }}>
      <Field label="Amount">{(id) => <Input id={id} required inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />}</Field>
      <Field label="Date">{(id) => <Input id={id} type="date" max={todayISO()} value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
      <Field label="Note (optional)">{(id) => <Input id={id} maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
      {err ? <ErrorNote error={err} /> : null}
      <Button type="submit" variant="primary">Add</Button>
    </form>
  );
}
