"use client";

import { useState } from "react";
import { Badge, Button, ErrorNote, Field, Input, Loading, PageHeader, Panel, Select } from "@/components/ui";
import { api, ApiError, useApi } from "@/lib/api";
import { formatDate } from "@/lib/format";

type Me = { email: string; display_name: string; timezone: string };
type Session = { id: string; created_at: string; last_seen_at: string; user_agent: string | null; current: boolean };
type AiProvider = { key: string; label: string; needs_key: boolean; default_model: string | null; key_hint: string; key_saved: boolean };
type AiSettings = { server_enabled: boolean; provider: string; model: string; providers: AiProvider[]; auto_categorize: boolean; auto_categorize_ready: boolean; auto_categorize_error: { code: string; message: string; at: string } | null; assistant_enabled: boolean; classification_enabled: boolean; share_descriptions: boolean; opted_in_at: string | null; key: { configured: boolean; source: string | null; masked_suffix: string | null }; notice: string; version: number };

export default function SettingsPage() {
  const { data: me } = useApi<Me>("/me");
  if (!me) return <Loading />;
  return (
    <>
      <PageHeader title="Settings" description="The optional AI assistant, inviting people to their own ledgers, and the devices signed in to yours." />
      <div className="grid grid-cols-1 gap-6">
        <div id="ai"><AiPanel /></div>
        <div id="people"><People /></div>
        <Security me={me} />
      </div>
    </>
  );
}

type Invite = { id: string; email: string | null; note: string | null; created_at: string; expires_at: string; status: "pending" | "used" | "expired" | "revoked"; used_by_email: string | null };

function People() {
  const { data, mutate } = useApi<Invite[]>("/invites");
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    setCopied(false);
    try {
      const r = await api<{ url: string }>("/invites", { method: "POST", json: { email: email.trim() || null, note: note.trim() || null } });
      setLink(r.url);
      setEmail("");
      setNote("");
      await mutate();
    } catch (e2) { setErr(e2); } finally { setBusy(false); }
  }
  const TONE = { pending: "review", used: "credit", expired: "neutral", revoked: "neutral" } as const;
  const LABEL = { pending: "Waiting", used: "Joined", expired: "Expired", revoked: "Revoked" };
  return (
    <Panel title="People">
      <p className="-mt-1 mb-5 max-w-[70ch] text-sm text-ink-soft">Invite someone to keep their own ledger on this OneLedger. They get a separate, private ledger: they can&apos;t see yours and you can&apos;t see theirs. Each link works once and expires in 7 days.</p>
      <form onSubmit={create} className="grid grid-cols-1 items-end gap-3 sm:grid-cols-[1fr_1fr_auto]">
        <Field label="Their email (optional)" hint="If set, only this email can use the link.">{(id, d) => <Input id={id} aria-describedby={d} type="email" placeholder="name@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
        <Field label="Note for you (optional)" hint="Only you see this, e.g. who it's for.">{(id, d) => <Input id={id} aria-describedby={d} maxLength={120} placeholder="e.g. Priya" value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
        <Button type="submit" variant="primary" busy={busy} className="sm:mb-6">Create invite link</Button>
      </form>
      {err ? <div className="mt-3"><ErrorNote error={err} /></div> : null}
      {link ? (
        <div role="status" className="mt-4 rounded-xl border border-credit/30 bg-credit-wash p-4">
          <p className="text-sm font-medium text-credit">Invite link ready. Send it to them yourself; it is shown only now.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Input readOnly aria-label="Invite link" value={link} onFocus={(e) => e.currentTarget.select()} className="min-w-0 flex-1 font-mono text-xs" />
            <Button onClick={async () => { try { await navigator.clipboard.writeText(link); setCopied(true); } catch { setCopied(false); } }}>{copied ? "Copied" : "Copy link"}</Button>
          </div>
        </div>
      ) : null}
      {data?.length ? (
        <>
          <h3 className="mb-1 mt-6 text-sm font-medium">Invites you&apos;ve sent</h3>
          <ul className="text-sm">
            {data.map((i) => { const title = i.status === "used" ? i.used_by_email : i.email ?? i.note ?? "Anyone with the link"; return (
              <li key={i.id} className="flex flex-wrap items-center justify-between gap-3 border-t border-rule py-2.5">
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2 font-medium">
                    {title}
                    <Badge tone={TONE[i.status]}>{LABEL[i.status]}</Badge>
                  </span>
                  <span className="block text-xs text-ink-faint">
                    {i.note && i.note !== title ? `${i.note}. ` : ""}
                    Created {formatDate(i.created_at.slice(0, 10))}{i.status === "pending" ? `, expires ${formatDate(i.expires_at.slice(0, 10))}` : ""}
                  </span>
                </span>
                {i.status === "pending" ? <Button size="sm" variant="ghost" onClick={() => api(`/invites/${i.id}/revoke`, { method: "POST" }).then(() => mutate())}>Revoke</Button> : null}
              </li>
            ); })}
          </ul>
        </>
      ) : null}
    </Panel>
  );
}

/** "Chrome on macOS" from a user-agent string; display only. */
function device(ua: string | null): string {
  if (!ua) return "Unknown device";
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : /curl|python|httpx/i.test(ua) ? "Script" : "Browser";
  const os = /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Mac OS X|Macintosh/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

function Security({ me }: { me: Me }) {
  const { data: sessions, mutate } = useApi<Session[]>("/auth/sessions");
  return (
    <Panel title="Sign-in">
      <p className="text-sm text-ink-soft">Signed in as <span className="font-medium text-ink">{me.email}</span></p>
      <div className="mb-1 mt-6 flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">Signed-in sessions</h3>
        {(sessions?.length ?? 0) > 1 ? <Button size="sm" variant="ghost" onClick={() => api("/auth/sessions/revoke-others", { method: "POST" }).then(() => mutate())}>Sign out all other devices</Button> : null}
      </div>
      <ul className="text-sm">
        {sessions?.map((s) => (
          <li key={s.id} className="flex items-center justify-between gap-3 border-t border-rule py-2.5">
            <span className="min-w-0">
              <span className="flex items-center gap-2 font-medium">{device(s.user_agent)}{s.current ? <Badge tone="credit">This device</Badge> : null}</span>
              <span className="block text-xs text-ink-faint">Last active {new Date(s.last_seen_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}</span>
            </span>
            {!s.current ? <Button size="sm" variant="ghost" onClick={() => api(`/auth/sessions/${s.id}`, { method: "DELETE" }).then(() => mutate())}>Sign out</Button> : null}
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function Switch({ checked, disabled, onChange, label, hint }: { checked: boolean; disabled?: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className={disabled ? "flex cursor-not-allowed items-start gap-3 opacity-50" : "flex cursor-pointer items-start gap-3"}>
      <input type="checkbox" role="switch" className="peer sr-only" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span aria-hidden className="relative mt-0.5 inline-flex h-5 w-9 shrink-0 rounded-full bg-rule-strong transition-colors peer-checked:bg-credit peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--focus)] after:absolute after:left-0.5 after:top-0.5 after:size-4 after:rounded-full after:bg-surface after:shadow after:transition-transform peer-checked:after:translate-x-4" />
      <span className="text-sm"><span className="font-medium">{label}</span>{hint ? <span className="mt-0.5 block text-xs text-ink-faint">{hint}</span> : null}</span>
    </label>
  );
}

function Step({ n, title, done, children }: { n: number; title: string; done: boolean; children: React.ReactNode }) {
  return (
    <li className="grid grid-cols-[2rem_1fr] gap-3 border-t border-rule py-5 first:border-0 first:pt-0">
      <span aria-hidden className={done ? "inline-flex size-7 items-center justify-center rounded-full bg-credit text-xs font-semibold text-surface" : "inline-flex size-7 items-center justify-center rounded-full border border-rule-strong text-xs font-semibold text-ink-soft"}>
        {done ? "✓" : n}
      </span>
      <div className="min-w-0">
        <h3 className="mb-3 text-sm font-semibold">{title}</h3>
        {children}
      </div>
    </li>
  );
}

function AiPanel() {
  const { data: s, mutate } = useApi<AiSettings>("/ai/settings");
  const [key, setKey] = useState("");
  const [model, setModel] = useState<string | null>(null);
  const [models, setModels] = useState<string[] | null>(null);
  const [accept, setAccept] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  if (!s) return <Panel title="AI assistant"><Loading /></Panel>;
  const current = s.providers.find((p) => p.key === s.provider);
  const needsKey = current?.needs_key !== false;
  const keyReady = !needsKey || s.key.configured;
  const status = !s.server_enabled ? { label: "Off on this server", tone: "neutral" as const }
    : !keyReady ? { label: "Needs an API key", tone: "review" as const }
    : s.auto_categorize_error ? { label: "Stopped", tone: "debit" as const }
    : s.auto_categorize ? { label: "Categorising new imports", tone: "credit" as const }
    : s.assistant_enabled || s.classification_enabled ? { label: "On", tone: "credit" as const }
    : { label: "Ready, not switched on", tone: "review" as const };

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setErr(null);
    setMsg(null);
    try { await fn(); } catch (e) { setErr(e); } finally { setBusy(null); }
  }
  const patch = (body: Record<string, unknown>) => run("patch", async () => {
    await api("/ai/settings", { method: "PATCH", json: { version: s.version, ...body } });
    await mutate();
  });

  return (
    <Panel title={<span className="flex items-center gap-2.5">AI assistant <Badge tone={status.tone}>{status.label}</Badge></span>}>
      <p className="-mt-1 mb-5 max-w-[70ch] text-sm text-ink-soft">Optional. Lets AI categorise what your rules miss and answer questions about your money. Everything else in OneLedger works without it.</p>
      {s.auto_categorize_error ? (
        <div role="alert" className="mb-5 rounded-xl border border-debit/30 bg-debit-wash p-4 text-sm">
          <p className="font-medium text-debit">Automatic categorisation stopped</p>
          <p className="mt-1 text-ink">{s.auto_categorize_error.message}</p>
          <p className="mt-1 text-xs text-ink-faint">Last tried {new Date(s.auto_categorize_error.at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}. Nothing was changed in your ledger.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" busy={busy === "retry"} onClick={() => run("retry", async () => {
              const r = await api<{ ok: boolean; error?: string }>("/ai/test", { method: "POST" });
              if (!r.ok) throw new ApiError(400, "AI_TEST_FAILED", r.error ?? "Still not working.");
              await api("/ai/categorize-now", { method: "POST" });
              setMsg("Working again. Categorising uncategorised transactions in the background.");
              await mutate();
            })}>Check and try again</Button>
          </div>
        </div>
      ) : null}
      {!s.server_enabled ? <p className="mb-4 rounded-lg bg-sunken px-3.5 py-2.5 text-sm">AI is switched off on this server. Set <code className="rounded bg-surface px-1">AI_ENABLED=true</code> in <code className="rounded bg-surface px-1">.env</code> and restart OneLedger to use it.</p> : null}
      <ol className={!s.server_enabled ? "pointer-events-none opacity-50" : undefined}>
        <Step n={1} title="Choose a provider and add your key" done={keyReady}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Provider" hint="OpenRouter gives access to models from many companies with one key.">{(id, d) => (
              <Select id={id} aria-describedby={d} value={s.provider} onChange={(e) => { setModels(null); setModel(null); patch({ provider: e.target.value }); }}>
                {s.providers.map((p) => <option key={p.key} value={p.key}>{p.label}{p.key_saved ? " (key saved)" : ""}</option>)}
              </Select>
            )}</Field>
            {needsKey ? (
              <Field label="API key" hint={s.key.configured ? `Saved: ${s.key.source === "server" ? "server key" : "your key"} ending ${s.key.masked_suffix}. Paste a new one to replace it.` : "Stored encrypted and never shown again."}>{(id, d) => (
                <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); run("key", async () => { await api("/ai/key", { method: "PUT", json: { api_key: key } }); setKey(""); setMsg("Key saved."); await mutate(); }); }}>
                  <Input id={id} aria-describedby={d} type="password" autoComplete="off" placeholder={current?.key_hint ? `Paste key (${current.key_hint})` : "Paste API key"} value={key} onChange={(e) => setKey(e.target.value)} />
                  <Button type="submit" busy={busy === "key"} disabled={key.trim().length < 8}>Save</Button>
                </form>
              )}</Field>
            ) : <p className="self-end text-sm text-ink-soft">No key needed. Make sure the service is running on this computer.</p>}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" disabled={!keyReady || !s.model} busy={busy === "test"} onClick={() => run("test", async () => { const r = await api<{ ok: boolean; error?: string; model?: string }>("/ai/test", { method: "POST" }); if (r.ok) setMsg(`Connected to ${r.model}. No financial data was sent.`); else setErr(new ApiError(400, "AI_TEST_FAILED", r.error ?? "Test failed.")); })}>Test connection</Button>
            {s.key.source === "owner" ? <Button size="sm" variant="ghost" onClick={() => run("del", async () => { await api("/ai/key", { method: "DELETE" }); await mutate(); })}>Delete key</Button> : null}
          </div>
        </Step>
        <Step n={2} title="Pick a model" done={keyReady && !!s.model}>
          <Field label="Model" hint={current?.default_model ? `Default: ${current.default_model}. Load the list to choose another that supports tool calling.` : "Load the provider's models, then pick one that supports tool calling."}>{(id, d) => (
            <div className="flex flex-wrap gap-2">
              <Input id={id} aria-describedby={d} list="ai-models" className="min-w-0 flex-1" value={model ?? s.model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. anthropic/claude-opus-5" />
              <datalist id="ai-models">{models?.map((m) => <option key={m} value={m} />)}</datalist>
              <Button type="button" disabled={!keyReady} busy={busy === "models"} onClick={() => run("models", async () => { const r = await api<{ models: string[] }>("/ai/models"); setModels(r.models); setMsg(`${r.models.length} models available. Start typing in the Model box to pick one.`); })}>Load list</Button>
              <Button type="button" variant="primary" disabled={model === null || model === s.model || !model.trim()} busy={busy === "patch"} onClick={() => patch({ model: model!.trim() }).then(() => setModel(null))}>Use this model</Button>
            </div>
          )}</Field>
        </Step>
        <Step n={3} title="Decide what the AI may do" done={!!s.opted_in_at && (s.assistant_enabled || s.classification_enabled)}>
          <details className="mb-4 rounded-lg bg-raised px-3.5 py-2.5 text-sm ring-1 ring-rule" open={!s.opted_in_at}>
            <summary className="font-medium">What is shared with the provider</summary>
            <p className="mt-2 text-ink-soft">{s.notice}</p>
          </details>
          {!s.opted_in_at ? (
            <label className="mb-4 flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={accept} onChange={(e) => setAccept(e.target.checked)} />I understand what is shared with the AI provider.</label>
          ) : <p className="mb-4 text-xs text-ink-faint">You accepted this on {formatDate(s.opted_in_at.slice(0, 10))}.</p>}
          <div className="flex flex-col gap-4">
            <Switch label="Answer my questions" hint="Enables the Ask page." checked={s.assistant_enabled} disabled={!keyReady || (!s.opted_in_at && !accept)} onChange={(v) => patch({ assistant_enabled: v, accept_notice: accept })} />
            <Switch label="Suggest categories" hint="For transactions your rules and merchants could not categorise." checked={s.classification_enabled} disabled={!keyReady || (!s.opted_in_at && !accept)} onChange={(v) => patch({ classification_enabled: v, accept_notice: accept })} />
            <Switch label="Share transaction descriptions" hint="Needed for categorising. Without it only amounts, dates and categories are sent." checked={s.share_descriptions} disabled={!keyReady} onChange={(v) => patch({ share_descriptions: v })} />
            <Switch label="Categorise new imports automatically" hint={'Only confident answers are applied, labelled "by AI", and you can change them. The AI never marks money as a transfer or investment.'} checked={s.auto_categorize} disabled={!s.classification_enabled || !s.share_descriptions} onChange={(v) => patch({ auto_categorize: v })} />
          </div>
          {s.auto_categorize ? (
            <Button size="sm" className="mt-4" disabled={!s.auto_categorize_ready} busy={busy === "catnow"}
              onClick={() => run("catnow", async () => { await api("/ai/categorize-now", { method: "POST" }); setMsg("Categorising uncategorised transactions in the background. Check Transactions in a minute."); })}>
              Categorise everything uncategorised now
            </Button>
          ) : null}
        </Step>
      </ol>
      {msg ? <p role="status" className="mt-2 rounded-lg bg-credit-wash px-3.5 py-2.5 text-sm text-credit">{msg}</p> : null}
      {err ? <div className="mt-2"><ErrorNote error={err} /></div> : null}
    </Panel>
  );
}
