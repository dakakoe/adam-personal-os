"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, Flame, TrendingUp, Target, AlertTriangle, CircleDot, Pencil } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type Stats } from "@/lib/api";

function fmtUsd(n: number): string {
  if (!n) return "$0";
  if (n >= 1_000_000) return `$${(n / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 1 })}M`;
  if (n >= 1000) return `$${Math.round(n / 1000).toLocaleString()}k`;
  return `$${n.toLocaleString()}`;
}

function StatCard({ icon: Icon, label, value, sub, accent, href }: {
  icon: typeof Flame; label: string; value: string | number; sub?: string; accent: string; href?: string;
}) {
  const body = (
    <div className="rounded-lg border border-border bg-card/40 p-3 h-full hover:bg-card/60 transition-colors">
      <div className={cn("flex items-center gap-1.5 text-xs", accent)}>
        <Icon className="h-3.5 w-3.5" /> {label}
      </div>
      <div className="text-2xl font-semibold tabular mt-1 leading-none">{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground mt-1">{sub}</div>}
    </div>
  );
  return href ? <Link href={href}>{body}</Link> : body;
}

/** SVG progress ring; pct 0..1. */
function Ring({ pct, size = 40, stroke = 4, className }: {
  pct: number; size?: number; stroke?: number; className?: string;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const p = Math.min(1, Math.max(0, pct));
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className={className}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke}
        className="stroke-border" />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke}
        strokeLinecap="round" stroke="currentColor"
        strokeDasharray={`${c * p} ${c}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: "stroke-dasharray 600ms ease" }} />
    </svg>
  );
}

/** Weekly goal ring with an inline-editable target. */
function GoalCard({ stats }: { stats: Stats }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(String(stats.weekly_goal));
  const pct = stats.weekly_goal > 0 ? stats.done_week / stats.weekly_goal : 0;
  const hit = stats.done_week >= stats.weekly_goal;

  async function save() {
    setEditing(false);
    const goal = parseInt(val, 10);
    if (!Number.isFinite(goal) || goal < 1 || goal === stats.weekly_goal) { setVal(String(stats.weekly_goal)); return; }
    try { await api.setWeeklyGoal(goal); router.refresh(); } catch { setVal(String(stats.weekly_goal)); }
  }

  return (
    <div className="rounded-lg border border-border bg-card/40 p-3 h-full group">
      <div className={cn("flex items-center gap-1.5 text-xs", hit ? "text-emerald-400" : "text-teal-400")}>
        <CircleDot className="h-3.5 w-3.5" /> Weekly goal
        <button onClick={() => setEditing(true)} aria-label="Edit weekly goal"
          className="ml-auto opacity-0 group-hover:opacity-60 hover:!opacity-100 transition-opacity">
          <Pencil className="h-3 w-3" />
        </button>
      </div>
      <div className="flex items-center gap-2.5 mt-1">
        <div className={cn("relative shrink-0", hit ? "text-emerald-400" : "text-teal-400")}>
          <Ring pct={pct} />
          {hit && <span className="absolute inset-0 grid place-items-center text-[11px]">🎯</span>}
        </div>
        <div>
          <div className="text-2xl font-semibold tabular leading-none">
            {stats.done_week}
            <span className="text-sm text-muted-foreground font-normal">
              /{editing ? (
                <input autoFocus type="number" min={1} value={val}
                  onChange={(e) => setVal(e.target.value)}
                  onBlur={save}
                  onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") { setVal(String(stats.weekly_goal)); setEditing(false); } }}
                  className="w-12 bg-transparent border-b border-border outline-none text-sm tabular" />
              ) : stats.weekly_goal}
            </span>
          </div>
          <div className="text-[11px] text-muted-foreground mt-1">
            {hit ? "goal hit 🎉" : `${stats.weekly_goal - stats.done_week} to go`}
          </div>
        </div>
      </div>
    </div>
  );
}

export function StatsStrip({ stats }: { stats: Stats }) {
  const maxBar = Math.max(1, ...stats.week.map((w) => w.count));
  const atRecord = stats.streak > 1 && stats.streak >= stats.best_streak;
  const maxProj = Math.max(1, ...stats.projects_week.map((p) => p.count));
  return (
    <div className="mb-5 space-y-2">
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <StatCard icon={CheckCircle2} accent="text-emerald-400" label="Done today"
          value={stats.done_today}
          sub={stats.due_today > 0 ? `${stats.due_today} due today` : "all clear ✨"} href="/tasks" />

        {/* This week — count + 7-day completion bars */}
        <div className="rounded-lg border border-border bg-card/40 p-3">
          <div className="flex items-center gap-1.5 text-xs text-sky-400">
            <TrendingUp className="h-3.5 w-3.5" /> This week
          </div>
          <div className="flex items-end justify-between gap-2 mt-1">
            <div className="text-2xl font-semibold tabular leading-none">{stats.done_week}</div>
            <div className="flex items-end gap-1 h-8 flex-1 max-w-[8rem]">
              {stats.week.map((w) => (
                <div key={w.date} title={`${w.dow}: ${w.count}`} className="flex-1 flex flex-col items-center justify-end gap-0.5 h-full">
                  <div className={cn("w-full rounded-sm transition-all", w.is_today ? "bg-emerald-400" : "bg-emerald-500/35")}
                    style={{ height: `${(w.count / maxBar) * 100}%`, minHeight: w.count ? 3 : 1 }} />
                  <span className={cn("text-[8px] leading-none", w.is_today ? "text-emerald-400 font-medium" : "text-muted-foreground/50")}>{w.dow[0]}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <GoalCard stats={stats} />

        <StatCard icon={Flame} accent="text-amber-400" label="Streak"
          value={`${stats.streak}${atRecord ? " 🏆" : ""}`}
          sub={atRecord ? "personal best!" : `best ${stats.best_streak}`} />

        <StatCard icon={Target} accent="text-violet-400" label="Pipeline"
          value={fmtUsd(stats.pipeline.total_usd)} sub={`${stats.pipeline.deals} live deals`} href="/opportunities" />
      </div>

      {/* This week, by project — mini rings sized against the busiest project */}
      {stats.projects_week.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          {stats.projects_week.map((p) => (
            <span key={p.name} title={`${p.name}: ${p.count} done this week`}
              className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground rounded-full border border-border bg-card/40 pl-1.5 pr-2.5 py-1">
              <span className="text-sky-400"><Ring pct={p.count / maxProj} size={16} stroke={2.5} /></span>
              {p.name} <span className="tabular font-medium text-foreground">{p.count}</span>
            </span>
          ))}
        </div>
      )}

      {/* Priorities — only when something needs attention */}
      {(stats.overdue > 0 || stats.due_today > 0) && (
        <Link href="/tasks" className="inline-flex items-center gap-3 text-xs rounded-md border border-border bg-card/40 px-3 py-1.5 hover:bg-card/60 transition-colors">
          {stats.overdue > 0 && (
            <span className="inline-flex items-center gap-1 text-rose-400">
              <AlertTriangle className="h-3.5 w-3.5" /> {stats.overdue} overdue
            </span>
          )}
          {stats.due_today > 0 && <span className="text-amber-400">{stats.due_today} due today</span>}
        </Link>
      )}
    </div>
  );
}
