"use client";

import { useState } from "react";
import { Button, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";

type Preview = { transactions_removed: number; shared_transactions_kept: number; state: string };

/** Two-step, in-page confirmation (no browser dialog) before an import and its transactions are removed. */
export function DeleteImport({ id, onDeleted, size = "md", quiet = false }: { id: string; onDeleted: () => void; size?: "sm" | "md"; quiet?: boolean }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  async function ask() {
    setErr(null);
    setBusy(true);
    try {
      setPreview(await api<Preview>(`/imports/${id}/delete-preview`));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setErr(null);
    try {
      await api(`/imports/${id}/delete`, { method: "POST", json: { confirm: true } });
      setPreview(null);
      onDeleted();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  if (!preview) {
    return (
      <span className="inline-flex flex-col items-end gap-2">
        {quiet ? (
          <Button variant="ghost" size="sm" busy={busy} onClick={ask} aria-label="Delete import" className="text-ink-faint hover:text-debit">Delete</Button>
        ) : (
          <Button variant="danger" size={size} busy={busy} onClick={ask}>Delete import</Button>
        )}
        {err ? <ErrorNote error={err} /> : null}
      </span>
    );
  }
  const n = preview.transactions_removed;
  return (
    <div role="alertdialog" aria-label="Confirm delete" className="flex w-full basis-full flex-col gap-3 rounded-xl border border-debit/30 bg-debit-wash p-4 text-sm">
      <p>
        {preview.state === "COMPLETED"
          ? <>Delete this statement and the <strong className="num">{n}</strong> transaction{n === 1 ? "" : "s"} it added? Transfers matched to them are undone and totals are recalculated.</>
          : <>Discard this import? Nothing from it is in your ledger.</>}
        {preview.shared_transactions_kept ? <> {preview.shared_transactions_kept} transaction{preview.shared_transactions_kept === 1 ? "" : "s"} that were already in your ledger before this file are kept.</> : null}
        {" "}The uploaded file is erased.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button variant="danger" size={size} busy={busy} onClick={remove}>{n ? `Delete import and ${n} transactions` : "Delete import"}</Button>
        <Button variant="ghost" size={size} onClick={() => setPreview(null)}>Keep it</Button>
      </div>
      {err ? <ErrorNote error={err} /> : null}
    </div>
  );
}
