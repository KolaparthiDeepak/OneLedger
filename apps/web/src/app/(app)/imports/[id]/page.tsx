"use client";

import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";
import { Amount, Badge, Button, ButtonLink, ErrorNote, Field, Loading, PageHeader, Panel, Select } from "@/components/ui";
import { DeleteImport } from "@/components/delete-import";
import { api, qs, useApi } from "@/lib/api";
import { formatDate } from "@/lib/format";

type Imp = {
  id: string; filename: string; format: string; state: string; account_id: string; headers: string[];
  suggested_mapping: Record<string, unknown>; mapping: Mapping | null; preview_hash: string | null;
  counts: Record<string, number | Record<string, Record<string, string>>>; date_min: string | null; date_max: string | null;
  is_replay: boolean; error: { code: string; message: string; stage: string } | null; job: { status: string; error_code: string | null } | null;
};
type Mapping = {
  date_column: string; date_format: string; value_date_column?: string | null; description_columns: string[];
  amount_mode: "split" | "signed" | "drcr_column"; debit_column?: string | null; credit_column?: string | null;
  amount_column?: string | null; signed_positive_means?: "credit" | "debit"; drcr_column?: string | null;
  reference_column?: string | null; balance_column?: string | null; currency: string;
};
type Row = { row_index: number; source_row_number: number; transaction_date: string | null; amount: string | null; description: string; status: string; skipped: boolean; errors: string[]; dedup_reason: string | null; resolution: string | null; raw: Record<string, string> };

const FORMATS = ["DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD", "DD-MM-YYYY", "DD.MM.YYYY", "DD/MM/YY", "MM/DD/YY", "DD-MM-YY", "DD-MMM-YYYY", "DD MMM YYYY", "DD-MMM-YY", "DD MMM YY", "DD/MMM/YYYY", "YYYY/MM/DD"];
const ERR: Record<string, string> = {
  invalid_date: "date not in the chosen format", missing_amount: "no amount", invalid_amount: "amount not a number",
  amount_both_debit_and_credit: "both withdrawal and deposit filled", zero_amount: "zero amount", missing_description: "no description",
  missing_date: "no date", amount_unknown_drcr_flag: "unknown Dr/Cr marker", invalid_balance: "balance not a number", invalid_value_date: "value date not valid",
};

export default function ImportDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { data: imp, error, mutate } = useApi<Imp>(`/imports/${id}`, {
    refreshInterval: (d) => (d && ["CONFIRMED", "COMMITTING"].includes(d.state) ? 1500 : 0),
  });
  if (error) return <ErrorNote error={error} />;
  if (!imp) return <Loading />;
  return (
    <>
      <PageHeader title={imp.filename} description={imp.is_replay ? "You imported this exact file before. Rows already in your ledger are linked, not added again." : undefined}
        actions={<ButtonLink href="/imports">All imports</ButtonLink>} />
      {imp.error && imp.state === "PREVIEW_READY" ? (
        <p role="status" className="mb-4 rounded-md bg-review-wash px-3 py-2 text-sm text-review">{imp.error.message}</p>
      ) : null}
      {imp.state === "FAILED" && imp.error ? (
        <Panel>
          <p className="text-debit">{imp.error.message}</p>
          <p className="mt-2 text-sm text-ink-soft">Nothing from this file was added to your ledger.</p>
          <div className="mt-3"><DeleteImport id={id} onDeleted={() => mutate()} /></div>
          {imp.error.stage === "COMMITTING" ? <Button className="mt-3" onClick={() => api(`/imports/${id}/retry`, { method: "POST" }).then(() => mutate())}>Retry import</Button> : null}
          {imp.error.stage === "PARSING" ? (
            <Button className="mt-3" onClick={() => api<{ id: string }>(`/imports/${id}/reparse`, { method: "POST" }).then((n) => router.push(`/imports/${n.id}`))}>
              Try reading this file again
            </Button>
          ) : null}
        </Panel>
      ) : imp.state === "NEEDS_MAPPING" || imp.state === "PREVIEW_READY" ? (
        <Wizard imp={imp} onChange={() => mutate()} />
      ) : imp.state === "COMPLETED" ? (
        <Done imp={imp} onDeleted={() => mutate()} />
      ) : imp.state === "CANCELLED" ? (
        <Panel><p>This import was cancelled. Nothing was added.</p></Panel>
      ) : imp.state === "DELETED" ? (
        <Panel><p>This import was deleted. The transactions it added were removed from your ledger and the file was erased.</p></Panel>
      ) : (
        <Panel><Loading label="Importing" /><p className="text-sm text-ink-soft">Rows are added in a hidden batch and appear together once every row has been saved.</p></Panel>
      )}
    </>
  );
}

function Done({ imp, onDeleted }: { imp: Imp; onDeleted: () => void }) {
  const c = imp.counts as Record<string, number>;
  const { data: aiSettings } = useApi<{ auto_categorize_ready: boolean }>("/ai/settings");
  return (
    <Panel title="Imported">
      <ul className="flex flex-col gap-1 text-sm">
        <li><span className="num font-medium">{c.inserted ?? 0}</span> new transactions added</li>
        <li><span className="num font-medium">{c.linked ?? 0}</span> already in your ledger (linked, not duplicated)</li>
        {c.rejected ? <li><span className="num font-medium">{c.rejected}</span> rows rejected. <a className="underline" href={`/api/bff/imports/${imp.id}/errors.csv`}>Download the list</a></li> : null}
        {c.review_required ? <li className="text-review"><span className="num font-medium">{c.review_required}</span> possible duplicates to review</li> : null}
        {c.transfers_auto_confirmed ? <li><span className="num font-medium">{c.transfers_auto_confirmed}</span> transfers between your accounts matched automatically</li> : null}
        {aiSettings?.auto_categorize_ready ? <li className="text-ink-soft">AI is categorising anything the rules could not, in the background.</li> : null}
        {c.transfers_suggested ? <li className="text-review"><span className="num font-medium">{c.transfers_suggested}</span> possible transfers to confirm</li> : null}
      </ul>
      <div className="mt-4 flex flex-wrap gap-2">
        <ButtonLink variant="primary" href={`/transactions?account_id=${imp.account_id}`}>View transactions</ButtonLink>
        <ButtonLink href={`/transactions?account_id=${imp.account_id}&uncategorized=1`}>Categorise what&apos;s left</ButtonLink>
        {(c.review_required || c.transfers_suggested) ? <ButtonLink href="/review">Review items</ButtonLink> : null}
      </div>
      <div className="mt-6 border-t border-rule pt-4">
        <p className="mb-2 text-sm text-ink-soft">Imported the wrong file or account? Deleting the import removes every transaction it added.</p>
        <DeleteImport id={imp.id} onDeleted={onDeleted} />
      </div>
    </Panel>
  );
}

function Wizard({ imp, onChange }: { imp: Imp; onChange: () => void }) {
  const s = imp.suggested_mapping as Partial<Mapping> & { date_format_candidates?: string[]; date_format_ambiguous?: boolean; preset_label?: string };
  const [m, setM] = useState<Mapping>(imp.mapping ?? {
    date_column: s.date_column ?? "", date_format: s.date_format ?? s.date_format_candidates?.[0] ?? "DD/MM/YYYY",
    description_columns: s.description_columns ?? [], amount_mode: s.amount_mode ?? "split", debit_column: s.debit_column ?? null,
    credit_column: s.credit_column ?? null, amount_column: s.amount_column ?? null, signed_positive_means: "credit",
    drcr_column: s.drcr_column ?? null, reference_column: s.reference_column ?? null, balance_column: s.balance_column ?? null,
    value_date_column: s.value_date_column ?? null, currency: s.currency ?? "INR",
  });
  const [editing, setEditing] = useState(imp.state === "NEEDS_MAPPING");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function saveMapping() {
    setBusy(true);
    setErr(null);
    try {
      const clean = Object.fromEntries(Object.entries(m).filter(([, v]) => v !== null && v !== "")) as Mapping;
      await api(`/imports/${imp.id}/mapping`, { method: "PUT", json: clean });
      setEditing(false);
      onChange();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  const col = (label: string, key: keyof Mapping, optional = true) => (
    <Field label={label}>{(id) => (
      <Select id={id} value={(m[key] as string | null) ?? ""} onChange={(e) => setM({ ...m, [key]: e.target.value || null })}>
        <option value="">{optional ? "Not in this file" : "Choose a column…"}</option>
        {imp.headers.map((h) => <option key={h} value={h}>{h}</option>)}
      </Select>
    )}</Field>
  );

  return (
    <div className="flex flex-col gap-6">
      {typeof s.preset_label === "string" ? (
        <p className="text-sm text-ink-soft">Recognised as a {s.preset_label}; columns were matched automatically. Check the preview below.</p>
      ) : null}
      {editing ? (
        <Panel title="Match the columns">
          <p className="mb-4 text-sm text-ink-soft">Tell OneLedger which column holds what. Money out should reduce your balance; check the preview after saving.</p>
          {s.date_format_ambiguous ? (
            <p className="mb-4 rounded-md bg-review-wash px-3 py-2 text-sm text-review">The dates in this file could be read as day/month or month/day. Choose the date format that matches your bank.</p>
          ) : null}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {col("Date column", "date_column", false)}
            <Field label="Date format">{(id) => (
              <Select id={id} value={m.date_format} onChange={(e) => setM({ ...m, date_format: e.target.value })}>
                {FORMATS.map((f) => <option key={f} value={f}>{f}{s.date_format_candidates?.includes(f) ? " (fits this file)" : ""}</option>)}
              </Select>
            )}</Field>
            <Field label="Description column">{(id) => (
              <Select id={id} value={m.description_columns[0] ?? ""} onChange={(e) => setM({ ...m, description_columns: e.target.value ? [e.target.value] : [] })}>
                <option value="">Choose a column…</option>{imp.headers.map((h) => <option key={h}>{h}</option>)}
              </Select>
            )}</Field>
            <Field label="How are amounts shown?">{(id) => (
              <Select id={id} value={m.amount_mode} onChange={(e) => setM({ ...m, amount_mode: e.target.value as Mapping["amount_mode"] })}>
                <option value="split">Two columns: money out, money in</option>
                <option value="signed">One column with + and −</option>
                <option value="drcr_column">One column plus a Dr/Cr column</option>
              </Select>
            )}</Field>
            {m.amount_mode === "split" ? (<>{col("Withdrawal (money out)", "debit_column", false)}{col("Deposit (money in)", "credit_column", false)}</>) : (
              <>
                {col("Amount column", "amount_column", false)}
                {m.amount_mode === "drcr_column" ? col("Dr/Cr column", "drcr_column", false) : (
                  <Field label="A positive amount means">{(id) => (
                    <Select id={id} value={m.signed_positive_means} onChange={(e) => setM({ ...m, signed_positive_means: e.target.value as "credit" | "debit" })}>
                      <option value="credit">Money in (typical bank export)</option><option value="debit">Money out (typical card statement)</option>
                    </Select>
                  )}</Field>
                )}
              </>
            )}
            {col("Reference / UTR (optional)", "reference_column")}
            {col("Balance (optional)", "balance_column")}
            {col("Value date (optional)", "value_date_column")}
          </div>
          {err ? <div className="mt-3"><ErrorNote error={err} /></div> : null}
          <div className="mt-4 flex gap-2">
            <Button variant="primary" busy={busy} onClick={saveMapping} disabled={!m.date_column || !m.description_columns.length}>Preview import</Button>
            {imp.state === "PREVIEW_READY" ? <Button variant="ghost" onClick={() => setEditing(false)}>Back to preview</Button> : null}
            {imp.state === "NEEDS_MAPPING" ? <Button variant="ghost" onClick={() => api(`/imports/${imp.id}/cancel`, { method: "POST" }).then(onChange)}>Cancel import</Button> : null}
          </div>
          <Sample imp={imp} m={m} />
        </Panel>
      ) : null}
      {imp.state === "PREVIEW_READY" && !editing ? <Preview imp={imp} onEdit={() => setEditing(true)} onChange={onChange} /> : null}
    </div>
  );
}

/** The first rows of the file, with the columns you picked labelled, so the mapping can be checked by eye. */
function Sample({ imp, m }: { imp: Imp; m: Mapping }) {
  const { data } = useApi<{ items: { source_row_number: number; raw: Record<string, string> }[] }>(`/imports/${imp.id}/rows?limit=5`);
  if (!data?.items.length) return null;
  const role: Record<string, string> = {};
  const put = (col: string | null | undefined, label: string) => { if (col) role[col] = role[col] ? `${role[col]}, ${label}` : label; };
  put(m.date_column, "Date");
  m.description_columns.forEach((c) => put(c, "Description"));
  if (m.amount_mode === "split") { put(m.debit_column, "Money out"); put(m.credit_column, "Money in"); }
  else { put(m.amount_column, "Amount"); if (m.amount_mode === "drcr_column") put(m.drcr_column, "Dr/Cr"); }
  put(m.balance_column, "Balance");
  put(m.reference_column, "Reference");
  put(m.value_date_column, "Value date");
  return (
    <div className="mt-6 border-t border-rule pt-5">
      <h3 className="mb-2 text-sm font-semibold">First rows of your file</h3>
      <div className="overflow-x-auto rounded-lg border border-rule">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="bg-raised">
              {imp.headers.map((h) => (
                <th key={h} scope="col" className={role[h] ? "border-b-2 border-ink px-3 py-2 align-bottom font-medium" : "border-b border-rule px-3 py-2 align-bottom font-normal text-ink-faint"}>
                  {role[h] ? <span className="mb-1 block text-[11px] font-semibold text-credit">{role[h]}</span> : null}
                  <span className="whitespace-nowrap">{h}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.items.map((r) => (
              <tr key={r.source_row_number} className="border-t border-rule">
                {imp.headers.map((h) => <td key={h} className={role[h] ? "num whitespace-nowrap px-3 py-1.5" : "num whitespace-nowrap px-3 py-1.5 text-ink-faint"}>{r.raw[h] ?? ""}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-ink-faint">Columns without a label are ignored.</p>
    </div>
  );
}

function Preview({ imp, onEdit, onChange }: { imp: Imp; onEdit: () => void; onChange: () => void }) {
  const [filter, setFilter] = useState<string>("");
  const { data, mutate } = useApi<{ items: Row[]; total: number }>(`/imports/${imp.id}/rows${qs({ status: filter || undefined, limit: 100 })}`);
  const [accept, setAccept] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const c = imp.counts as Record<string, number> & { totals?: Record<string, Record<string, string>> };
  const totals = c.totals ? Object.entries(c.totals)[0] : undefined;
  useEffect(() => { mutate(); }, [imp.preview_hash, mutate]);

  async function resolve(row: number, resolution: string) {
    await api(`/imports/${imp.id}/resolutions`, { method: "POST", json: { resolutions: { [row]: resolution } } }).catch(setErr);
    onChange();
  }

  async function confirm() {
    setBusy(true);
    setErr(null);
    try {
      await api(`/imports/${imp.id}/confirm`, { method: "POST", json: { preview_hash: imp.preview_hash, accept_rejections: accept } });
      onChange();
    } catch (e) {
      setErr(e);
      onChange();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Panel title="Check before importing">
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-rule bg-rule sm:grid-cols-4">
          <Stat label="Will be added" value={c.to_import} />
          <Stat label="Already in ledger" value={c.to_link} />
          <Stat label="Possible duplicates" value={c.possible_duplicate} tone={c.possible_duplicate ? "review" : undefined} />
          <Stat label="Rejected rows" value={c.invalid} tone={c.invalid ? "debit" : undefined} />
        </div>
        {totals ? (
          <p className="mt-4 text-sm">
            Adding <Amount value={totals[1].inflow} currency={totals[0]} colored={false} signed={false} /> in and <Amount value={totals[1].outflow} currency={totals[0]} colored={false} signed={false} /> out,
            {" "}from {formatDate(imp.date_min)} to {formatDate(imp.date_max)}.
          </p>
        ) : null}
        <p className="mt-1 text-xs text-ink-faint">Money out is shown in red with a minus sign. If withdrawals look like deposits, <button className="underline" onClick={onEdit}>change the column mapping</button>.</p>
        {c.invalid ? (
          <label className="mt-4 flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={accept} onChange={(e) => setAccept(e.target.checked)} />
            Import the valid rows and leave out the {c.invalid} rejected rows. You can download them afterwards.
          </label>
        ) : null}
        {err ? <div className="mt-3"><ErrorNote error={err} /></div> : null}
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="primary" busy={busy} onClick={confirm} disabled={!!c.invalid && !accept}>Import {c.to_import} transactions</Button>
          <Button onClick={onEdit}>Change mapping</Button>
          <Button variant="ghost" onClick={() => api(`/imports/${imp.id}/cancel`, { method: "POST" }).then(onChange)}>Cancel import</Button>
        </div>
      </Panel>
      <Panel title="Rows" action={
        <Select aria-label="Show rows" value={filter} onChange={(e) => setFilter(e.target.value)} className="w-auto">
          <option value="">All rows</option><option value="POSSIBLE_DUPLICATE">Possible duplicates</option><option value="INVALID">Rejected</option><option value="DUPLICATE">Already in ledger</option><option value="VALID">New</option>
        </Select>
      }>
        {!data ? <Loading /> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="text-left text-ink-faint"><tr><th className="py-1 font-normal">Row</th><th className="font-normal">Date</th><th className="font-normal">Description</th><th className="text-right font-normal">Amount</th><th className="font-normal pl-3">Status</th></tr></thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.row_index} className="border-t border-rule align-top">
                    <td className="num py-1.5 text-ink-faint">{r.source_row_number}</td>
                    <td className="whitespace-nowrap pr-2">{r.transaction_date ? formatDate(r.transaction_date) : <span className="text-debit">—</span>}</td>
                    <td className="max-w-[28ch] truncate pr-2" title={r.description}>{r.description || Object.values(r.raw).join(" ")}</td>
                    <td className="text-right">{r.amount ? <Amount value={r.amount} /> : "—"}</td>
                    <td className="pl-3">
                      {r.skipped ? <span className="text-ink-faint">Summary line, skipped</span>
                        : r.status === "INVALID" ? <span className="text-debit">Rejected: {r.errors.map((e) => ERR[e] ?? e).join(", ")}</span>
                        : r.status === "DUPLICATE" ? <Badge>Already in ledger</Badge>
                        : r.status === "POSSIBLE_DUPLICATE" ? (
                          <span className="flex flex-wrap items-center gap-2">
                            <Badge tone="review">Looks like one you have</Badge>
                            <Select aria-label="Decision" value={r.resolution ?? "IMPORT"} onChange={(e) => resolve(r.row_index, e.target.value)} className="min-h-8 w-auto py-0 text-sm">
                              <option value="IMPORT">Import and review later</option><option value="LINK">Same transaction, don't add</option><option value="SKIP">Skip this row</option>
                            </Select>
                          </span>
                        ) : <Badge tone="credit">New</Badge>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.total > data.items.length ? <p className="mt-2 text-xs text-ink-faint">Showing the first {data.items.length} of {data.total} rows.</p> : null}
          </div>
        )}
      </Panel>
    </>
  );
}

function Stat({ label, value, tone }: { label: string; value: number | undefined; tone?: "review" | "debit" }) {
  return (
    <div className="bg-surface px-4 py-3.5">
      <p className={tone === "review" ? "display text-[2rem] font-medium leading-none text-review" : tone === "debit" ? "display text-[2rem] font-medium leading-none text-debit" : "display text-[2rem] font-medium leading-none"}>{value ?? 0}</p>
      <p className="mt-1.5 text-[13px] text-ink-soft">{label}</p>
    </div>
  );
}

