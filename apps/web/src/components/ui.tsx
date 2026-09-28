"use client";

import Link from "next/link";
import { forwardRef, useEffect, useId, useRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";
import { ApiError } from "@/lib/api";
import { formatDate, formatMoney, isNegative, periodLabel } from "@/lib/format";

function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-ink shadow-[inset_0_-1px_0_rgb(0_0_0/0.18)] hover:opacity-90 disabled:opacity-40",
  secondary: "bg-surface text-ink border border-rule-strong/80 hover:border-ink-faint hover:bg-raised disabled:opacity-50",
  ghost: "text-ink-soft hover:text-ink hover:bg-sunken disabled:opacity-50",
  danger: "bg-surface text-debit border border-debit/35 hover:bg-debit-wash disabled:opacity-50",
};

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean; size?: "sm" | "md" }>(
  function Button({ variant = "secondary", busy, size = "md", className, children, disabled, ...rest }, ref) {
    return (
      <button
        ref={ref}
        className={cx(
          "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium transition-[background-color,border-color,opacity] duration-150",
          size === "sm" ? "min-h-8 px-3 text-sm" : "min-h-10 px-4 text-[15px]",
          VARIANTS[variant],
          className,
        )}
        disabled={disabled || busy}
        aria-busy={busy || undefined}
        {...rest}
      >
        {busy ? <span className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden /> : null}
        {children}
      </button>
    );
  },
);

export function ButtonLink({ href, children, variant = "secondary", className }: { href: string; children: ReactNode; variant?: Variant; className?: string }) {
  return (
    <Link href={href} className={cx("inline-flex min-h-10 items-center justify-center gap-2 whitespace-nowrap rounded-lg px-4 text-[15px] font-medium transition-[background-color,border-color,opacity] duration-150", VARIANTS[variant], className)}>
      {children}
    </Link>
  );
}

export function Field({ label, hint, error, children }: { label: string; hint?: ReactNode; error?: string | null; children: (id: string, describedBy?: string) => ReactNode }) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errId = error ? `${id}-err` : undefined;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium text-ink-soft">
        {label}
      </label>
      {children(id, [hintId, errId].filter(Boolean).join(" ") || undefined)}
      {hint ? (
        <p id={hintId} className="text-xs text-ink-faint">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errId} className="text-sm text-debit">
          {error}
        </p>
      ) : null}
    </div>
  );
}

const inputCls =
  "min-h-10 w-full rounded-lg border border-rule-strong/80 bg-surface px-3 text-ink shadow-[inset_0_1px_0_rgb(0_0_0/0.03)] placeholder:text-ink-faint transition-colors hover:border-ink-faint focus:border-ink focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-0 disabled:opacity-60";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={cx(inputCls, className)} {...rest} />;
});

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx(inputCls, "cursor-pointer pr-8", className)} {...rest}>
      {children}
    </select>
  );
}

/** An amount with sign, currency and direction conveyed by text, not colour alone. */
export function Amount({ value, currency = "INR", className, colored = true, signed = true, absolute = false }: { value: string | null | undefined; currency?: string; className?: string; colored?: boolean; signed?: boolean; absolute?: boolean }) {
  const neg = isNegative(value);
  const zero = value == null || /^[-+]?0*(\.0*)?$/.test(value);
  return (
    <span className={cx("num whitespace-nowrap", colored && !zero && (neg ? "text-debit" : "text-credit"), className)}>
      {formatMoney(value, currency, { signed: signed && !absolute, absolute })}
    </span>
  );
}

type Prov = {
  query_id?: string;
  start_date?: string | null;
  end_date_exclusive?: string | null;
  transaction_count?: number;
  partial?: boolean;
  warnings?: string[];
  evidence_url?: string;
  ledger_revision?: number;
};

/** The provenance line printed under every figure: period, count, completeness and evidence. */
export function Provenance({ p, showWarnings = false, hidePartial = false, hideWarning }: { p?: Prov | null; showWarnings?: boolean; hidePartial?: boolean; hideWarning?: (w: string) => boolean }) {
  if (!p) return null;
  const period = periodLabel(p.start_date, p.end_date_exclusive);
  return (
    <div className="mt-3 text-xs leading-relaxed text-ink-faint">
      <span>{period}</span>
      {typeof p.transaction_count === "number" && p.transaction_count > 0 ? <span>{period ? ", " : ""}{p.transaction_count} transactions</span> : null}
      {p.partial && !hidePartial ? <span className="ml-2 inline-flex rounded-md bg-review-wash px-1.5 py-px align-baseline text-review">Incomplete data</span> : null}
      {p.query_id && p.evidence_url?.startsWith("/transactions") ? (
        <Link className="ml-2 underline decoration-dotted underline-offset-2 hover:text-ink" href={p.evidence_url}>
          See transactions
        </Link>
      ) : null}
      {showWarnings && p.warnings?.filter((w) => !hideWarning?.(w)).length ? (
        <ul className="mt-1 list-disc pl-4">
          {p.warnings.filter((w) => !hideWarning?.(w)).map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-7 flex flex-wrap items-end justify-between gap-x-6 gap-y-3 border-b border-rule pb-5">
      <div className="min-w-0">
        <h1 className="display text-[2rem] font-medium leading-tight [overflow-wrap:anywhere] sm:text-[2.35rem]">{title}</h1>
        {description ? <p className="mt-1.5 max-w-[68ch] text-[15px] text-ink-soft">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
    </header>
  );
}

export function Panel({ title, action, children, className }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("min-w-0 rounded-xl border border-rule bg-surface p-5 sm:p-6", className)}>
      {title ? (
        <div className="mb-4 flex items-baseline justify-between gap-3">
          <h2 className="text-[15px] font-semibold tracking-[-0.005em]">{title}</h2>
          {action ? <div className="text-sm text-ink-soft">{action}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2.5 py-10 text-sm text-ink-faint">
      <span className="size-4 animate-spin rounded-full border-2 border-rule-strong border-t-ink-soft" aria-hidden />
      {label}…
    </div>
  );
}

export function ErrorNote({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof ApiError ? error.message : "Something went wrong.";
  return (
    <div role="alert" className="rounded-lg border border-debit/30 bg-debit-wash px-3.5 py-2.5 text-sm text-debit">
      {msg}
      {onRetry ? (
        <button className="ml-3 font-medium underline underline-offset-2" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-rule-strong bg-surface/40 px-6 py-14 text-center">
      <p className="display text-xl font-medium">{title}</p>
      {children ? <div className="mx-auto mt-2 max-w-[52ch] text-sm text-ink-soft">{children}</div> : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "credit" | "debit" | "review"; children: ReactNode }) {
  const tones = {
    neutral: "bg-sunken text-ink-soft",
    credit: "bg-credit-wash text-credit",
    debit: "bg-debit-wash text-debit",
    review: "bg-review-wash text-review",
  };
  return <span className={cx("inline-flex items-center whitespace-nowrap rounded-md px-1.5 py-px text-xs font-medium leading-5", tones[tone])}>{children}</span>;
}

/** Accessible modal/sheet built on <dialog>: focus trap, Escape to close, bottom sheet on mobile. */
export function Sheet({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      d.showModal();
      // Start in the first field, not on the close button.
      const first = d.querySelector<HTMLElement>("input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled])");
      if (first) first.focus();
      else d.querySelector<HTMLElement>("[data-sheet-body]")?.focus();
    }
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby="sheet-title"
      className={cx(
        "m-0 mt-auto max-h-[92vh] w-full max-w-none overflow-y-auto rounded-t-2xl border border-rule bg-surface p-0 text-ink shadow-sheet sm:m-auto sm:rounded-2xl",
        wide ? "sm:max-w-3xl" : "sm:max-w-lg",
      )}
    >
      {open ? (
        <div>
          <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-rule bg-surface/95 px-5 py-3.5 backdrop-blur sm:px-6">
            <h2 id="sheet-title" className="text-[15px] font-semibold">
              {title}
            </h2>
            <button onClick={onClose} className="-mr-1.5 inline-flex size-9 items-center justify-center rounded-lg text-ink-soft hover:bg-sunken hover:text-ink" aria-label="Close">
              <svg aria-hidden="true" viewBox="0 0 24 24" className="size-[18px]" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
            </button>
          </div>
          <div data-sheet-body tabIndex={-1} className="px-5 pb-6 pt-5 outline-none sm:px-6">{children}</div>
        </div>
      ) : null}
    </dialog>
  );
}

export function DateText({ value, withYear = true }: { value: string | null | undefined; withYear?: boolean }) {
  return <time dateTime={value ?? undefined}>{formatDate(value, withYear)}</time>;
}

export function LedgerLine({ label, value, currency, sub, strong }: { label: ReactNode; value: string | null | undefined; currency?: string; sub?: ReactNode; strong?: boolean }) {
  return (
    <div className="py-2">
      <div className="flex items-baseline gap-3">
        <span className={cx(strong ? "font-semibold" : "text-ink-soft")}>{label}</span>
        <span className="leader" aria-hidden />
        <Amount value={value} currency={currency} colored={false} signed={false} className={cx(strong ? "font-semibold" : "")} />
      </div>
      {sub ? <div className="mt-0.5 text-xs text-ink-faint">{sub}</div> : null}
    </div>
  );
}

export { cx };

/** A labelled share meter (display only; the numbers beside it are the authoritative figures). */
export function Meter({ value, max, tone = "ink", label }: { value: number; max: number; tone?: "ink" | "credit" | "debit" | "review"; label: string }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  const fill = { ink: "bg-ink/70", credit: "bg-credit", debit: "bg-debit", review: "bg-review" }[tone];
  return (
    <div role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)} className="h-1.5 overflow-hidden rounded-full bg-sunken">
      <div className={cx("h-full rounded-full", fill)} style={{ width: `${Math.max(pct, pct > 0 ? 1.5 : 0)}%` }} />
    </div>
  );
}
