import { toast } from "sonner";
import { api, type Stats } from "@/lib/api";

const CHEERS = [
  "Nice — one down!", "Boom 💥", "On a roll!", "Crushed it.", "Keep it going!",
  "That's the way.", "Done and dusted.", "Another one ✅", "Momentum!", "Clean.",
];

let lastBurst = 0;

/** celebrate() + a follow-up stats fetch that fires milestone cheers:
 *  cleared-everything-due-today, weekly-goal hit, new streak record. Each
 *  milestone toasts once (localStorage-guarded per day / week / streak len). */
export async function celebrateCompletion(onUndo?: () => void) {
  celebrate(undefined, onUndo);
  try {
    fireMilestones(await api.getStats());
  } catch {
    /* stats are decoration — never block the completion UX */
  }
}

function once(key: string): boolean {
  try {
    if (localStorage.getItem(key)) return false;
    localStorage.setItem(key, "1");
    return true;
  } catch {
    return false;
  }
}

function fireMilestones(stats: Stats) {
  const today = new Date().toISOString().slice(0, 10);
  const weekKey = stats.week[0]?.date ?? today;
  // Everything due today (and nothing overdue) is done.
  if (stats.due_today === 0 && stats.overdue === 0 && stats.done_today > 0
      && once(`ms-clear-${today}`)) {
    milestone("Cleared everything due today! ✨");
  }
  // Weekly goal reached.
  if (stats.weekly_goal > 0 && stats.done_week >= stats.weekly_goal
      && once(`ms-goal-${weekKey}`)) {
    milestone(`Weekly goal hit — ${stats.done_week}/${stats.weekly_goal} 🎯`);
  }
  // New all-time streak record (fires once per record length).
  if (stats.streak > 1 && stats.streak >= stats.best_streak
      && once(`ms-record-${stats.streak}`)) {
    milestone(`New streak record — ${stats.streak} days! 🏆`);
  }
}

function milestone(message: string) {
  toast.success(message, { duration: 6000 });
  if (typeof window === "undefined") return;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
  confettiBurst();
  setTimeout(confettiBurst, 300);   // double burst — bigger moment than one task
}

/** Lightweight, dependency-free celebration for completing a task: a confetti
 *  burst + a cheer toast. Respects prefers-reduced-motion (skips the confetti,
 *  keeps the toast). `message` overrides the random cheer (e.g. a milestone).
 *  When `onUndo` is given, the cheer toast carries an Undo button and stays for
 *  5s (the accidental-completion escape hatch). */
export function celebrate(message?: string, onUndo?: () => void) {
  const cheer = message ?? CHEERS[Math.floor(Math.random() * CHEERS.length)];
  toast.success(cheer, onUndo
    ? { duration: 5000, action: { label: "Undo", onClick: onUndo } }
    : undefined);
  if (typeof window === "undefined") return;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
  const now = Date.now();
  if (now - lastBurst < 200) return;   // throttle rapid completions
  lastBurst = now;
  confettiBurst();
}

function confettiBurst() {
  const canvas = document.createElement("canvas");
  canvas.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:9999";
  const dpr = window.devicePixelRatio || 1;
  canvas.width = window.innerWidth * dpr;
  canvas.height = window.innerHeight * dpr;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.scale(dpr, dpr);
  document.body.appendChild(canvas);

  const colors = ["#34d399", "#60a5fa", "#a78bfa", "#f472b6", "#fbbf24", "#f87171"];
  const W = window.innerWidth, H = window.innerHeight;
  const cx = W / 2, cy = H * 0.3;
  const parts = Array.from({ length: 90 }, () => {
    const ang = Math.random() * Math.PI * 2;
    const sp = 4 + Math.random() * 7;
    return {
      x: cx, y: cy,
      vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp - 3,
      r: 3 + Math.random() * 4, c: colors[(Math.random() * colors.length) | 0],
      rot: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.3,
    };
  });

  const t0 = performance.now();
  function frame(t: number) {
    const dt = t - t0;
    ctx!.clearRect(0, 0, W, H);
    for (const p of parts) {
      p.vy += 0.18; p.vx *= 0.99;
      p.x += p.vx; p.y += p.vy; p.rot += p.vr;
      ctx!.save();
      ctx!.translate(p.x, p.y);
      ctx!.rotate(p.rot);
      ctx!.globalAlpha = Math.max(0, 1 - dt / 1400);
      ctx!.fillStyle = p.c;
      ctx!.fillRect(-p.r, -p.r, p.r * 2, p.r * 2);
      ctx!.restore();
    }
    if (dt < 1400) requestAnimationFrame(frame);
    else canvas.remove();
  }
  requestAnimationFrame(frame);
}
