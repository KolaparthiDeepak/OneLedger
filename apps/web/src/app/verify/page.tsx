"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { Mark } from "@/components/icons";
import { api, ApiError } from "@/lib/api";
import { Button, Field, Input } from "@/components/ui";

function Verify() {
  const router = useRouter();
  const params = useSearchParams();
  const enrol = params.get("enrol") === "1";
  const [code, setCode] = useState("");
  const [secret, setSecret] = useState<{ otpauth_uri: string; secret: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (enrol) api<{ otpauth_uri: string; secret: string }>("/auth/mfa/enroll", { method: "POST" }).then(setSecret).catch((e: ApiError) => setError(e.message));
  }, [enrol]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(enrol ? "/auth/mfa/enroll/confirm" : "/auth/mfa/verify", { method: "POST", json: { code } });
      const next = params.get("next");
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Verification failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      {enrol ? (
        <div className="text-sm text-ink-soft">
          <p>This deployment requires two-step verification. Add OneLedger to your authenticator app with this key, then enter the 6-digit code.</p>
          {secret ? <p className="num mt-3 break-all rounded-md bg-sunken p-3 font-medium text-ink">{secret.secret}</p> : null}
        </div>
      ) : (
        <p className="text-sm text-ink-soft">Enter the 6-digit code from your authenticator app.</p>
      )}
      <Field label="Verification code" error={error}>
        {(id, d) => <Input id={id} inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" maxLength={6} value={code} aria-describedby={d} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />}
      </Field>
      <Button variant="primary" type="submit" busy={busy} disabled={code.length !== 6}>
        Verify
      </Button>
    </form>
  );
}

export default function VerifyPage() {
  return (
    <main className="relative flex min-h-dvh items-center justify-center overflow-hidden px-5 py-10">
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-[repeating-linear-gradient(180deg,transparent_0_35px,var(--rule)_35px_36px)] opacity-60 [mask-image:radial-gradient(ellipse_at_center,black_20%,transparent_70%)]" />
      <div className="relative w-full max-w-[400px] rounded-2xl border border-rule bg-surface p-7 shadow-sheet sm:p-9">
        <Mark className="size-8" />
        <h1 className="display mb-5 mt-4 text-[1.7rem] font-medium leading-tight">Two-step verification</h1>
        <Suspense>
          <Verify />
        </Suspense>
      </div>
    </main>
  );
}
