"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useState, type FormEvent } from "react";
import { Mark } from "@/components/icons";
import { Button, Field, Input, Loading } from "@/components/ui";

type Check = { email: string | null; invited_by: string; expires_at: string };
type ApiErr = { error?: { code?: string; message?: string; details?: { fields?: { loc: string[]; msg: string }[] } } };

export default function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const router = useRouter();
  const [check, setCheck] = useState<Check | null>(null);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/api/auth/invite/check", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ token }) })
      .then(async (r) => {
        const data = (await r.json()) as Check & ApiErr;
        if (!r.ok) setInvalid(data.error?.message ?? "This invite link is not valid.");
        else { setCheck(data); if (data.email) setEmail(data.email); }
      })
      .catch(() => setInvalid("Could not reach OneLedger. Try again in a moment."));
  }, [token]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < 12) return setError("Use at least 12 characters for your password.");
    if (password !== repeat) return setError("The two passwords don't match.");
    setBusy(true);
    try {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kolkata";
      const r = await fetch("/api/auth/invite/accept", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token, email, name, password, timezone }),
      });
      const data = (await r.json()) as ApiErr;
      if (!r.ok) {
        setError(data.error?.details?.fields?.[0]?.msg ?? data.error?.message ?? "Could not create your ledger.");
        return;
      }
      router.replace("/onboarding");
    } catch {
      setError("Could not reach OneLedger. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="relative flex min-h-dvh items-center justify-center overflow-hidden px-5 py-10">
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-[repeating-linear-gradient(180deg,transparent_0_35px,var(--rule)_35px_36px)] opacity-60 [mask-image:radial-gradient(ellipse_at_center,black_20%,transparent_70%)]" />
      <div className="relative w-full max-w-[420px] rounded-2xl border border-rule bg-surface p-7 shadow-sheet sm:p-9">
        <div className="flex items-center gap-2.5">
          <Mark className="size-8" />
          <span className="display text-[1.7rem] font-semibold leading-none">OneLedger</span>
        </div>
        {invalid ? (
          <>
            <h1 className="display mb-2 mt-6 text-2xl font-medium">This link can&apos;t be used</h1>
            <p className="text-ink-soft">{invalid}</p>
            <p className="mt-6 text-sm">Already have a ledger here? <Link className="font-medium underline underline-offset-4" href="/login">Sign in</Link></p>
          </>
        ) : !check ? <Loading label="Checking your invite" /> : (
          <>
            <h1 className="display mt-6 text-2xl font-medium leading-snug">{check.invited_by} invited you</h1>
            <p className="mb-6 mt-2 text-ink-soft">Create your own private ledger. Only you can see what you add; the person who invited you can&apos;t, and you can&apos;t see theirs.</p>
            <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
              <Field label="Your name">{(id) => <Input id={id} autoFocus required maxLength={120} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
              <Field label="Email" hint={check.email ? "This invite is for this address." : "You'll sign in with this."}>{(id, d) => (
                <Input id={id} aria-describedby={d} type="email" required autoComplete="username" value={email} readOnly={!!check.email} className={check.email ? "bg-sunken! text-ink-soft" : undefined} onChange={(e) => setEmail(e.target.value)} />
              )}</Field>
              <Field label="Password" hint="At least 12 characters.">{(id, d) => (
                <div className="relative">
                  <Input id={id} aria-describedby={d} type={show ? "text" : "password"} required autoComplete="new-password" className="pr-16" value={password} onChange={(e) => setPassword(e.target.value)} />
                  <button type="button" onClick={() => setShow(!show)} aria-pressed={show} className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md px-2 py-1 text-xs font-medium text-ink-soft hover:bg-sunken hover:text-ink">{show ? "Hide" : "Show"}</button>
                </div>
              )}</Field>
              <Field label="Repeat password" error={error}>{(id, d) => (
                <Input id={id} aria-describedby={d} aria-invalid={!!error} type={show ? "text" : "password"} required autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
              )}</Field>
              <Button variant="primary" type="submit" busy={busy} disabled={!name.trim() || !email || !password || !repeat}>Create my ledger</Button>
            </form>
            <p className="mt-6 border-t border-rule pt-4 text-xs text-ink-faint">
              This link works once and expires on {new Date(check.expires_at).toLocaleDateString("en-IN", { day: "numeric", month: "long" })}.
            </p>
          </>
        )}
      </div>
    </main>
  );
}
