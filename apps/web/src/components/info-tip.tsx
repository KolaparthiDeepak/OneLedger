"use client";

import { useEffect, useId, useRef, useState } from "react";

/** Small ⓘ button that shows a short explanation on tap/click (and hover on desktop); tap outside or Escape closes it. */
export function InfoTip({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);

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

  return (
    <span ref={ref} className="relative inline-flex align-middle" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" aria-label={label} aria-expanded={open} aria-describedby={open ? id : undefined} onClick={() => setOpen(true)}
        className="ml-1 inline-flex size-5 items-center justify-center rounded-full text-sm leading-none text-ink-faint hover:text-ink">
        ⓘ
      </button>
      {open ? (
        <span role="tooltip" id={id}
          className="absolute left-0 top-full z-20 mt-1 w-64 rounded-md border border-rule bg-surface p-3 text-left text-xs font-normal leading-relaxed text-ink-soft shadow-lg">
          {children}
        </span>
      ) : null}
    </span>
  );
}
