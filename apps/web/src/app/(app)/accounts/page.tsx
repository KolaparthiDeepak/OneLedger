"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AccountForm } from "@/components/account-form";
import { Icon } from "@/components/icons";
import { ledgerChanged } from "@/components/quick-add";
import { Amount, Button, Empty, ErrorNote, Loading, PageHeader, Panel, Sheet } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { formatDate, KIND_LABEL, todayISO } from "@/lib/format";
import { addMoney, negate } from "@/lib/money";

type Account = { id: string; name: string; kind: string; nature: string; currency: string; masked_identifier: string | null; institution: string | null; balance: string | null; balance_as_of: string | null; balance_stale: boolean; person_id: string | null };

function CashWalletOffer({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  return (
    <div className="mb-6 flex flex-wrap items-center gap-3 rounded-xl border border-dashed border-rule-strong bg-surface/60 px-4 py-3">
      <Icon name="wallet" className="size-6 text-ink-soft" />
      <p className="min-w-0 flex-1 text-sm">
        <span className="font-medium">Track cash in a wallet.</span>{" "}
        <span className="text-ink-soft">ATM withdrawals then move into it instead of counting as spending, and you add cash purchases as you make them.</span>
      </p>
      {msg ? <span className="text-sm text-credit">{msg}</span> : (
        <Button size="sm" busy={busy} onClick={async () => {
          setBusy(true);
          setErr(null);
          try {
            const r = await api<{ cash_withdrawals_linked?: number }>("/accounts", { method: "POST", json: { name: "Cash", kind: "CASH", opening_date: todayISO(), opening_balance: "0" } });
            setMsg(r.cash_withdrawals_linked ? `Added. ${r.cash_withdrawals_linked} ATM withdrawals after today will move into it.` : "Cash wallet added.");
            ledgerChanged();
            onDone();
          } catch (x) { setErr(x); } finally { setBusy(false); }
        }}>Add a cash wallet</Button>
      )}
      {err ? <div className="w-full"><ErrorNote error={err} /></div> : null}
    </div>
  );
}

function Accounts() {
  const params = useSearchParams();
  const router = useRouter();
  const [adding, setAdding] = useState(params.get("new") === "1");
  const { data, error, mutate } = useApi<Account[]>("/accounts");
  if (error) return <ErrorNote error={error} onRetry={() => mutate()} />;
  if (!data) return <Loading />;
  const groups = [
    { label: "Bank and cash", kinds: ["BANK_SAVINGS", "BANK_CURRENT", "CASH", "WALLET"], people: false },
    { label: "Cards and loans", kinds: ["CREDIT_CARD", "LOAN", "OTHER_LIABILITY"], people: false },
    { label: "Investments and other assets", kinds: ["BROKERAGE", "INVESTMENT", "FIXED_DEPOSIT", "OTHER_ASSET"], people: false },
    { label: "Shared with people", kinds: ["OTHER_ASSET"], people: true },
  ];
  const hasWallet = data.some((a) => a.kind === "CASH");
  return (
    <>
      <PageHeader title="Accounts" description="Balances come from imported statements or balances you enter. Unknown balances are shown as unknown, never as zero."
        actions={<Button variant="primary" onClick={() => setAdding(true)}>Add account</Button>} />
      {data.length > 0 && !hasWallet ? <CashWalletOffer onDone={() => mutate()} /> : null}
      {data.length === 0 ? <Empty title="No accounts yet" action={<Button variant="primary" onClick={() => setAdding(true)}>Add account</Button>}>Add each bank account, card and loan you want to track.</Empty> : (
        <div className="flex flex-col gap-6">
          {groups.map((g) => {
            const rows = data.filter((a) => g.kinds.includes(a.kind) && !!a.person_id === g.people);
            if (!rows.length) return null;
            const known = rows.filter((a) => a.balance !== null);
            const total = addMoney(...known.map((a) => (a.nature === "LIABILITY" ? negate(a.balance) : a.balance)));
            return (
              <Panel key={g.label} title={g.label} action={
                g.people ? <Link href="/people" className="underline-offset-4 hover:underline">People</Link> : (
                  <span className="num font-medium text-ink">
                    <Amount value={total} colored={total.startsWith("-")} signed={false} />
                    {known.length < rows.length ? <span className="ml-1 text-xs font-normal text-review">+ unknown</span> : null}
                  </span>
                )}>
                <ul>
                  {rows.map((a) => (
                    <li key={a.id} className="border-b border-rule last:border-0">
                      <Link href={a.person_id ? "/people" : `/accounts/${a.id}`} className="-mx-2 flex items-center justify-between gap-3 rounded-lg px-2 py-3 hover:bg-sunken/50">
                        <div className="min-w-0">
                          <p className="truncate font-medium">{a.name}</p>
                          <p className="text-xs text-ink-faint">{[KIND_LABEL[a.kind], a.institution, a.masked_identifier].filter(Boolean).join(", ")}</p>
                        </div>
                        <div className="text-right">
                          {a.balance === null ? <span className="text-sm text-review">Balance unknown</span> : (
                            <>
                              <Amount value={a.nature === "LIABILITY" ? `-${a.balance}` : a.balance} currency={a.currency} colored={a.nature === "LIABILITY"} signed={false} className="font-medium" />
                              {a.person_id ? <p className="text-xs text-ink-faint">{a.balance.startsWith("-") ? "you owe" : "owes you"}</p> : <p className={a.balance_stale ? "text-xs text-review" : "text-xs text-ink-faint"}>{a.nature === "LIABILITY" ? "owed, " : ""}as of {formatDate(a.balance_as_of, false)}</p>}
                            </>
                          )}
                        </div>
                      </Link>
                    </li>
                  ))}
                </ul>
              </Panel>
            );
          })}
        </div>
      )}
      <Sheet open={adding} onClose={() => setAdding(false)} title="Add account" wide>
        <AccountForm onDone={(id) => { setAdding(false); mutate(); router.push(`/accounts/${id}`); }} />
      </Sheet>
    </>
  );
}

export default function AccountsPage() {
  return <Suspense fallback={<Loading />}><Accounts /></Suspense>;
}
