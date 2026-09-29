"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useApi } from "@/lib/api";
import { AlertsBell } from "./alerts";
import { Icon, Mark, type IconName } from "./icons";
import { QuickAddProvider, useQuickAdd } from "./quick-add-context";
import { ThemeToggle } from "./theme-toggle";
import { cx, Loading } from "./ui";

type Me = { display_name: string; mfa_required: boolean; mfa_satisfied: boolean; mfa_enabled: boolean; onboarded: boolean };

const NAV: { href: string; label: string; group: "main" | "wealth" | "plan" | "setup"; icon: IconName }[] = [
  { href: "/", label: "Home", group: "main", icon: "home" },
  { href: "/transactions", label: "Transactions", group: "main", icon: "transactions" },
  { href: "/review", label: "Review", group: "main", icon: "review" },
  { href: "/imports", label: "Import statements", group: "main", icon: "import" },
  { href: "/accounts", label: "Accounts", group: "main", icon: "accounts" },
  { href: "/assistant", label: "Ask", group: "main", icon: "ask" },
  { href: "/stats", label: "Stats", group: "wealth", icon: "stats" },
  { href: "/net-worth", label: "Net worth", group: "wealth", icon: "networth" },
  { href: "/cards", label: "Credit cards", group: "wealth", icon: "cards" },
  { href: "/loans", label: "Loans", group: "wealth", icon: "loans" },
  { href: "/investments", label: "Investments", group: "wealth", icon: "investments" },
  { href: "/budgets", label: "Budgets & goals", group: "plan", icon: "budgets" },
  { href: "/recurring", label: "Recurring & bills", group: "plan", icon: "recurring" },
  { href: "/people", label: "Shared with people", group: "plan", icon: "people" },
  { href: "/categories", label: "Categories & rules", group: "setup", icon: "categories" },
  { href: "/settings", label: "Settings", group: "setup", icon: "settings" },
];

const MOBILE_LEFT = ["/", "/transactions"];
const MOBILE_RIGHT = ["/stats"];

function active(path: string, href: string) {
  return href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`);
}

function useReviewCount() {
  const { data } = useApi<Record<string, number>>("/review/counts", { dedupingInterval: 60_000 });
  return data ? Object.values(data).reduce((a, b) => a + b, 0) : 0;
}

function ReviewCount() {
  const n = useReviewCount();
  return n ? <span className="num ml-auto rounded-full bg-review-wash px-2 text-[11.5px] font-semibold leading-5 text-review">{n}</span> : null;
}

function AddButton() {
  const add = useQuickAdd();
  return (
    <button type="button" onClick={() => add()} className="hidden min-h-9 items-center gap-1.5 rounded-lg bg-accent px-3 text-sm font-medium text-accent-ink hover:opacity-90 lg:inline-flex">
      <Icon name="plus" className="size-4" />Add
    </button>
  );
}

function MobileAdd() {
  const add = useQuickAdd();
  return (
    <div className="flex flex-1 items-start justify-center">
      <button type="button" onClick={() => add()} aria-label="Add a transaction"
        className="-mt-4 inline-flex size-14 items-center justify-center rounded-full bg-accent text-accent-ink shadow-sheet ring-4 ring-paper active:scale-95">
        <Icon name="plus" className="size-6" strokeWidth={2.2} />
      </button>
    </div>
  );
}

function MoreBadge() {
  const n = useReviewCount();
  return n ? <span className="num absolute right-[calc(50%-18px)] top-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-review px-1 text-[10px] font-semibold text-surface">{n}</span> : null;
}

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const { data: me, error } = useApi<Me>("/me");
  const [menu, setMenu] = useState(false);
  // "Ask" appears in the menu once the assistant is switched on in Settings.
  const { data: ai } = useApi<{ server_enabled: boolean; assistant_enabled: boolean }>("/ai/settings", { dedupingInterval: 60_000 });
  const askOn = !!ai?.server_enabled && !!ai?.assistant_enabled;

  useEffect(() => setMenu(false), [path]);
  useEffect(() => {
    if (!me) return;
    if (me.mfa_required && !me.mfa_satisfied) router.replace(me.mfa_enabled ? "/verify" : "/verify?enrol=1");
    else if (!me.onboarded && path !== "/onboarding") router.replace("/onboarding");
  }, [me, path, router]);

  async function signOut() {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.assign("/login?signed_out=1");
  }

  if (error) return <main className="p-6"><Loading label="Reconnecting" /></main>;
  if (!me) return <main className="p-6"><Loading /></main>;

  const groups = [
    { key: "main", label: null },
    { key: "wealth", label: "Money" },
    { key: "plan", label: "Planning" },
    { key: "setup", label: "Setup" },
  ] as const;

  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-5">
      {groups.map((g) => (
        <div key={g.key}>
          {g.label ? <p className="mb-1.5 px-3 text-xs font-medium text-ink-faint">{g.label}</p> : null}
          <ul className="flex flex-col gap-px">
            {NAV.filter((n) => n.group === g.key && (n.href !== "/assistant" || askOn || path.startsWith("/assistant"))).map((n) => {
              const on = active(path, n.href);
              return (
                <li key={n.href}>
                  <Link
                    href={n.href}
                    aria-current={on ? "page" : undefined}
                    className={cx(
                      "group flex min-h-9 items-center gap-2.5 rounded-lg px-3 text-[14.5px] transition-colors",
                      on ? "bg-surface font-medium text-ink shadow-[0_1px_0_rgb(0_0_0/0.03),0_0_0_1px_var(--rule)]" : "text-ink-soft hover:bg-sunken/70 hover:text-ink",
                    )}
                  >
                    <Icon name={n.icon} className={cx("size-[18px] shrink-0", on ? "text-ink" : "text-ink-faint group-hover:text-ink-soft")} />
                    {n.label}
                    {n.href === "/review" ? <ReviewCount /> : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );

  const initials = me.display_name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase() || "O";

  return (
    <QuickAddProvider>
    <div className="lg:grid lg:grid-cols-[248px_1fr]">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-lg focus:bg-surface focus:p-2">
        Skip to content
      </a>
      <aside className="sticky top-0 hidden h-dvh flex-col gap-7 overflow-y-auto border-r border-rule bg-paper px-3 pb-4 pt-5 lg:flex">
        <Link href="/" className="flex items-center gap-2.5 px-2.5">
          <Mark />
          <span className="display text-[1.35rem] font-semibold leading-none">OneLedger</span>
        </Link>
        {nav}
        <div className="mt-auto flex items-center gap-2.5 rounded-lg px-2.5 py-2">
          <span aria-hidden className="inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-accent-wash text-xs font-semibold text-ink">{initials}</span>
          <span className="min-w-0 flex-1 truncate text-sm font-medium">{me.display_name}</span>
          <button onClick={signOut} title="Sign out" aria-label="Sign out" className="inline-flex size-8 items-center justify-center rounded-lg text-ink-faint hover:bg-sunken hover:text-ink">
            <Icon name="signout" className="size-[18px]" />
          </button>
        </div>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-20 flex items-center justify-between gap-2 border-b border-rule bg-paper/90 px-4 py-2 backdrop-blur sm:px-6 lg:static lg:justify-end lg:border-0 lg:bg-transparent lg:px-10 lg:pt-4 lg:backdrop-blur-none">
          <Link href="/" className="flex items-center gap-2 lg:hidden">
            <Mark className="size-6" />
            <span className="display text-lg font-semibold">OneLedger</span>
          </Link>
          <div className="flex items-center gap-1.5">
            <AddButton />
            <AlertsBell />
            <ThemeToggle />
          </div>
        </header>
        <main id="main" className="min-w-0 px-4 pb-28 pt-5 sm:px-6 lg:px-10 lg:pb-16 lg:pt-0">
          <div className="mx-auto max-w-[1120px]">{children}</div>
        </main>
      </div>

      <nav aria-label="Quick" className="fixed inset-x-0 bottom-0 z-30 flex border-t border-rule bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
        {[...MOBILE_LEFT, "+", ...MOBILE_RIGHT].map((href) => {
          if (href === "+") return <MobileAdd key="add" />;
          const item = NAV.find((n) => n.href === href)!;
          const on = active(path, href);
          return (
            <Link key={href} href={href} aria-current={on ? "page" : undefined}
              className={cx("flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11.5px]", on ? "font-semibold text-ink" : "text-ink-faint")}>
              <Icon name={item.icon} className="size-5" />
              {item.label}
            </Link>
          );
        })}
        <button onClick={() => setMenu(true)} className="relative flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11.5px] text-ink-faint" aria-expanded={menu}>
          <Icon name="more" className="size-5" strokeWidth={3} />
          More
          <MoreBadge />
        </button>
      </nav>

      {menu ? (
        <div role="dialog" aria-modal="true" aria-label="Menu" className="fixed inset-0 z-40 overflow-y-auto bg-paper px-4 pb-8 pt-4 lg:hidden">
          <div className="mb-5 flex items-center justify-between">
            <span className="flex items-center gap-2"><Mark className="size-6" /><span className="display text-lg font-semibold">OneLedger</span></span>
            <button onClick={() => setMenu(false)} className="rounded-lg px-3 py-2 text-sm font-medium text-ink-soft hover:bg-sunken">Close</button>
          </div>
          {nav}
          <div className="mt-6 flex items-center gap-2.5 border-t border-rule px-3 pt-4">
            <span aria-hidden className="inline-flex size-8 items-center justify-center rounded-full bg-accent-wash text-xs font-semibold">{initials}</span>
            <span className="flex-1 truncate text-sm font-medium">{me.display_name}</span>
            <button onClick={signOut} className="rounded-lg px-3 py-2 text-sm text-ink-soft hover:bg-sunken">Sign out</button>
          </div>
        </div>
      ) : null}
    </div>
    </QuickAddProvider>
  );
}
