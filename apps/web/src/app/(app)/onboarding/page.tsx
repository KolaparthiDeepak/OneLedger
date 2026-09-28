"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { mutate } from "swr";
import { AccountForm } from "@/components/account-form";
import { Button, Field, Input, PageHeader, Panel, Select } from "@/components/ui";
import { api, useApi } from "@/lib/api";

type Me = { display_name: string; timezone: string; base_currency: string };

export default function Onboarding() {
  const router = useRouter();
  const { data: me } = useApi<Me>("/me");
  const [step, setStep] = useState<1 | 2>(1);
  const [tz, setTz] = useState<string | null>(null);
  const [name, setName] = useState<string | null>(null);

  async function finish(next: string) {
    await api("/me", { method: "PATCH", json: { onboarded: true } });
    await mutate("/me");
    router.replace(next);
  }

  if (!me) return null;
  return (
    <div className="max-w-2xl">
      <PageHeader title="Set up OneLedger" description="Two quick steps. Your data stays in your own database; nothing is shared with anyone." />
      {step === 1 ? (
        <Panel title="Your preferences">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Your name">{(id) => <Input id={id} value={name ?? me.display_name} onChange={(e) => setName(e.target.value)} />}</Field>
            <Field label="Time zone" hint="Months and 'last month' are calculated in this zone.">{(id, d) => (
              <Select id={id} aria-describedby={d} value={tz ?? me.timezone} onChange={(e) => setTz(e.target.value)}>
                {["Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Europe/London", "America/New_York", "America/Los_Angeles", "UTC"].map((z) => <option key={z}>{z}</option>)}
              </Select>
            )}</Field>
          </div>
          <Button className="mt-4" variant="primary" onClick={async () => { await api("/me", { method: "PATCH", json: { timezone: tz ?? me.timezone, display_name: name ?? me.display_name } }); setStep(2); }}>
            Continue
          </Button>
        </Panel>
      ) : (
        <Panel title="Add your first account">
          <p className="mb-4 text-sm text-ink-soft">Start with the bank account you use most. You will import its statement next.</p>
          <AccountForm submitLabel="Add and import a statement" onDone={(id) => finish(`/imports?account=${id}`)} />
          <button className="mt-4 text-sm text-ink-soft underline" onClick={() => finish("/")}>Skip for now</button>
        </Panel>
      )}
    </div>
  );
}
