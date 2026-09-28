// Small line icons (1.6px stroke, 20px grid) used in navigation and a few buttons.
import type { SVGProps } from "react";

const PATHS: Record<string, string> = {
  home: "M3.5 9.5 10 4l6.5 5.5M5.5 8v8h9V8",
  transactions: "M4 6h12M4 10h12M4 14h8",
  accounts: "M3 8h14M5 8v7M9 8v7M11 8v7M15 8v7M3 16.5h14M10 3l7 3.5H3z",
  import: "M10 3v9m0 0-3.5-3.5M10 12l3.5-3.5M4 13v3h12v-3",
  review: "M4 10.5l3.5 3.5L16 5.5",
  ask: "M4 5.5h12v7.5H9l-3.5 3v-3H4z",
  networth: "M3.5 15.5l4-5 3 3 6-7.5M12.5 6h4v4",
  cards: "M3 5.5h14v9H3zM3 8.5h14M6 12h3",
  loans: "M10 3.5v13M13.5 6.5c0-1.4-1.6-2.3-3.5-2.3S6.5 5 6.5 6.5 8 8.5 10 9s3.5 1 3.5 2.5S12 14 10 14s-3.5-.9-3.5-2.3",
  investments: "M4 16V11M8 16V8M12 16v-5M16 16V5",
  recurring: "M15.5 8A6 6 0 0 0 4.5 7M4.5 12a6 6 0 0 0 11 1M15.5 4v4h-4M4.5 16v-4h4",
  budgets: "M10 3.5a6.5 6.5 0 1 0 6.5 6.5H10z M12 3.8A6.5 6.5 0 0 1 16.2 8H12z",
  categories: "M4 4h5v5H4zM11 4h5v5h-5zM4 11h5v5H4zM11 11h5v5h-5z",
  settings: "M10 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM10 2.8v2M10 15.2v2M17.2 10h-2M4.8 10h-2M15.1 4.9l-1.4 1.4M6.3 13.7l-1.4 1.4M15.1 15.1l-1.4-1.4M6.3 6.3 4.9 4.9",
  signout: "M8 4H4.5v12H8M12 6.5 15.5 10 12 13.5M15.5 10H8",
  more: "M4.5 10h.01M10 10h.01M15.5 10h.01",
  chevron: "M8 5l5 5-5 5",
  download: "M10 3.5v9m0 0-3.5-3.5M10 12.5l3.5-3.5M4 15.5h12",
  plus: "M10 4.5v11M4.5 10h11",
  search: "M9 15a6 6 0 1 0 0-12 6 6 0 0 0 0 12zM13.5 13.5 17 17",
  filter: "M3.5 5.5h13M6 10h8M8.5 14.5h3",
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, className = "size-5", ...rest }: { name: IconName } & SVGProps<SVGSVGElement>) {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" className={className} fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" {...rest}>
      <path d={PATHS[name]} />
    </svg>
  );
}

/** Brand mark: a ruled passbook page. */
export function Mark({ className = "size-7" }: { className?: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 28 28" className={className}>
      <rect x="3" y="3" width="22" height="22" rx="6" fill="var(--accent)" />
      <path d="M8.5 10.5h11M8.5 14h11M8.5 17.5h6.5" stroke="var(--accent-ink)" strokeWidth="1.7" strokeLinecap="round" />
      <path d="M19.5 17.5h.01" stroke="var(--credit)" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
  );
}
