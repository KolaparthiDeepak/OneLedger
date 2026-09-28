"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { Mark } from "@/components/icons";
import { Button, Field, Input } from "@/components/ui";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [show, setShow] = useState(false);
  const notice = params.get("expired") ? "Your session ended. Sign in again to carry on where you were." : params.get("signed_out") ? "You're signed out." : null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = (await res.json()) as { mfa_required?: boolean; error?: { message?: string } };
      if (!res.ok) {
        setError(data.error?.message ?? "Sign-in failed.");
        return;
      }
      const next = params.get("next");
      const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
      router.replace(data.mfa_required ? `/verify?next=${encodeURIComponent(safeNext)}` : safeNext);
    } catch {
      setError("Could not reach OneLedger. Check that the API is running.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
      {notice ? <p role="status" className="rounded-lg bg-accent-wash px-3.5 py-2.5 text-sm text-ink-soft">{notice}</p> : null}
      <Field label="Email">
        {(id) => <Input id={id} type="email" autoComplete="username" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} />}
      </Field>
      <Field label="Password" error={error}>
        {(id, d) => (
          <div className="relative">
            <Input id={id} type={show ? "text" : "password"} autoComplete="current-password" required value={password} aria-describedby={d} aria-invalid={!!error} className="pr-16" onChange={(e) => setPassword(e.target.value)} />
            <button type="button" onClick={() => setShow(!show)} aria-pressed={show} className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md px-2 py-1 text-xs font-medium text-ink-soft hover:bg-sunken hover:text-ink">
              {show ? "Hide" : "Show"}
            </button>
          </div>
        )}
      </Field>
      <Button variant="primary" type="submit" busy={busy}>
        Sign in
      </Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="relative flex min-h-dvh items-center justify-center overflow-hidden px-5 py-10">
      {/* Faint passbook rules behind the sign-in page. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-[repeating-linear-gradient(180deg,transparent_0_35px,var(--rule)_35px_36px)] opacity-60 [mask-image:radial-gradient(ellipse_at_center,black_20%,transparent_70%)]" />
      <div className="relative w-full max-w-[400px] rounded-2xl border border-rule bg-surface p-7 shadow-sheet sm:p-9">
        <div className="flex items-center gap-2.5">
          <Mark className="size-8" />
          <span className="display text-[1.7rem] font-semibold leading-none">OneLedger</span>
        </div>
        <p className="mb-7 mt-3 text-ink-soft">Every account, card and loan in one private ledger.</p>
        <Suspense>
          <LoginForm />
        </Suspense>
        <p className="mt-7 border-t border-rule pt-4 text-xs leading-relaxed text-ink-faint">
          New here? Ask someone who already uses this OneLedger for an invite link. There is no public sign-up.
        </p>
      </div>
    </main>
  );
}
