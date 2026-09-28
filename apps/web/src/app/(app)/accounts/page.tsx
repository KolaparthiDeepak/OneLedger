"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AccountForm } from "@/components/account-form";
import { Amount, Button, Empty, ErrorNote, Loading, PageHeader, Panel, Sheet } from "@/components/ui";
import { useApi } from "@/lib/api";
import { formatDate, KIND_LABEL } from "@/lib/format";

type Account = { id: string; name: string; kind: string; nature: string; currency: string; masked_identifier: string | null; institution: string | null; balance: string | null; balance_as_of: string | null; balance_stale: boolean };

function Accounts() {
  const params = useSearchParams();
  const router = useRouter();
  const [adding, setAdding] = useState(params.get("new") === "1");
  const { data, error, mutate } = useApi<Account[]>("/accounts");
  if (error) return <ErrorNote error={error} onRetry={() => mutate()} />;
  if (!data) return <Loading />;
  const groups = [
    { label: "Bank and cash", kinds: ["BANK_SAVINGS", "BANK_CURRENT", "CASH", "WALLET"] },
    { label: "Cards and loans", kinds: ["CREDIT_CARD", "LOAN", "OTHER_LIABILITY"] },
    { label: "Investments and other assets", kinds: ["BROKERAGE", "INVESTMENT", "FIXED_DEPOSIT", "OTHER_ASSET"] },
  ];
  return (
    <>
      <PageHeader title="Accounts" description="Balances come from imported statements or balances you enter. Unknown balances are shown as unknown, never as zero."
        actions={<Button variant="primary" onClick={() => setAdding(true)}>Add account</Button>} />
      {data.length === 0 ? <Empty title="No accounts yet" action={<Button variant="primary" onClick={() => setAdding(true)}>Add account</Button>}>Add each bank account, card and loan you want to track.</Empty> : (
        <div className="flex flex-col gap-6">
          {groups.map((g) => {
            const rows = data.filter((a) => g.kinds.includes(a.kind));
            if (!rows.length) return null;
            return (
              <Panel key={g.label} title={g.label}>
                <ul>
                  {rows.map((a) => (
                    <li key={a.id} className="border-b border-rule last:border-0">
                      <Link href={`/accounts/${a.id}`} className="flex items-center justify-between gap-3 py-3 hover:bg-sunken/50">
                        <div className="min-w-0">
                          <p className="truncate font-medium">{a.name}</p>
                          <p className="text-xs text-ink-faint">{[KIND_LABEL[a.kind], a.institution, a.masked_identifier].filter(Boolean).join(", ")}</p>
                        </div>
                        <div className="text-right">
                          {a.balance === null ? <span className="text-sm text-review">Balance unknown</span> : (
                            <>
                              <Amount value={a.nature === "LIABILITY" ? `-${a.balance}` : a.balance} currency={a.currency} colored={a.nature === "LIABILITY"} signed={false} className="font-medium" />
                              <p className={a.balance_stale ? "text-xs text-review" : "text-xs text-ink-faint"}>{a.nature === "LIABILITY" ? "owed, " : ""}as of {formatDate(a.balance_as_of, false)}</p>
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
