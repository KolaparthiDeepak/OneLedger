"use client";

import { useState } from "react";
import { Badge, Button, ErrorNote, Field, Input, Loading, PageHeader, Panel, Select } from "@/components/ui";
import { api, ApiError, useApi } from "@/lib/api";
import { formatDate } from "@/lib/format";

type Me = { email: string; display_name: string; timezone: string; mfa_enabled: boolean; mfa_required: boolean };
type Session = { id: string; created_at: string; last_seen_at: string; user_agent: string | null; current: boolean };
type AiProvider = { key: string; label: string; needs_key: boolean; default_model: string | null; key_hint: string; key_saved: boolean };
type PhoneAlerts = { enabled: boolean; server_url: string; topic: string | null; subscribe_url: string | null; bills: boolean; alerts: boolean; show_amounts: boolean; quiet_hours: boolean; last_sent_at: string | null; last_error: string | null };
type AiSettings = { server_enabled: boolean; provider: string; model: string; providers: AiProvider[]; auto_categorize: boolean; auto_categorize_ready: boolean; auto_categorize_error: { code: string; message: string; at: string } | null; assistant_enabled: boolean; classification_enabled: boolean; share_descriptions: boolean; opted_in_at: string | null; key: { configured: boolean; source: string | null; masked_suffix: string | null }; notice: string; version: number };

export default function SettingsPage() {
  const { data: me } = useApi<Me>("/me");
  if (!me) return <Loading />;
  return (
    <>
      <PageHeader title="Settings" description="Your sign-in and devices, phone alerts, your data, the optional AI assistant, and invites for other people's ledgers." />
      <nav aria-label="Settings sections" className="-mx-4 mb-6 flex gap-1.5 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:mx-0 sm:px-0">
        {[["security", "Sign-in and security"], ["phone", "Phone alerts"], ["data", "Your data"], ["ai", "AI assistant"], ["people", "Invites"]].map(([id, label]) => (
          <a key={id} href={`#${id}`} className="shrink-0 rounded-full border border-rule bg-surface px-3 py-1 text-sm text-ink-soft hover:border-ink-faint hover:text-ink">{label}</a>
        ))}
      </nav>
      <div className="grid grid-cols-1 gap-6">
        <div id="security" className="scroll-mt-20"><Security me={me} /></div>
        <div id="phone" className="scroll-mt-20"><PhoneAlertsPanel /></div>
        <div id="data" className="scroll-mt-20"><DataPanel /></div>
        <div id="ai" className="scroll-mt-20"><AiPanel /></div>
        <div id="people" className="scroll-mt-20"><People /></div>
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
    <Panel title="Invite someone to their own ledger">
      <p className="-mt-1 mb-5 max-w-[70ch] text-sm text-ink-soft">To split bills with someone inside your ledger, use <a href="/people" className="underline">Shared with people</a> instead. Invite someone to keep their own ledger on this OneLedger. They get a separate, private ledger: they can&apos;t see yours and you can&apos;t see theirs. Each link works once and expires in 7 days.</p>
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

function PasswordForm() {
  const [f, setF] = useState({ current: "", next: "", again: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const mismatch = f.again.length > 0 && f.next !== f.again;
  return (
    <form className="grid gap-3 sm:grid-cols-3" onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true);
      setErr(null);
      setMsg(null);
      try {
        const r = await api<{ signed_out: number }>("/auth/password", { method: "POST", json: { current_password: f.current, new_password: f.next } });
        setMsg(r.signed_out ? `Password changed. ${r.signed_out} other ${r.signed_out === 1 ? "device was" : "devices were"} signed out.` : "Password changed.");
        setF({ current: "", next: "", again: "" });
      } catch (x) { setErr(x); } finally { setBusy(false); }
    }}>
      <Field label="Current password">{(id) => <Input id={id} type="password" autoComplete="current-password" required value={f.current} onChange={(e) => setF({ ...f, current: e.target.value })} />}</Field>
      <Field label="New password" hint="At least 12 characters.">{(id, d) => <Input id={id} aria-describedby={d} type="password" autoComplete="new-password" required minLength={12} value={f.next} onChange={(e) => setF({ ...f, next: e.target.value })} />}</Field>
      <Field label="New password again" error={mismatch ? "The two new passwords are different." : null}>{(id) => <Input id={id} type="password" autoComplete="new-password" required value={f.again} onChange={(e) => setF({ ...f, again: e.target.value })} />}</Field>
      <div className="flex flex-wrap items-center gap-3 sm:col-span-3">
        <Button type="submit" busy={busy} disabled={mismatch || f.next.length < 12 || !f.current}>Change password</Button>
        <span className="text-xs text-ink-faint">Other devices are signed out when you change it.</span>
      </div>
      {msg ? <p role="status" className="rounded-lg bg-credit-wash px-3.5 py-2 text-sm text-credit sm:col-span-3">{msg}</p> : null}
      {err ? <div className="sm:col-span-3"><ErrorNote error={err} /></div> : null}
    </form>
  );
}

function TwoStep({ me, onChanged }: { me: Me; onChanged: () => void }) {
  const [enrol, setEnrol] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setErr(null);
    try { await fn(); } catch (x) { setErr(x); } finally { setBusy(false); }
  }
  if (me.mfa_enabled) {
    return (
      <div className="flex flex-col gap-3">
        <p className="text-sm"><Badge tone="credit">On</Badge> <span className="ml-1 text-ink-soft">Signing in asks for a code from your authenticator app.</span></p>
        {me.mfa_required ? <p className="text-xs text-ink-faint">This server requires two-step sign-in, so it can&apos;t be turned off.</p> : (
          <form className="grid items-end gap-3 sm:grid-cols-[1fr_10rem_auto]" onSubmit={(e) => { e.preventDefault(); void run(async () => { await api("/auth/mfa/disable", { method: "POST", json: { code, password } }); setCode(""); setPassword(""); onChanged(); }); }}>
            <Field label="Password">{(id) => <Input id={id} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />}</Field>
            <Field label="Code from the app">{(id) => <Input id={id} inputMode="numeric" autoComplete="one-time-code" required maxLength={8} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />}</Field>
            <Button type="submit" variant="danger" busy={busy}>Turn off</Button>
          </form>
        )}
        {err ? <ErrorNote error={err} /> : null}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm"><Badge tone="review">Off</Badge> <span className="ml-1 text-ink-soft">Add a code from an authenticator app (Google Authenticator, 1Password, Authy) to every sign-in.</span></p>
      {!enrol ? (
        <Button className="self-start" busy={busy} onClick={() => run(async () => setEnrol(await api<{ secret: string; otpauth_uri: string }>("/auth/mfa/enroll", { method: "POST" })))}>Turn on two-step sign-in</Button>
      ) : (
        <form className="flex flex-col gap-3 rounded-xl border border-rule bg-raised p-4" onSubmit={(e) => { e.preventDefault(); void run(async () => { await api("/auth/mfa/enroll/confirm", { method: "POST", json: { code } }); setEnrol(null); setCode(""); onChanged(); }); }}>
          <p className="text-sm">1. In your authenticator app, add an account with this key, or <a href={enrol.otpauth_uri} className="underline">open it in the app</a> on this device:</p>
          <code className="select-all break-all rounded-lg bg-surface px-3 py-2 font-mono text-sm tracking-wider ring-1 ring-rule">{enrol.secret.replace(/(.{4})/g, "$1 ").trim()}</code>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="2. Enter the 6-digit code it shows">{(id) => <Input id={id} inputMode="numeric" autoComplete="one-time-code" required maxLength={8} className="w-40" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />}</Field>
            <Button type="submit" variant="primary" busy={busy} disabled={code.length < 6}>Confirm</Button>
            <Button type="button" variant="ghost" onClick={() => setEnrol(null)}>Cancel</Button>
          </div>
        </form>
      )}
      {err ? <ErrorNote error={err} /> : null}
    </div>
  );
}

function Security({ me }: { me: Me }) {
  const { data: sessions, mutate } = useApi<Session[]>("/auth/sessions");
  const { mutate: mutateMe } = useApi<Me>("/me");
  const current = sessions?.find((s) => s.current);
  const others = (sessions ?? []).filter((s) => !s.current);
  const groups = Object.values(others.reduce<Record<string, Session[]>>((acc, s) => { (acc[device(s.user_agent)] ??= []).push(s); return acc; }, {}));
  return (
    <Panel title="Sign-in and security">
      <p className="text-sm text-ink-soft">Signed in as <span className="font-medium text-ink">{me.email}</span></p>
      <section className="mt-6">
        <h3 className="mb-3 text-sm font-medium">Password</h3>
        <PasswordForm />
      </section>
      <section className="mt-6 border-t border-rule pt-5">
        <h3 className="mb-3 text-sm font-medium">Two-step sign-in</h3>
        <TwoStep me={me} onChanged={() => { mutateMe(); mutate(); }} />
      </section>
      <div className="mb-1 mt-6 flex items-center justify-between gap-3 border-t border-rule pt-5">
        <h3 className="text-sm font-medium">Signed-in sessions</h3>
        {others.length ? <Button size="sm" variant="ghost" onClick={() => api("/auth/sessions/revoke-others", { method: "POST" }).then(() => mutate())}>Sign out all other devices</Button> : null}
      </div>
      <ul className="text-sm">
        {current ? (
          <li className="flex items-center justify-between gap-3 border-t border-rule py-2.5">
            <span className="min-w-0">
              <span className="flex items-center gap-2 font-medium">{device(current.user_agent)}<Badge tone="credit">This device</Badge></span>
              <span className="block text-xs text-ink-faint">Last active {new Date(current.last_seen_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}</span>
            </span>
          </li>
        ) : null}
        {groups.map((g) => {
          const latest = g[0]!;
          return (
            <li key={latest.id} className="flex items-center justify-between gap-3 border-t border-rule py-2.5">
              <span className="min-w-0">
                <span className="flex items-center gap-2 font-medium">{device(latest.user_agent)}{g.length > 1 ? <span className="num text-xs font-normal text-ink-faint">{g.length} sessions</span> : null}</span>
                <span className="block text-xs text-ink-faint">Last active {new Date(latest.last_seen_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}</span>
              </span>
              <Button size="sm" variant="ghost" onClick={async () => { for (const s of g) await api(`/auth/sessions/${s.id}`, { method: "DELETE" }); mutate(); }}>{g.length > 1 ? `Sign out all ${g.length}` : "Sign out"}</Button>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

function PhoneAlertsPanel() {
  const { data: s, mutate } = useApi<PhoneAlerts>("/notifications/settings");
  const [server, setServer] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  if (!s) return <Panel title="Phone alerts"><Loading /></Panel>;
  const status = s.last_error ? { label: "Not delivering", tone: "debit" as const } : s.enabled ? { label: "On", tone: "credit" as const } : { label: "Off", tone: "neutral" as const };

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setErr(null);
    setMsg(null);
    try { await fn(); } catch (e) { setErr(e); } finally { setBusy(null); }
  }
  const put = (body: Partial<PhoneAlerts>) => run("put", async () => { await api("/notifications/settings", { method: "PUT", json: body }); await mutate(); });
  async function copy(text: string) {
    try { await navigator.clipboard.writeText(text); setMsg("Topic copied."); } catch { setMsg("Select the topic and copy it."); }
  }

  return (
    <Panel title={<span className="flex items-center gap-2.5">Phone alerts <Badge tone={status.tone}>{status.label}</Badge></span>}>
      <p className="-mt-1 mb-5 max-w-[70ch] text-sm text-ink-soft">Bills coming due and anything that needs attention, pushed to your phone through the free ntfy app, even when OneLedger is closed. Each alert arrives once; a bill rings again, at the highest priority, on the day it&apos;s due.</p>
      {s.last_error ? (
        <div role="alert" className="mb-5 rounded-xl border border-debit/30 bg-debit-wash p-4 text-sm">
          <p className="font-medium text-debit">The last alert didn&apos;t go out</p>
          <p className="mt-1 text-ink">{s.last_error} OneLedger keeps trying every minute.</p>
        </div>
      ) : null}
      <div className="mb-5"><Switch label="Send alerts to my phone" hint="Needs the OneLedger worker running (make dev starts it)." checked={s.enabled} onChange={(v) => put({ enabled: v })} /></div>
      <ol className={!s.enabled ? "opacity-60" : undefined}>
        <Step n={1} title="Install ntfy on your phone" done={!!s.last_sent_at}>
          <p className="text-sm text-ink-soft">Free, no account needed: <a className="underline underline-offset-4" href="https://play.google.com/store/apps/details?id=io.heckel.ntfy" target="_blank" rel="noreferrer">Android</a> or <a className="underline underline-offset-4" href="https://apps.apple.com/app/ntfy/id1625396347" target="_blank" rel="noreferrer">iPhone</a>.</p>
        </Step>
        <Step n={2} title="Subscribe to your private topic" done={!!s.last_sent_at}>
          {s.topic ? (
            <>
              <p className="mb-2 text-sm text-ink-soft">In ntfy, tap <b>+</b> and enter this topic{s.server_url !== "https://ntfy.sh" ? <> with the server <span className="num">{s.server_url}</span></> : null}. Anyone who knows it can read your alerts, so keep it to yourself.</p>
              <div className="flex flex-wrap items-center gap-2">
                <code className="num min-w-0 select-all break-all rounded-lg bg-sunken px-3 py-2 text-sm">{s.topic}</code>
                <Button size="sm" onClick={() => copy(s.topic!)}>Copy</Button>
              </div>
            </>
          ) : <p className="text-sm text-ink-soft">Your topic appears here when you switch alerts on.</p>}
        </Step>
        <Step n={3} title="Choose what to send, then test it" done={!!s.last_sent_at}>
          <div className="flex flex-col gap-3">
            <Switch label="Bills" hint="Card bills, loan EMIs and confirmed recurring payments, 3 days before and on the day." checked={s.bills} onChange={(v) => put({ bills: v })} />
            <Switch label="Other alerts" hint="Over or near a budget, unusual transactions, statements to import." checked={s.alerts} onChange={(v) => put({ alerts: v })} />
            <Switch label="Show amounts and names" hint="Off: the alert only says a bill is due or something needs attention. Messages pass through the ntfy server." checked={s.show_amounts} onChange={(v) => put({ show_amounts: v })} />
            <Switch label="Quiet at night" hint="Nothing between 22:00 and 07:00; alerts wait until morning." checked={s.quiet_hours} onChange={(v) => put({ quiet_hours: v })} />
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button busy={busy === "test"} onClick={() => run("test", async () => { await api("/notifications/test", { method: "POST" }); await mutate(); setMsg("Test alert sent. It should buzz your phone within a few seconds."); })}>Send a test alert</Button>
            {s.last_sent_at ? <span className="text-xs text-ink-faint">Last sent {new Date(s.last_sent_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}</span> : null}
          </div>
          <p className="mt-3 max-w-[70ch] text-xs text-ink-faint">To make urgent alerts ring like an alarm, open this topic&apos;s settings in the ntfy app and pick a loud sound for urgent (priority 5) messages. On Android you can also let them sound during Do Not Disturb.</p>
        </Step>
      </ol>
      <details className="mt-2 border-t border-rule pt-4 text-sm">
        <summary className="cursor-pointer text-ink-soft">Your own ntfy server, or a new topic</summary>
        <div className="mt-3 flex flex-col gap-4">
          <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (server !== null) put({ server_url: server }).then(() => setServer(null)); }}>
            <Field label="ntfy server">{(id) => <Input id={id} className="w-72" value={server ?? s.server_url} onChange={(e) => setServer(e.target.value)} />}</Field>
            <Button type="submit" disabled={server === null || server === s.server_url} busy={busy === "put"}>Save</Button>
          </form>
          <div>
            <Button variant="ghost" busy={busy === "topic"} onClick={() => run("topic", async () => { await api("/notifications/topic", { method: "POST" }); await mutate(); setMsg("New topic created. Subscribe to it in ntfy; the old one gets nothing more."); })}>Create a new topic</Button>
            <p className="mt-1 text-xs text-ink-faint">Do this if someone else may have seen your topic. You&apos;ll need to subscribe again.</p>
          </div>
        </div>
      </details>
      {msg ? <p role="status" className="mt-4 text-sm text-credit">{msg}</p> : null}
      {err ? <div className="mt-4"><ErrorNote error={err} /></div> : null}
    </Panel>
  );
}

function DataPanel() {
  return (
    <Panel title="Your data">
      <p className="-mt-1 mb-4 max-w-[70ch] text-sm text-ink-soft">Take a copy whenever you like. The JSON file has everything: accounts, balances, transactions with their categories and splits, rules, budgets, goals, loans, cards, investments, people and templates. Statement files and receipt photos stay encrypted on the server.</p>
      <div className="flex flex-wrap gap-2">
        <a href="/api/bff/export/ledger.json" download className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-accent px-4 text-[15px] font-medium text-accent-ink hover:opacity-90">Download everything (JSON)</a>
        <a href="/api/bff/export/transactions.csv" download className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-rule-strong/80 bg-surface px-4 text-[15px] font-medium hover:border-ink-faint">Transactions for a spreadsheet (CSV)</a>
      </div>
      <p className="mt-3 text-xs text-ink-faint">Whoever runs this server can also make full backups with <code className="rounded bg-sunken px-1">make backup</code>.</p>
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
              <Input id={id} aria-describedby={d} list="ai-models" className="min-w-0 flex-1 basis-60" value={model ?? s.model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. anthropic/claude-opus-5" />
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
