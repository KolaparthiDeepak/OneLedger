"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { Icon } from "@/components/icons";
import { Badge, Button, cx, ErrorNote, Field, Loading, PageHeader, Panel, Select } from "@/components/ui";
import { DeleteImport } from "@/components/delete-import";
import { api, useApi } from "@/lib/api";
import { formatDate } from "@/lib/format";

type Imp = { id: string; filename: string; format: string; state: string; account_id: string; counts: Record<string, number>; created_at: string; error: { message: string } | null };

const STATE_LABEL: Record<string, string> = {
  NEEDS_MAPPING: "Needs column mapping", PREVIEW_READY: "Ready to review", CONFIRMED: "Queued", COMMITTING: "Importing",
  COMPLETED: "Imported", FAILED: "Failed", CANCELLED: "Cancelled", DELETED: "Deleted", UPLOADED: "Uploaded", VALIDATING: "Checking",
};

function Upload() {
  const router = useRouter();
  const params = useSearchParams();
  const { data: accounts } = useApi<{ id: string; name: string }[]>("/accounts");
  const { data: imports, error, mutate } = useApi<Imp[]>("/imports");
  const [account, setAccount] = useState(params.get("account") ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [showOld, setShowOld] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setErr(null);
    try {
      const fd = new FormData();
      fd.set("account_id", account);
      fd.set("file", file);
      const imp = await api<Imp>("/imports", { method: "POST", body: fd });
      router.push(`/imports/${imp.id}`);
    } catch (e2) {
      setErr(e2);
      setBusy(false);
    }
  }

  const names = Object.fromEntries((accounts ?? []).map((a) => [a.id, a.name]));
  return (
    <>
      <PageHeader title="Import statements" description="Upload a CSV or Excel (.xls/.xlsx) statement, or a text PDF whose running balance can be verified. Nothing reaches your ledger until you review and confirm it." />
      <Panel>
        <form onSubmit={submit} className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_1.4fr_auto] sm:items-end">
          <Field label="Account">{(id) => (
            <Select id={id} required value={account} onChange={(e) => setAccount(e.target.value)}>
              <option value="">Choose an account…</option>
              {accounts?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          )}</Field>
          <Field label="Statement file">{(id) => (
            <label htmlFor={id}
              onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files?.[0]; if (f) setFile(f); }}
              className={cx("flex min-h-10 cursor-pointer items-center gap-3 rounded-lg border border-dashed px-3 py-2 text-sm transition-colors", dragging ? "border-ink bg-accent-wash" : file ? "border-credit/50 bg-credit-wash/60" : "border-rule-strong hover:border-ink-faint hover:bg-raised")}>
              <Icon name="import" className={cx("size-[18px] shrink-0", file ? "text-credit" : "text-ink-faint")} />
              <span className="min-w-0 flex-1 truncate">{file ? <span className="font-medium">{file.name}</span> : <span className="text-ink-soft">Choose a file or drop it here</span>}</span>
              {file ? <span className="num shrink-0 text-xs text-ink-faint">{(file.size / 1024).toFixed(0)} KB</span> : null}
              <input id={id} required type="file" accept=".csv,.txt,.xls,.xlsx,.pdf" className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
          )}</Field>
          <Button type="submit" variant="primary" busy={busy} disabled={!account || !file}>Upload</Button>
        </form>
        <p className="mt-3 text-xs text-ink-faint">CSV, Excel (.xls, .xlsx) or text PDF, up to 20 MB. Password-protected PDFs and scanned images are not supported.</p>
        {err ? <div className="mt-3"><ErrorNote error={err} /></div> : null}
      </Panel>
      <div className="mb-3 mt-10 flex items-baseline justify-between gap-3">
        <h2 className="text-[15px] font-semibold">Recent imports</h2>
        {imports?.some((i) => i.state === "DELETED" || i.state === "CANCELLED") ? (
          <button className="text-sm text-ink-soft underline underline-offset-2 hover:text-ink" onClick={() => setShowOld(!showOld)}>
            {showOld ? "Hide cancelled and deleted" : `Show cancelled and deleted (${imports.filter((i) => i.state === "DELETED" || i.state === "CANCELLED").length})`}
          </button>
        ) : null}
      </div>
      {error ? <ErrorNote error={error} /> : !imports ? <Loading /> : imports.length === 0 ? <p className="text-sm text-ink-soft">No imports yet. Your uploaded statements will be listed here.</p> : (
        <ul className="rounded-xl border border-rule bg-surface px-5">
          {imports.filter((i) => showOld || (i.state !== "DELETED" && i.state !== "CANCELLED")).map((i) => (
            <li key={i.id} className={cx("border-b border-rule py-3 last:border-0", i.state === "DELETED" && "opacity-60")}>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <span aria-hidden className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg bg-sunken text-[10px] font-semibold uppercase text-ink-soft">{i.format}</span>
                <Link href={`/imports/${i.id}`} className="min-w-[60%] flex-1 rounded-md hover:underline hover:underline-offset-4 sm:min-w-0">
                  <span className="block truncate font-medium">{i.filename}</span>
                  <span className="flex flex-wrap gap-x-2.5 text-[13px] text-ink-faint">
                    <span>{names[i.account_id] ?? "Account"}</span>
                    <span>{formatDate(i.created_at.slice(0, 10))}</span>
                    {i.counts.inserted !== undefined ? <span>{i.counts.inserted} added{i.counts.linked ? `, ${i.counts.linked} already present` : ""}</span> : null}
                  </span>
                </Link>
                <span className="ml-auto sm:ml-0"><Badge tone={i.state === "COMPLETED" ? "credit" : i.state === "FAILED" ? "debit" : i.state === "PREVIEW_READY" || i.state === "NEEDS_MAPPING" ? "review" : "neutral"}>{STATE_LABEL[i.state] ?? i.state}</Badge></span>
                {["COMPLETED", "FAILED", "NEEDS_MAPPING", "PREVIEW_READY", "CANCELLED"].includes(i.state) ? (
                  <DeleteImport id={i.id} size="sm" quiet onDeleted={() => mutate()} />
                ) : <span className="w-[60px]" aria-hidden />}
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export default function ImportsPage() {
  return <Suspense fallback={<Loading />}><Upload /></Suspense>;
}
