"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, useApi } from "@/lib/api";
import { Icon } from "./icons";
import { cx } from "./ui";

export type Alert = { key: string; severity: "high" | "medium" | "low"; title: string; detail: string; href: string };

const DOT = { high: "bg-debit", medium: "bg-review", low: "bg-ink-faint" };

export function AlertList({ alerts, onDismiss, onNavigate, compact }: { alerts: Alert[]; onDismiss: (key: string) => void; onNavigate?: () => void; compact?: boolean }) {
  return (
    <ul className="flex flex-col divide-y divide-rule">
      {alerts.map((a) => (
        <li key={a.key} className="flex items-start gap-3 py-2.5">
          <span aria-hidden className={cx("mt-1.5 size-2 shrink-0 rounded-full", DOT[a.severity])} />
          <Link href={a.href} onClick={onNavigate} className="min-w-0 flex-1 rounded-md hover:text-ink">
            <span className="block text-sm font-medium">{a.title}</span>
            {!compact || a.severity === "high" ? <span className="block text-xs text-ink-soft">{a.detail}</span> : null}
          </Link>
          <button type="button" onClick={() => onDismiss(a.key)} aria-label={`Dismiss: ${a.title}`} title="Dismiss"
            className="-mr-1 inline-flex size-7 shrink-0 items-center justify-center rounded-md text-ink-faint hover:bg-sunken hover:text-ink">
            <Icon name="close" className="size-4" />
          </button>
        </li>
      ))}
    </ul>
  );
}

/** Header bell: things that need attention now (bills due, budgets, unusual spending, missed payments). */
export function AlertsBell() {
  const { data, mutate } = useApi<Alert[]>("/insights/alerts", { dedupingInterval: 60_000 });
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const alerts = data ?? [];
  const urgent = alerts.filter((a) => a.severity !== "low").length;

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  async function dismiss(key: string) {
    await mutate(alerts.filter((a) => a.key !== key), { revalidate: false });
    await api("/insights/alerts/dismiss", { method: "POST", json: { key } }).catch(() => mutate());
  }

  const label = alerts.length ? `Alerts, ${alerts.length}` : "Alerts, none";
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="dialog" aria-label={label} title="Alerts"
        className="relative inline-flex size-9 items-center justify-center rounded-lg text-ink-soft hover:bg-sunken hover:text-ink">
        <Icon name="bell" className="size-5" />
        {alerts.length ? (
          <span className={cx("num absolute -right-0.5 -top-0.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full px-1 text-[10.5px] font-semibold text-surface", urgent ? "bg-debit" : "bg-ink-soft")}>
            {alerts.length > 9 ? "9+" : alerts.length}
          </span>
        ) : null}
      </button>
      {open ? (
        <div role="dialog" aria-label="Alerts" className="absolute right-0 top-11 z-40 w-[min(92vw,380px)] rounded-xl border border-rule bg-surface p-4 shadow-sheet">
          <p className="mb-1 text-sm font-semibold">Needs attention</p>
          {alerts.length ? <AlertList alerts={alerts} onDismiss={dismiss} onNavigate={() => setOpen(false)} /> : <p className="py-3 text-sm text-ink-soft">Nothing right now. Bills due soon, budgets running out and unusual spending show up here.</p>}
        </div>
      ) : null}
    </div>
  );
}
