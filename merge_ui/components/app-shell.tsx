"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Users, KeyRound, Command, Menu, X, Send, Briefcase, CheckSquare, Target, Sparkles, Sun, ClipboardList, Activity, Building2, Wallet, LogOut, Plug, Mail, CalendarDays, CalendarClock } from "lucide-react";
import { cn } from "@/lib/utils";
import { useEffect, useState } from "react";
import { CommandPalette } from "./command-palette";
import { api } from "@/lib/api";

/**
 * App layout shell.
 *
 * Desktop (md+): fixed 14rem sidebar on the left, content on the right.
 * Mobile (<md): sidebar hidden by default. A top bar with a hamburger
 * appears; tapping it slides the sidebar in as an overlay with a
 * backdrop. Closes on backdrop tap, on Escape, on route change, and
 * after picking a nav item.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [pendingSuggestions, setPendingSuggestions] = useState<number | null>(null);
  const [healthOk, setHealthOk] = useState<boolean | null>(null);
  // Role drives which nav we render. Ask the API (works for Authelia-identified
  // users, who have no merge_role cookie); the API is the real enforcement
  // boundary regardless. Tri-state: null = not yet known. We fail CLOSED — until
  // the API confirms an admin identity we render the restricted view, so the
  // admin nav/IA never flashes for a budget user and never shows on a fetch
  // error (which previously left the admin nav visible to the budget-only member).
  const [role, setRole] = useState<"admin" | "budget" | null>(null);
  useEffect(() => {
    api.getMe()
      .then((r) => setRole(r.role === "budget" ? "budget" : "admin"))
      .catch(() => setRole("budget"));
  }, []);
  const budgetOnly = role !== "admin";

  // Poll the suggestions count + system health for the sidebar. Skipped for the
  // budget role (those endpoints 403 for it). Refetches on route change.
  useEffect(() => {
    if (budgetOnly) return;
    let cancelled = false;
    api.countSuggestions()
      .then((r) => { if (!cancelled) setPendingSuggestions(r.pending); })
      .catch(() => {});
    api.getSystemHealth()
      .then((r) => { if (!cancelled) setHealthOk(r.ok); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [pathname, budgetOnly]);

  // ⌘K / Ctrl-K palette
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
      if (e.key === "Escape") setSidebarOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Close the mobile drawer on every route change so it doesn't linger
  // over the next page.
  useEffect(() => { setSidebarOpen(false); }, [pathname]);

  // Prevent body scroll while the mobile drawer is open
  useEffect(() => {
    if (sidebarOpen) {
      const prev = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => { document.body.style.overflow = prev; };
    }
  }, [sidebarOpen]);

  return (
    <div className="min-h-dvh md:grid md:grid-cols-[14rem_1fr]">
      {/* Mobile top bar — md:hidden hides it on desktop */}
      <header className="md:hidden sticky top-0 z-30 flex items-center justify-between gap-2 px-3 py-2 border-b border-border bg-card/80 backdrop-blur">
        <button
          type="button"
          onClick={() => setSidebarOpen(true)}
          aria-label="Open menu"
          className="grid place-items-center h-9 w-9 rounded-md hover:bg-accent"
        >
          <Menu className="h-5 w-5" />
        </button>
        <div className="flex items-center gap-2 text-primary">
          <KeyRound className="h-4 w-4" />
          <span className="font-semibold tracking-tight text-sm">ADAM</span>
        </div>
        {!budgetOnly && (
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            aria-label="Quick search"
            className="grid place-items-center h-9 w-9 rounded-md hover:bg-accent text-muted-foreground"
          >
            <Command className="h-4 w-4" />
          </button>
        )}
      </header>

      {/* Mobile drawer backdrop */}
      {sidebarOpen && (
        <div
          className="md:hidden fixed inset-0 z-40 bg-background/70 backdrop-blur-sm"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Sidebar — on mobile, slides in as a fixed overlay; on desktop, an
          always-visible column. The same markup serves both via responsive
          classes (md:static, md:translate-x-0). */}
      <aside
        className={cn(
          "z-50 border-r border-border bg-card px-3 py-4 w-64",
          // Mobile: fixed overlay that slides in
          "fixed inset-y-0 left-0 transition-transform duration-200",
          sidebarOpen ? "translate-x-0" : "-translate-x-full",
          // Desktop: static, always visible, sticky to top
          "md:static md:translate-x-0 md:w-auto md:bg-card/40 md:sticky md:top-0 md:h-dvh",
        )}
      >
        <div className="flex items-center justify-between mb-6 md:block">
          <div className="px-2">
            <div className="flex items-center gap-2 text-primary">
              <KeyRound className="h-4 w-4" />
              <span className="font-semibold tracking-tight">ADAM</span>
            </div>
            <div className="text-xs text-muted-foreground mt-1">
              Personal OS
            </div>
          </div>
          {/* Close button on mobile only */}
          <button
            type="button"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close menu"
            className="md:hidden grid place-items-center h-8 w-8 rounded-md hover:bg-accent"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {budgetOnly ? (
          <nav className="space-y-1">
            <NavItem href="/budget" active={pathname.startsWith("/budget")} icon={<Wallet className="h-4 w-4" />}>
              Budget
            </NavItem>
            <NavItem href="/projects" active={pathname.startsWith("/projects")} icon={<Briefcase className="h-4 w-4" />}>
              Projects
            </NavItem>
            <NavItem href="/persons" active={pathname.startsWith("/persons")} icon={<Users className="h-4 w-4" />}>
              People
            </NavItem>
            <NavItem href="/companies" active={pathname.startsWith("/companies")} icon={<Building2 className="h-4 w-4" />}>
              Companies
            </NavItem>
          </nav>
        ) : (
          <>
            <nav className="space-y-1">
              <NavItem href="/today" active={pathname.startsWith("/today")} icon={<Sun className="h-4 w-4" />}>
                Today
              </NavItem>
              <NavItem href="/schedule" active={pathname.startsWith("/schedule")} icon={<CalendarDays className="h-4 w-4" />}>
                Schedule
              </NavItem>
              <NavItem href="/plan" active={pathname.startsWith("/plan")} icon={<ClipboardList className="h-4 w-4" />}>
                Plan
              </NavItem>
              <NavItem href="/budget" active={pathname.startsWith("/budget")} icon={<Wallet className="h-4 w-4" />}>
                Budget
              </NavItem>
            </nav>

            <div className="mt-4 mb-2 px-2 text-[10px] uppercase tracking-wider text-muted-foreground/60">Network</div>
            <nav className="space-y-1">
              <NavItem href="/persons" active={pathname.startsWith("/persons")} icon={<Users className="h-4 w-4" />}>
                People
              </NavItem>
              <NavItem href="/prospects" active={pathname.startsWith("/prospects")} icon={<Target className="h-4 w-4" />}>
                Prospects
              </NavItem>
              <NavItem href="/circles" active={pathname.startsWith("/circles")} icon={<Users className="h-4 w-4" />}>
                Circles
              </NavItem>
              <NavItem href="/followups" active={pathname.startsWith("/followups")} icon={<CalendarClock className="h-4 w-4" />}>
                Follow-ups
              </NavItem>
              <NavItem href="/companies" active={pathname.startsWith("/companies")} icon={<Building2 className="h-4 w-4" />}>
                Companies
              </NavItem>
              <NavItem href="/mail" active={pathname.startsWith("/mail")} icon={<Mail className="h-4 w-4" />}>
                Mail
              </NavItem>
              <NavItem href="/groups" active={pathname.startsWith("/groups")} icon={<Send className="h-4 w-4" />}>
                TG groups
              </NavItem>
            </nav>

            <div className="mt-4 mb-2 px-2 text-[10px] uppercase tracking-wider text-muted-foreground/60">Work</div>
            <nav className="space-y-1">
              <NavItem href="/suggestions" active={pathname.startsWith("/suggestions")} icon={<Sparkles className="h-4 w-4" />} badge={pendingSuggestions ?? undefined}>
                Inbox
              </NavItem>
              <NavItem href="/projects" active={pathname.startsWith("/projects")} icon={<Briefcase className="h-4 w-4" />}>
                Projects
              </NavItem>
              <NavItem href="/tasks" active={pathname.startsWith("/tasks")} icon={<CheckSquare className="h-4 w-4" />}>
                Tasks
              </NavItem>
              <NavItem href="/opportunities" active={pathname.startsWith("/opportunities")} icon={<Target className="h-4 w-4" />}>
                Opportunities
              </NavItem>
              <NavItem href="/health" active={pathname.startsWith("/health")} icon={<Activity className="h-4 w-4" />} alert={healthOk === false}>
                Health
              </NavItem>
              <NavItem href="/sources" active={pathname.startsWith("/sources")} icon={<Plug className="h-4 w-4" />}>
                Sources
              </NavItem>
            </nav>
          </>
        )}

        {!budgetOnly && (
          <button
            onClick={() => setPaletteOpen(true)}
            className="mt-6 w-full flex items-center justify-between gap-2 px-2 py-1.5 rounded-md text-xs text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
          >
            <span className="flex items-center gap-2">
              <Command className="h-3.5 w-3.5" />
              Quick search
            </span>
            <kbd className="font-mono text-[10px] px-1.5 py-0.5 rounded border border-border bg-muted text-muted-foreground">
              ⌘K
            </kbd>
          </button>
        )}

        {/* Log out — ends the Authelia session (the real gate). Hitting the
            app's /api/logout alone wouldn't, since auth now lives in the
            Authelia cookie on example.com. After logout, merge redirects back
            to the Authelia login portal. Shown for every role. */}
        <a
          href="https://auth.example.com/logout?rd=https%3A%2F%2Fmerge.example.com"
          className="mt-2 w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-xs text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
        >
          <LogOut className="h-3.5 w-3.5" />
          Log out
        </a>
      </aside>

      <main className="min-w-0">{children}</main>

      {!budgetOnly && <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />}
    </div>
  );
}

function NavItem({
  href,
  active,
  icon,
  children,
  badge,
  alert,
}: {
  href: string;
  active: boolean;
  icon: React.ReactNode;
  children: React.ReactNode;
  badge?: number;
  alert?: boolean;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "flex items-center gap-2 px-2 py-1.5 rounded-md text-sm transition-colors",
        active
          ? "bg-accent text-foreground"
          : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
      )}
    >
      {icon}
      <span className="flex-1">{children}</span>
      {alert && (
        <span className="h-2 w-2 rounded-full bg-rose-500" aria-label="needs attention" />
      )}
      {badge !== undefined && badge > 0 && (
        <span className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-primary text-primary-foreground text-[10px] font-medium tabular">
          {badge > 99 ? "99+" : badge}
        </span>
      )}
    </Link>
  );
}
