"use client";

import { useState } from "react";
import { Plus, Check, Circle, Trash2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { api, type TaskRow } from "@/lib/api";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

/**
 * Minimal task list for the budget role (member) on a shared project: add, toggle
 * done/open, delete. Deliberately uses ONLY the allowed task endpoints
 * (POST/PATCH/DELETE /api/tasks) — no calendar/people/decompose/detail panel,
 * which the budget role is blocked from.
 */
export function ProjectTasksBudget({ projectId, initial }: { projectId: string; initial: TaskRow[] }) {
  const [tasks, setTasks] = useState<TaskRow[]>(initial);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    try {
      const t = await api.createTask({ title: title.trim(), project_id: projectId });
      setTasks((p) => [t, ...p]);
      setTitle("");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't add task");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(t: TaskRow) {
    const next = t.status === "done" ? "open" : "done";
    try {
      const u = await api.patchTask(t.id, { status: next });
      setTasks((p) => p.map((x) => (x.id === t.id ? u : x)));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't update task");
    }
  }

  async function remove(t: TaskRow) {
    if (!confirm(`Delete "${t.title}"?`)) return;
    try {
      await api.deleteTask(t.id);
      setTasks((p) => p.filter((x) => x.id !== t.id));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't delete task");
    }
  }

  const open = tasks.filter((t) => t.status !== "done" && t.status !== "cancelled");
  const done = tasks.filter((t) => t.status === "done");

  return (
    <div className="space-y-3">
      <form onSubmit={add} className="flex items-center gap-2">
        <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Add a task…"
          className="h-8 text-sm flex-1" disabled={busy} />
        <Button type="submit" size="sm" disabled={busy || !title.trim()}><Plus className="h-3.5 w-3.5 mr-1" /> Add</Button>
      </form>

      <ul className="rounded-md border border-border bg-card/40 divide-y divide-border overflow-hidden">
        {tasks.length === 0 ? (
          <li className="px-3 py-8 text-center text-sm text-muted-foreground">No tasks yet.</li>
        ) : [...open, ...done].map((t) => (
          <li key={t.id} className="group flex items-center gap-3 px-3 py-2 hover:bg-accent/20 transition-colors">
            <button onClick={() => toggle(t)} title={t.status === "done" ? "Mark open" : "Mark done"}
              className="shrink-0 text-muted-foreground hover:text-foreground">
              {t.status === "done" ? <Check className="h-4 w-4 text-emerald-400" /> : <Circle className="h-4 w-4" />}
            </button>
            <span className={cn("text-sm flex-1 min-w-0 truncate", t.status === "done" && "line-through text-muted-foreground")}>
              {t.title}
            </span>
            {t.due_date && <span className="text-[11px] text-muted-foreground tabular shrink-0">{t.due_date}</span>}
            <button onClick={() => remove(t)} title="Delete"
              className="opacity-0 group-hover:opacity-60 hover:!opacity-100 grid place-items-center h-7 w-7 rounded text-muted-foreground hover:bg-destructive hover:text-destructive-foreground transition shrink-0">
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
