"use client";

import { NetWorthChart } from "@/components/charts";
import { Amount, ErrorNote, Loading, PageHeader, Panel, Provenance } from "@/components/ui";
import { useApi } from "@/lib/api";
import { formatDate, KIND_LABEL } from "@/lib/format";

type NW = {
  data: {
    totals: Record<string, { assets: string; liabilities: string; net_worth: string }>;
    components: { key: string; label: string; nature: string; kind: string; currency: string; value: string | null; as_of: string | null; stale: boolean }[];
    missing: string[];
    changes: Record<string, { available: boolean; reason: string; delta: Record<string, string> }>;
  };
  provenance: { partial: boolean; warnings: string[] };
};

export default function NetWorthPage() {
  const { data, error } = useApi<NW>("/analytics/net-worth");
  const { data: hist } = useApi<{ points: { date: string; currency: string; net_worth: string; partial: boolean; source: string }[] }>("/analytics/net-worth/history?days=730");
  if (error) return <ErrorNote error={error} />;
  if (!data) return <Loading />;
  const inr = data.data.totals.INR;
  const assets = data.data.components.filter((c) => c.nature === "ASSET");
  const liabilities = data.data.components.filter((c) => c.nature === "LIABILITY");
  const reason: Record<string, string> = { partial_coverage: "not enough data", coverage_changed: "accounts changed since then", comparable: "" };
  return (
    <>
      <PageHeader title="Net worth" description="Assets minus what you owe, using each account's latest known balance. Transfers between your accounts never change it." />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_1.3fr]">
        <Panel>
          <p className="text-sm text-ink-soft">Today</p>
          {inr ? <p className="display mt-1 text-[2.6rem] font-medium leading-none"><Amount value={inr.net_worth} colored={false} signed={false} /></p> : <p>No balances yet.</p>}
          {Object.values(data.data.changes).some((c) => c.available && c.delta.INR) ? (
            <dl className="mt-5 grid grid-cols-3 gap-3 border-t border-rule pt-4 text-sm">
              {Object.entries(data.data.changes).map(([k, c]) => (
                <div key={k}>
                  <dt className="text-xs text-ink-faint">{k === "1y" ? "1 year" : k.replace("d", " days")}</dt>
                  <dd className="font-medium">{c.available && c.delta.INR ? <Amount value={c.delta.INR} /> : <span className="font-normal text-ink-faint">{reason[c.reason] ? `Not comparable: ${reason[c.reason]}` : "Unavailable"}</span>}</dd>
                </div>
              ))}
            </dl>
          ) : <p className="mt-4 border-t border-rule pt-4 text-sm text-ink-faint">Changes over 30 days, 90 days and a year appear once there is enough history to compare.</p>}
          <Provenance p={data.provenance} showWarnings />
        </Panel>
        <Panel title="History">
          {hist && hist.points.length > 1 ? (
            <>
              <NetWorthChart points={hist.points.filter((p) => p.currency === "INR")} />
              {hist.points.some((p) => p.source === "computed") ? <p className="mt-1 text-xs text-ink-faint">Month ends before daily tracking began are rebuilt from your recorded balances.</p> : null}
            </>
          ) : <p className="text-sm text-ink-soft">The chart appears once there are balances on at least two dates. Import a statement with a balance column, or record a balance on an account.</p>}
        </Panel>
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        {[{ title: "What you own", rows: assets, total: inr?.assets }, { title: "What you owe", rows: liabilities, total: inr?.liabilities }].map((g) => (
          <Panel key={g.title} title={g.title}>
            <ul className="text-sm">
              {g.rows.map((c) => (
                <li key={c.key} className="flex items-baseline justify-between gap-3 border-b border-rule py-2.5 last:border-0">
                  <span className="min-w-0"><span className="block truncate">{c.label}</span><span className={c.stale ? "text-xs text-review" : "text-xs text-ink-faint"}>{KIND_LABEL[c.kind] ?? c.kind.toLowerCase()}{c.as_of ? `, ${formatDate(c.as_of)}` : ""}{c.stale ? ", out of date" : ""}</span></span>
                  {c.value === null ? <span className="text-review">Unknown</span> : <Amount value={c.value} currency={c.currency} colored={false} signed={false} />}
                </li>
              ))}
            </ul>
            {g.total ? <div className="mt-2 flex justify-between border-t-2 border-double border-rule-strong pt-3 font-semibold"><span>Total</span><Amount value={g.total} colored={false} signed={false} /></div> : null}
          </Panel>
        ))}
      </div>
    </>
  );
}
