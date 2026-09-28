"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { Button, ButtonLink, Empty, Loading, PageHeader, Panel } from "@/components/ui";
import { api, ApiError, useApi } from "@/lib/api";
import { formatMoney } from "@/lib/format";

type Answer = { answer: string; validated: boolean; metrics: { id: string; label: string; value: string; currency: string | null; kind: string }[]; evidence: { tool: string; evidence_url: string | null; partial: boolean | null }[]; tools_used: string[]; notice: string | null };
type Turn = { q: string; a?: Answer; error?: string };

const EXAMPLES = ["How much did I spend this month?", "How much did I spend on food last month?", "What subscriptions am I paying for?", "Compare this month's spending with last month", "Show transactions above ₹10,000 this month", "What is my net worth?"];

/** Renders the server-validated answer as plain text paragraphs (no HTML injection path). */
function AnswerText({ text }: { text: string }) {
  return (
    <div className="flex flex-col gap-2">
      {text.split(/\n{2,}/).map((para, i) => (
        <p key={i} className="whitespace-pre-wrap">{para.replace(/\*\*(.+?)\*\*/g, "$1")}</p>
      ))}
    </div>
  );
}

export default function AssistantPage() {
  const { data: settings } = useApi<{ server_enabled: boolean; assistant_enabled: boolean; key: { configured: boolean } }>("/ai/settings");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  async function ask(question: string) {
    if (!question.trim()) return;
    setBusy(true);
    setQ("");
    const history = turns.flatMap((t) => (t.a ? [{ role: "user", content: t.q }, { role: "assistant", content: t.a.answer }] : []));
    setTurns((t) => [...t, { q: question }]);
    try {
      const a = await api<Answer>("/ai/ask", { method: "POST", json: { question, history } });
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, a } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, error: e instanceof ApiError ? e.message : "Failed." } : x)));
    } finally {
      setBusy(false);
    }
  }

  if (!settings) return <Loading />;
  const ready = settings.server_enabled && settings.assistant_enabled && settings.key.configured;
  return (
    <>
      <PageHeader title="Ask about your money" description="Answers come from your ledger. Every figure is calculated by OneLedger and linked to its transactions; the AI only explains them." />
      {!ready ? (
        <Empty title="The assistant is off" action={<ButtonLink href="/settings#ai" variant="primary">Set up the assistant</ButtonLink>}>
          {!settings.server_enabled ? "AI is disabled on this server (AI_ENABLED=false). " : ""}
          {!settings.key.configured ? "Add an API key in Settings. " : ""}
          {!settings.assistant_enabled ? "Turn the assistant on after reading what is shared. " : ""}
          Everything else in OneLedger works without AI.
        </Empty>
      ) : (
        <div className="flex max-w-3xl flex-col gap-4">
          {turns.length === 0 ? (
            <div className="flex flex-wrap gap-2">{EXAMPLES.map((e) => <Button key={e} size="sm" onClick={() => ask(e)}>{e}</Button>)}</div>
          ) : null}
          {turns.map((t, i) => (
            <div key={i} className="flex flex-col gap-2">
              <p className="self-end rounded-lg bg-sunken px-3 py-2">{t.q}</p>
              {t.error ? <p className="text-debit">{t.error}</p> : !t.a ? <Loading label="Checking your ledger" /> : (
                <Panel>
                  <AnswerText text={t.a.answer} />
                  {!t.a.validated ? <p className="mt-2 text-xs text-review">The AI explanation was not shown because it could not be verified against your ledger.</p> : null}
                  {t.a.metrics.length ? (
                    <details className="mt-3 text-sm">
                      <summary className="cursor-pointer text-ink-faint">Figures used</summary>
                      <ul className="mt-1">{t.a.metrics.map((m) => <li key={m.id} className="flex justify-between gap-3 border-t border-rule py-1"><span>{m.label}</span><span className="num">{m.kind === "count" ? m.value : formatMoney(m.value, m.currency ?? "INR")}</span></li>)}</ul>
                    </details>
                  ) : null}
                  {t.a.evidence.filter((e) => e.evidence_url).length ? (
                    <p className="mt-2 text-sm">{t.a.evidence.filter((e) => e.evidence_url).map((e, n) => <Link key={n} className="mr-3 underline" href={e.evidence_url!}>See transactions{t.a!.evidence.length > 1 ? ` (${n + 1})` : ""}</Link>)}</p>
                  ) : null}
                </Panel>
              )}
            </div>
          ))}
          <form onSubmit={(e: FormEvent) => { e.preventDefault(); ask(q); }} className="sticky bottom-20 flex gap-2 rounded-lg border border-rule bg-surface p-2 lg:bottom-4">
            <input aria-label="Your question" className="min-h-10 flex-1 bg-transparent px-2 outline-none" maxLength={1000} placeholder="Ask about spending, income, loans, net worth…" value={q} onChange={(e) => setQ(e.target.value)} />
            <Button type="submit" variant="primary" busy={busy} disabled={!q.trim()}>Ask</Button>
          </form>
        </div>
      )}
    </>
  );
}
