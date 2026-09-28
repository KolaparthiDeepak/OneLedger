"use client";

import { useState, type FormEvent } from "react";
import { CategoryOptions, useCategories } from "@/components/transactions";
import { Amount, Button, ErrorNote, Field, Input, Loading, PageHeader, Panel, Select, Sheet, cx } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { formatMonth } from "@/lib/format";

type Budget = { id: string; name: string; amount: string; spent: string; remaining: string; percent: string; over: boolean; transaction_count: number; category_id: string | null; version: number };
type Goal = { id: string; name: string; target_amount: string; current: string; percent: string; target_date: string | null; progress_mode: string; monthly_needed: string | null; partial: boolean };

export default function BudgetsPage() {
  const now = new Date().toISOString().slice(0, 7);
  const [month, setMonth] = useState(now);
  const { data, error, mutate } = useApi<{ data: { budgets: Budget[] }; provenance: { warnings: string[] } }>(`/budgets?month=${month}`);
  const { data: goals, mutate: mutGoals } = useApi<Goal[]>("/goals");
  const [adding, setAdding] = useState<"budget" | "goal" | null>(null);
  const [contrib, setContrib] = useState<Goal | null>(null);
  if (error) return <ErrorNote error={error} />;
  if (!data || !goals) return <Loading />;
  return (
    <>
      <PageHeader title="Budgets & goals" description="Budgets count spending net of refunds in each category, including its sub-categories. Unused amounts do not roll over."
        actions={<><Button onClick={() => setAdding("goal")}>Add goal</Button><Button variant="primary" onClick={() => setAdding("budget")}>Add budget</Button></>} />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel title={`Budgets for ${formatMonth(month)}`} action={<Input type="month" aria-label="Month" value={month} onChange={(e) => setMonth(e.target.value)} className="w-auto" />}>
          {data.data.budgets.length === 0 ? <p className="text-sm text-ink-soft">No budgets yet.</p> : (
            <ul className="flex flex-col gap-4">
              {data.data.budgets.map((b) => {
                const pct = Math.min(Number(b.percent), 100);
                return (
                  <li key={b.id}>
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="font-medium">{b.name}</span>
                      <span className="text-sm"><Amount value={b.spent} colored={false} signed={false} /> of <Amount value={b.amount} colored={false} signed={false} /></span>
                    </div>
                    <div className="mt-1 h-2 rounded-full bg-sunken" role="progressbar" aria-valuenow={Number(b.percent)} aria-valuemin={0} aria-valuemax={100} aria-label={`${b.name} used`}>
                      <div className={cx("h-2 rounded-full", b.over ? "bg-debit" : Number(b.percent) > 85 ? "bg-review" : "bg-credit")} style={{ width: `${pct}%` }} />
                    </div>
                    <p className={b.over ? "mt-1 text-xs text-debit" : "mt-1 text-xs text-ink-faint"}>
                      {b.over ? <>Over by <Amount value={b.remaining.replace("-", "")} colored={false} signed={false} /></> : <><Amount value={b.remaining} colored={false} signed={false} /> left</>}, {b.transaction_count} transactions
                      <button className="ml-2 underline" onClick={() => api(`/budgets/${b.id}`, { method: "DELETE" }).then(() => mutate())}>Remove</button>
                    </p>
                  </li>
                );
              })}
            </ul>
          )}
          {data.provenance.warnings.map((w) => <p key={w} className="mt-3 text-xs text-review">{w}</p>)}
        </Panel>
        <Panel title="Goals">
          {goals.length === 0 ? <p className="text-sm text-ink-soft">No goals yet.</p> : (
            <ul className="flex flex-col gap-4">
              {goals.map((g) => (
                <li key={g.id}>
                  <div className="flex items-baseline justify-between gap-2"><span className="font-medium">{g.name}</span><span className="num text-sm">{g.percent}%</span></div>
                  <div className="mt-1 h-2 rounded-full bg-sunken"><div className="h-2 rounded-full bg-ink/70" style={{ width: `${Math.min(Number(g.percent), 100)}%` }} /></div>
                  <p className="mt-1 text-xs text-ink-faint">
                    <Amount value={g.current} colored={false} signed={false} /> of <Amount value={g.target_amount} colored={false} signed={false} />
                    {g.monthly_needed ? <>, about <Amount value={g.monthly_needed} colored={false} signed={false} /> a month to reach it on time</> : null}
                    {g.partial ? ", some balances unknown" : ""}
                    {g.progress_mode === "MANUAL" ? <button className="ml-2 underline" onClick={() => setContrib(g)}>Add money</button> : null}
                  </p>
                </li>
              ))}
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

function BudgetForm({ onDone }: { onDone: () => void }) {
  const { data: cats } = useCategories();
  const [f, setF] = useState({ name: "", category_id: "", amount: "", start: new Date().toISOString().slice(0, 7) });
  const [err, setErr] = useState<unknown>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    const name = f.name || cats?.find((c) => c.id === f.category_id)?.name || "Budget";
    try { await api("/budgets", { method: "POST", json: { name, category_id: f.category_id || null, amount: f.amount, start_date: `${f.start}-01` } }); onDone(); } catch (e2) { setErr(e2); }
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-3">
      <Field label="Category">{(id) => <Select id={id} value={f.category_id} onChange={(e) => setF({ ...f, category_id: e.target.value })}><option value="">All spending</option>{cats ? <CategoryOptions cats={cats} /> : null}</Select>}</Field>
      <Field label="Monthly amount">{(id) => <Input id={id} required inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />}</Field>
      <Field label="Starting from" hint="Pick an earlier month to see how past months compare.">{(id, d) => <Input id={id} aria-describedby={d} type="month" required value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} />}</Field>
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
  const [err, setErr] = useState<unknown>(null);
  return (
    <form className="flex flex-col gap-3" onSubmit={async (e) => { e.preventDefault(); try { await api(`/goals/${goal.id}/contributions`, { method: "POST", json: { amount, contribution_date: new Date().toISOString().slice(0, 10) } }); onDone(); } catch (e2) { setErr(e2); } }}>
      <Field label="Amount">{(id) => <Input id={id} required inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />}</Field>
      {err ? <ErrorNote error={err} /> : null}
      <Button type="submit" variant="primary">Add</Button>
    </form>
  );
}
