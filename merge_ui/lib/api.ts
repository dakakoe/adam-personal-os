// Same-origin fetch — middleware/Caddy already routed /api/* to FastAPI and
// forwarded the session cookie. No need for an Authorization header here in
// the browser; server components pass through the cookie via `cookies()`.

export type ProspectRow = {
  person_id: string;
  display_name: string;
  telegram_username: string | null;
  email: string | null;
  total_interactions: number;
  last_interaction_at: string | null;
  days_since: number | null;
  has_profile: boolean;
  summary: string | null;
  in_pipeline: boolean;
  dismissed: boolean;
  score: number;
  distance?: number | null;   // ICP semantic distance (search mode only)
};

export type PersonRow = {
  person_id: string;
  display_name: string;
  visibility: "shared" | "private";
  sensitive: boolean;
  telegram_username: string | null;
  email: string | null;
  linkedin: string | null;
  total_interactions: number;
  last_interaction_at: string | null;
  circles?: PersonCircleRef[];      // inline, so the list can tag without a call per row
};

export type IdentityRow = {
  identity_id: number;
  source: string;
  source_id: string;
  evidence: Record<string, unknown> | null;
  created_at: string;
};

export type SignalRow = {
  signal_type: string;
  value: string;
  confidence: string;
  source: string;
};

export type LinkedInInfo = {
  vanity: string;
  url: string;
  company: string | null;
  position: string | null;
  connected_on: string | null;
  location: string | null;
};

export type BioRow = {
  source: string;     // 'telegram' | 'linkedin' | 'google_contacts'
  kind: string;       // 'bio' | 'role' | 'title' | 'notes'
  text: string;
  fetched_at: string | null;
};

export type NameSuggestion = {
  source: string;
  suggested: string;
  evidence: string;
};

export type NameSuggestionsResponse = {
  current_display_name: string;
  current_is_synthetic: boolean;
  suggestions: NameSuggestion[];
};

// --- projects / tasks / opportunities (Phase 1 personal-OS) ---------

export type ProjectStatus = "active" | "paused" | "archived";
export type TaskStatus = "open" | "doing" | "done" | "cancelled";
// Stage keys are config-driven (memory.opp_stage) — see StageConfig.
export type OpportunityStage = string;

export type StageConfig = {
  key: string;
  label: string;
  sort: number;
  terminal: boolean;   // lost-like: muted column, out of pipeline totals
  closes: boolean;     // entering it marks the deal closed (won/lost)
  color: string;       // palette name (sky/violet/amber/…)
  in_use: number;
};

export type ProjectRow = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  status: ProjectStatus;
  color: string | null;
  created_at: string;
  updated_at: string;
  member_count: number;
  open_task_count: number;
  live_opp_count: number;
};

export type ProjectMember = {
  person_id: string;
  role: string | null;
  added_at: string;
  display_name: string;
};

export type ProjectDetail = Omit<ProjectRow, "member_count" | "open_task_count" | "live_opp_count"> & {
  members: ProjectMember[];
};

export type TaskRow = {
  id: string;
  title: string;
  description: string | null;
  project_id: string | null;
  project_slug: string | null;
  project_name: string | null;
  project_color: string | null;
  opportunity_id: string | null;
  opportunity_title: string | null;    // the deal this task belongs to
  with_person_id: string | null;
  with_person_name: string | null;
  assignee_person_id: string | null;   // null = "Me"
  assignee_name: string | null;
  parent_task_id: string | null;
  subtask_total: number;
  subtask_done: number;
  people_count: number;
  status: TaskStatus;
  due_date: string | null;       // ISO date "YYYY-MM-DD"
  due_time: string | null;       // "HH:MM:SS" or null (all-day)
  duration_min: number | null;   // calendar event length (min)
  source_kind: string | null;
  source_ref: string | null;
  gcal_account: string | null;   // connected calendar this task syncs to
  gcal_calendar_id: string | null;   // sub-calendar id (null = primary)
  gcal_event_id: string | null;
  gcal_html_link: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type RoutineParticipant = {
  person_id: string;
  display_name: string;
  email: string | null;     // null = no email on file → not invitable
  sensitive: boolean;       // sensitive contacts are never put on a Google invite
};

export type RecurringTaskRow = {
  id: string;
  title: string;
  description: string | null;
  project_id: string | null;
  project_slug: string | null;
  project_name: string | null;
  project_color: string | null;
  with_person_id: string | null;
  with_person_name: string | null;
  at_time: string | null;        // "HH:MM:SS" or null (all-day reminder)
  duration_min: number | null;   // recurring event length (min)
  freq: "daily" | "weekly" | "monthly" | "yearly";
  byweekday: number[];           // 0=Mon … 6=Sun (weekly)
  anchor_date: string;
  active: boolean;
  gcal_account: string | null;   // connected calendar the recurring event lives on
  gcal_calendar_id: string | null;   // sub-calendar id (null = primary)
  gcal_event_id: string | null;
  gcal_html_link: string | null;
  participants: RoutineParticipant[];
  created_at: string;
  updated_at: string;
};

export type TaskPerson = {
  person_id: string;
  display_name: string;
  added_at: string | null;
};

export type TaskDetail = TaskRow & {
  people: TaskPerson[];
  subtasks: TaskRow[];
};

export type OpportunityRow = {
  id: string;
  title: string;
  description: string | null;
  project_id: string | null;
  project_slug: string | null;
  project_name: string | null;
  project_color: string | null;
  counterparty_id: string | null;
  counterparty_name: string | null;
  company: string | null;
  company_id: string | null;
  company_name: string | null;
  responsible_person_id: string | null;   // null = "Me"
  responsible_name: string | null;
  stage: OpportunityStage;
  estimated_value: string | null;         // legacy free-text
  award_usd: number | null;
  award_note: string | null;
  tags: string[];              // stream labels ('job', 'consulting', …)
  source_kind: string | null;
  source_ref: string | null;
  task_count: number;
  open_task_count: number;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
};

export type OpportunityEvent = {
  id: string;
  kind: "stage_change" | "note";
  from_stage: OpportunityStage | null;
  to_stage: OpportunityStage | null;
  next_step: string | null;
  note: string | null;
  created_at: string;
};

export type OpportunityDetail = OpportunityRow & {
  events: OpportunityEvent[];
  tasks: TaskRow[];
};

export type PipelineStage = { stage: OpportunityStage; count: number; usd: number };
export type PipelineSummary = {
  by_stage: PipelineStage[];
  total_usd: number;
  total_count: number;
};

export type HealthUnit = {
  unit: string;
  label: string;
  kind: "always_on" | "oneshot";
  ok: boolean | null;          // null = never run yet
  active_state: string | null;
  result: string | null;
  exit_status: string | null;
  last_run: string | null;
};

export type SystemHealth = {
  ok: boolean;
  db_ok: boolean;
  anthropic_configured: boolean;
  units: HealthUnit[];
};

export type VersionInfo = {
  current: string | null;        // installed VERSION (null on a dev/source checkout)
  latest: string | null;         // newest published Release
  update_available: boolean;
  notes: string | null;
  url: string | null;
};

export type SourceStatus = {
  key: string;
  label: string;
  unit: string;
  kind: "always_on" | "oneshot";
  noun: string;
  count: number | null;
  last_sync: string | null;     // ISO timestamp of newest ingested item
  unit_ok: boolean | null;
  unit_state: string | null;
  needs_reconnect: boolean;     // a fetcher flagged the OAuth/login as revoked
  reconnect_url: string | null; // web re-consent start URL, when configured
  reconnect_accounts: string[]; // the flagged account email(s) to reconnect
  reconnect_reason: string | null; // manual-recovery reason for non-OAuth sources (Telegram/Granola)
  oauth_accounts: string[];     // all OAuth accounts (healthy included) for on-demand re-consent
  retired_accounts?: string[];  // sunsetted mailboxes kept as a read-only archive (never synced)
};

export type FocusActionItem = {
  kind: "task" | "opportunity";
  id: string;
  title: string;
  score: number;
  reason: string;
  project_slug: string | null;
  project_name: string | null;
  project_color: string | null;
  person_id: string | null;
  person_name: string | null;
  stage: OpportunityStage | null;
  status: TaskStatus | null;
  due_date: string | null;
};

export type CompanyRow = {
  id: string;
  name: string;
  country: string | null;
  website: string | null;
  domain: string | null;
  description: string | null;
  visibility: "shared" | "private";
  created_at: string;
  updated_at: string;
  people_count: number;
  live_opp_count: number;
  pipeline_usd: number;
};

export type CompanyPersonRow = {
  person_id: string;
  display_name: string;
  role: string | null;
  is_current: boolean;
  added_at: string | null;
};

export type LinkSuggestion = {
  person_id: string;
  person_name: string;
  employer: string;        // raw LinkedIn employer text
  role: string | null;
  company_id: string;
  company_name: string;    // the fuzzy-matched existing entity
  company_domain: string | null;
  similarity: number;
  linkedin_vanity: string | null;   // the person's LinkedIn /in/<vanity>, if known
};

export type CompanyDetail = {
  id: string;
  name: string;
  country: string | null;
  website: string | null;
  domain: string | null;
  description: string | null;
  visibility: "shared" | "private";
  owner_member_id: string | null;
  owner_member_name: string | null;
  created_at: string;
  updated_at: string;
  people: CompanyPersonRow[];
  opportunities: OpportunityRow[];
};

export type ContactCircle = {
  id: string;
  key: string;
  label: string;
  priority: number;        // lower = stronger; a person inherits their lowest
  color: string | null;
  cadence_days: number | null;   // null = pure label, no expectation
  notes: string | null;
  member_count: number;
  created_at: string;
  updated_at: string;
};

export type FollowupNote = {
  id: string;
  body: string;
  created_at: string;
};

export type FollowupRow = {
  id: string;
  person_id: string;
  display_name: string;
  due_date: string;                 // "2026-08-12"
  due_time: string | null;          // "15:00:00", or null for an all-day one
  topic: string | null;
  status: "open" | "connected" | "cancelled";
  connected: boolean;               // did the conversation happen (either way)
  connected_at: string | null;
  connected_via: string | null;     // 'telegram_text', 'gmail', 'manual', …
  connected_source: "auto" | "manual" | null;
  notes: FollowupNote[];            // newest first
  created_at: string;
};

export type PersonCircleRef = {
  id: string; key: string; label: string;
  priority: number; color: string | null; cadence_days: number | null;
};

export type CircleDueRow = {
  person_id: string;
  display_name: string;
  circle_label: string | null;
  priority: number;
  cadence_days: number | null;
  last_interaction_at: string | null;
  total_interactions: number;
  days_since: number | null;
  days_overdue: number | null;
};

export type PersonCompany = {
  company_id: string;
  name: string;
  role: string | null;
  is_current: boolean;
};

export type DraftChannel = "telegram" | "email";
export type DraftStatus = "draft" | "sent" | "discarded";

export type DraftRow = {
  id: string;
  person_id: string;
  channel: DraftChannel;
  subject: string | null;
  body: string;
  status: DraftStatus;
  task_id: string | null;
  opportunity_id: string | null;
  model: string | null;
  created_at: string;
  updated_at: string;
  decided_at: string | null;
};

export type SuggestionKind = "task" | "opportunity" | "person_mention";
export type SuggestionStatus = "pending" | "accepted" | "dismissed";

export type SuggestionRow = {
  id: string;
  kind: SuggestionKind;
  status: SuggestionStatus;
  title: string;
  detail: string | null;
  project_id: string | null;
  project_slug: string | null;
  project_name: string | null;
  project_color: string | null;
  person_id: string | null;
  person_name_raw: string | null;
  person_name: string | null;
  suggested_stage: OpportunityStage | null;
  estimated_value: string | null;
  due_date: string | null;
  owner_hint: string | null;
  confidence: string;
  source_kind: string;
  source_ref: string | null;
  created_at: string;
  decided_at: string | null;
  context_at: string | null;        // when it was actually discussed
  accepted_entity_kind: string | null;
  accepted_entity_id: string | null;
  recap_title: string | null;
  recap_date: string | null;
};

// --- daily plan (Phase 4 personal-OS) -------------------------------

export type FocusKind = "task" | "opportunity" | "reply" | "suggestion" | "other";

export type FocusItem = {
  title: string;
  reason: string | null;
  kind: FocusKind;
  ref_id: string | null;
  project_slug?: string | null;
};

export type CalendarEvent = {
  summary: string | null;
  location: string | null;
  start_ts: string | null;
  end_ts: string | null;
  all_day: boolean;
  self_response: string | null;
  account_email?: string;
  attendee_count?: number;
  display_ts?: string | null;   // start clamped to the agenda window (multi-day events)
};

export type DailyPlanStructured = {
  focus?: FocusItem[];
  counts?: {
    open_tasks: number;
    live_opps: number;
    pending_suggestions: number;
    owes_reply: number;
    events?: number;
  };
  owes_reply?: Array<{ person_id: string; display_name: string; last_in: string; inb: number }>;
  events?: CalendarEvent[];
  timezone?: string;
  generated_via?: "llm" | "fallback";
  model?: string | null;
};

export type GoogleCalendar = { id: string; summary: string; primary: boolean };

/** A pickable sync target: an account's primary or one of its sub-calendars. */
export type CalendarOption = {
  account: string;
  calendar_id: string | null;   // null = primary
  label: string;
  value: string;                // stable <select> value
};

export function calendarOptions(
  r: { accounts: { account: string; calendars?: GoogleCalendar[] }[] },
): CalendarOption[] {
  const out: CalendarOption[] = [];
  for (const a of r.accounts) {
    const cals = a.calendars?.length ? a.calendars : [{ id: "primary", summary: "Primary", primary: true }];
    for (const c of cals) {
      const cid = c.primary ? null : c.id;
      out.push({
        account: a.account,
        calendar_id: cid,
        label: c.primary ? a.account : `${a.account} · ${c.summary}`,
        value: `${a.account}|${cid ?? ""}`,
      });
    }
  }
  return out;
}

export type TodayCounts = {
  open_tasks: number;
  live_opps: number;
  pending_suggestions: number;
  owed_reply: number;
  meetings_today: number;
};

export type Stats = {
  done_today: number;
  done_week: number;
  week: { date: string; dow: string; count: number; is_today: boolean }[];
  streak: number;
  best_streak: number;
  weekly_goal: number;
  projects_week: { name: string; count: number }[];
  overdue: number;
  due_today: number;
  pipeline: { total_usd: number; deals: number; by_stage: { stage: string; count: number; usd: number }[] };
};

export type DailyPlan = {
  id: string;
  plan_date: string;           // ISO date "YYYY-MM-DD"
  narrative: string | null;
  structured: DailyPlanStructured;
  generated_at: string;
};

export type TelegramGroup = {
  chat_id: number;
  title: string | null;
  kind: string;           // 'group' | 'supergroup' | 'channel' | 'other'
  member_count: number | null;
  enabled: boolean;
  first_seen_at: string;
  last_seen_at: string;        // when OUR fetcher last touched this row
  enabled_at: string | null;
  last_message_at: string | null;  // the CHAT's own last message (Telethon dialog.date)
  msg_count: number;               // raw.telegram_message rows ingested for this chat
};

export type CleanupCandidate = {
  person_id: string;
  display_name: string;
  sample_email: string | null;
  by_email: boolean;
  by_name: boolean;
  msgs: number;
  last_at: string | null;
};

// Augments PersonDetail.recent_messages etc.
type PersonTask = TaskRow;
type PersonOpp = OpportunityRow;

export type PersonDetail = {
  person_id: string;
  display_name: string;
  notes: string | null;
  visibility: "shared" | "private";
  sensitive: boolean;
  owner_member_id: string | null;
  owner_member_name: string | null;
  telegram_username: string | null;
  telegram_bio: string | null;
  phone: string | null;
  birthday: string | null;
  linkedin: LinkedInInfo | null;
  structured: {
    current_company?: string | null;
    current_role?: string | null;
    past_companies?: string[];
    personal_emails?: string[];
    personal_social?: Record<string, string | null>;
    languages?: string[];
  } | null;
  summary: string | null;
  total_interactions: number;
  inbound_count: number;
  outbound_count: number;
  first_interaction_at: string | null;
  last_interaction_at: string | null;
  channels: string[];
  identities: IdentityRow[];
  signals: SignalRow[];
  bios: BioRow[];
  tasks: PersonTask[];
  opportunities: PersonOpp[];
  companies: PersonCompany[];
  circles: PersonCircleRef[];
  recent_messages: Array<{
    occurred_at: string;
    direction: "inbound" | "outbound";
    channel: string;
    body_excerpt: string;
    group_chat_id: string | null;   // signed chat_id if this came from a TG group
    group_title: string | null;
  }>;
};

export type SimilarPerson = {
  person_id: string;
  display_name: string;
  total_interactions: number;
  identity_count: number;
  sources: string[] | null;
  identities_preview: Array<{ source: string; source_id: string }>;
};

export type PendingMergeForPerson = {
  id: number;
  source: string;
  confidence: "high" | "medium" | "low";
  score: number | null;
  evidence: Record<string, unknown>;
  other_person_id: string;
  other_display_name: string;
  other_identity_count: number;
  other_interactions: number;
};

export type MergeCandidate = {
  id: number;
  source: string;
  confidence: "high" | "medium" | "low";
  score: number | null;
  evidence: Record<string, unknown>;
  created_at: string;
  left: PersonDetail;
  right: PersonDetail;
};

function apiBase(): string {
  // Server-side: hit the FastAPI directly to skip rewrite hop
  if (typeof window === "undefined") {
    return process.env.MERGE_API_URL ?? "http://127.0.0.1:9100";
  }
  return "";  // browser: same-origin /api/*
}

async function jsonFetch<T>(path: string, init?: RequestInit & { cookieHeader?: string }): Promise<T> {
  const url = `${apiBase()}${path}`;
  const headers: Record<string, string> = {
    "content-type": "application/json",
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  // Server components forward the session cookie explicitly.
  if (init?.cookieHeader) {
    headers["cookie"] = init.cookieHeader;
  }
  // Server-side this fetch goes STRAIGHT to the API (bypassing Caddy), so it
  // must carry the caller's identity itself. Forward the incoming request's
  // cookie (bearer-token login) AND the Authelia identity Caddy injected
  // (x-proxy-secret + remote-* headers — present for Authelia logins, which
  // have no merge_session cookie). Without this, an Authelia user gets the app
  // shell but every server-rendered fetch is unauthenticated → no data.
  if (typeof window === "undefined") {
    try {
      const { headers: incomingHeaders } = await import("next/headers");
      const h = await incomingHeaders();
      if (!headers["cookie"]) {
        const cookie = h.get("cookie");
        if (cookie) headers["cookie"] = cookie;
      }
      for (const name of ["x-proxy-secret", "remote-user", "remote-groups", "remote-name", "remote-email"]) {
        const v = h.get(name);
        if (v) headers[name] = v;
      }
    } catch {
      // Not inside a request scope (e.g. build-time prerender) — nothing to forward.
    }
  }
  const res = await fetch(url, {
    ...init,
    headers,
    cache: "no-store",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`API ${res.status} ${res.statusText}: ${body.slice(0, 200)}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  listPersons: (
    params: { q?: string; company_id?: string; circle?: string; limit?: number; offset?: number },
    opts?: { cookieHeader?: string },
  ) =>
    jsonFetch<PersonRow[]>(
      `/api/persons?` +
        new URLSearchParams({
          ...(params.q ? { q: params.q } : {}),
          ...(params.company_id ? { company_id: params.company_id } : {}),
          ...(params.circle ? { circle: params.circle } : {}),
          limit: String(params.limit ?? 50),
          offset: String(params.offset ?? 0),
        }),
      { cookieHeader: opts?.cookieHeader },
    ),

  // --- prospects (reconnect / BD hunt) ---
  listProspects: (
    params: {
      min_interactions?: number; dormant_after_days?: number; dormant_before_days?: number;
      include_dismissed?: boolean; include_pipeline?: boolean; q?: string;
      limit?: number; offset?: number;
    } = {},
    opts?: { cookieHeader?: string },
  ) => {
    const sp = new URLSearchParams();
    if (params.min_interactions != null) sp.set("min_interactions", String(params.min_interactions));
    if (params.dormant_after_days != null) sp.set("dormant_after_days", String(params.dormant_after_days));
    if (params.dormant_before_days != null) sp.set("dormant_before_days", String(params.dormant_before_days));
    if (params.include_dismissed) sp.set("include_dismissed", "true");
    if (params.include_pipeline) sp.set("include_pipeline", "true");
    if (params.q) sp.set("q", params.q);
    sp.set("limit", String(params.limit ?? 50));
    sp.set("offset", String(params.offset ?? 0));
    return jsonFetch<ProspectRow[]>(`/api/prospects?${sp.toString()}`, { cookieHeader: opts?.cookieHeader });
  },
  searchProspects: (
    params: {
      q: string; min_interactions?: number; dormant_after_days?: number;
      include_pipeline?: boolean; limit?: number;
    },
    opts?: { cookieHeader?: string },
  ) => {
    const sp = new URLSearchParams({ q: params.q });
    if (params.min_interactions != null) sp.set("min_interactions", String(params.min_interactions));
    if (params.dormant_after_days != null) sp.set("dormant_after_days", String(params.dormant_after_days));
    if (params.include_pipeline) sp.set("include_pipeline", "true");
    sp.set("limit", String(params.limit ?? 50));
    return jsonFetch<ProspectRow[]>(`/api/prospects/search?${sp.toString()}`, { cookieHeader: opts?.cookieHeader });
  },
  dismissProspect: (personId: string) =>
    jsonFetch<void>(`/api/prospects/${personId}/dismiss`, { method: "POST" }),
  undismissProspect: (personId: string) =>
    jsonFetch<void>(`/api/prospects/${personId}/dismiss`, { method: "DELETE" }),

  // --- suggestions inbox (Phase 2) -----------------------------

  listSuggestions: (
    params: { status_filter?: string; kind?: string; limit?: number; offset?: number } = {},
    opts?: { cookieHeader?: string },
  ) => {
    const sp = new URLSearchParams();
    if (params.status_filter) sp.set("status_filter", params.status_filter);
    if (params.kind) sp.set("kind", params.kind);
    sp.set("limit", String(params.limit ?? 100));
    sp.set("offset", String(params.offset ?? 0));
    return jsonFetch<SuggestionRow[]>(`/api/suggestions?${sp.toString()}`, { cookieHeader: opts?.cookieHeader });
  },
  countSuggestions: (opts?: { cookieHeader?: string }) =>
    jsonFetch<{ pending: number }>(`/api/suggestions/count`, { cookieHeader: opts?.cookieHeader }),
  acceptSuggestion: (id: string) =>
    jsonFetch<{ ok: true; entity_kind: string; entity_id: string }>(`/api/suggestions/${id}/accept`, { method: "POST" }),
  dismissSuggestion: (id: string) =>
    jsonFetch<void>(`/api/suggestions/${id}/dismiss`, { method: "POST" }),
  reassignSuggestionPerson: (id: string, person_id: string) =>
    jsonFetch<SuggestionRow>(`/api/suggestions/${id}/reassign`, {
      method: "POST", body: JSON.stringify({ person_id }),
    }),

  // --- daily plan (Phase 4) ------------------------------------

  getToday: (opts?: { cookieHeader?: string }) =>
    jsonFetch<DailyPlan | null>(`/api/today`, { cookieHeader: opts?.cookieHeader }),
  getTodayCounts: (opts?: { cookieHeader?: string }) =>
    jsonFetch<TodayCounts>(`/api/today/counts`, { cookieHeader: opts?.cookieHeader }),
  getStats: (opts?: { cookieHeader?: string }) =>
    jsonFetch<Stats>(`/api/stats`, { cookieHeader: opts?.cookieHeader }),
  listEvents: (params: { days?: number; start?: string; end?: string } = {}, opts?: { cookieHeader?: string }) => {
    const sp = new URLSearchParams();
    if (params.start) { sp.set("start", params.start); if (params.end) sp.set("end", params.end); }
    else sp.set("days", String(params.days ?? 2));
    return jsonFetch<{ events: CalendarEvent[]; timezone: string }>(
      `/api/events?${sp.toString()}`, { cookieHeader: opts?.cookieHeader });
  },
  setWeeklyGoal: (goal: number) =>
    jsonFetch<{ ok: true; weekly_goal: number }>(`/api/settings/weekly-goal`, {
      method: "PUT", body: JSON.stringify({ goal }),
    }),
  getFocus: (params: { limit?: number } = {}, opts?: { cookieHeader?: string }) =>
    jsonFetch<FocusActionItem[]>(`/api/focus?limit=${params.limit ?? 15}`, { cookieHeader: opts?.cookieHeader }),
  getSystemHealth: (opts?: { cookieHeader?: string }) =>
    jsonFetch<SystemHealth>(`/api/health/system`, { cookieHeader: opts?.cookieHeader }),
  getMe: (opts?: { cookieHeader?: string }) =>
    jsonFetch<{ role: "admin" | "budget" }>(`/api/me`, { cookieHeader: opts?.cookieHeader }),
  getSources: (opts?: { cookieHeader?: string }) =>
    jsonFetch<{ sources: SourceStatus[] }>(`/api/sources`, { cookieHeader: opts?.cookieHeader }),
  patchIdentityRole: (personId: string, identityId: number, body: { position?: string; company?: string }) =>
    jsonFetch<IdentityRow>(`/api/persons/${personId}/identities/${identityId}`,
      { method: "PATCH", body: JSON.stringify(body) }),
  // --- contact circles ---
  listCircles: (opts?: { cookieHeader?: string }) =>
    jsonFetch<ContactCircle[]>(`/api/circles`, { cookieHeader: opts?.cookieHeader }),
  createCircle: (body: { key: string; label: string; priority?: number; color?: string; cadence_days?: number | null; notes?: string }) =>
    jsonFetch<ContactCircle>(`/api/circles`, { method: "POST", body: JSON.stringify(body) }),
  patchCircle: (id: string, body: Record<string, unknown>) =>
    jsonFetch<ContactCircle>(`/api/circles/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteCircle: (id: string) =>
    jsonFetch<void>(`/api/circles/${id}`, { method: "DELETE" }),
  circlesDue: (params: { limit?: number } = {}, opts?: { cookieHeader?: string }) =>
    jsonFetch<CircleDueRow[]>(`/api/circles/due?limit=${params.limit ?? 100}`, { cookieHeader: opts?.cookieHeader }),
  listFollowups: (
    params: { scope?: "open" | "all"; person_id?: string; limit?: number } = {},
    opts?: { cookieHeader?: string },
  ) => {
    const sp = new URLSearchParams({ scope: params.scope ?? "open" });
    if (params.person_id) sp.set("person_id", params.person_id);
    if (params.limit) sp.set("limit", String(params.limit));
    return jsonFetch<FollowupRow[]>(`/api/followups?${sp}`, { cookieHeader: opts?.cookieHeader });
  },
  createFollowup: (body: { person_id: string; due_date: string; due_time?: string | null; topic?: string | null }) =>
    jsonFetch<FollowupRow>(`/api/followups`, { method: "POST", body: JSON.stringify(body) }),
  patchFollowup: (id: string, body: Record<string, unknown>) =>
    jsonFetch<FollowupRow>(`/api/followups/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  addFollowupNote: (id: string, body: string) =>
    jsonFetch<FollowupRow>(`/api/followups/${id}/notes`, { method: "POST", body: JSON.stringify({ body }) }),
  deleteFollowupNote: (id: string, noteId: string) =>
    jsonFetch<FollowupRow>(`/api/followups/${id}/notes/${noteId}`, { method: "DELETE" }),
  deleteFollowup: (id: string) =>
    jsonFetch<void>(`/api/followups/${id}`, { method: "DELETE" }),
  setPersonCircles: (personId: string, circle_ids: string[]) =>
    jsonFetch<ContactCircle[]>(`/api/persons/${personId}/circles`, { method: "PUT", body: JSON.stringify({ circle_ids }) }),
  getVersion: (opts?: { cookieHeader?: string }) =>
    jsonFetch<VersionInfo>(`/api/version`, { cookieHeader: opts?.cookieHeader }),
  retireAccount: (email: string) =>
    jsonFetch<void>(`/api/sources/accounts/${encodeURIComponent(email)}/retire`, { method: "POST" }),
  unretireAccount: (email: string) =>
    jsonFetch<void>(`/api/sources/accounts/${encodeURIComponent(email)}/retire`, { method: "DELETE" }),
  syncSource: (key: string) =>
    jsonFetch<{ ok: boolean; unit: string }>(`/api/sources/${key}/sync`, { method: "POST" }),

  // setup wizard (first-run source connection)
  setupTelegramSendCode: (phone?: string) =>
    jsonFetch<{ ok: boolean; already_authorized?: boolean; phone_code_hash?: string }>(
      `/api/setup/telegram/send-code`,
      { method: "POST", body: JSON.stringify(phone ? { phone } : {}) }),
  setupTelegramSignIn: (body: { code: string; phone_code_hash: string; phone?: string; password?: string }) =>
    jsonFetch<{ ok: boolean; need_2fa?: boolean; user?: string | null }>(
      `/api/setup/telegram/sign-in`, { method: "POST", body: JSON.stringify(body) }),
  setupTelegramCancel: () =>
    jsonFetch<{ ok: boolean }>(`/api/setup/telegram/cancel`, { method: "POST" }),
  getSecretStatus: (sourceKey: string) =>
    jsonFetch<{ configured: boolean; updated_at: string | null }>(`/api/setup/secrets/${sourceKey}`),
  putSecret: (sourceKey: string, secret: string) =>
    jsonFetch<{ ok: boolean; configured: boolean }>(
      `/api/setup/secrets/${sourceKey}`, { method: "POST", body: JSON.stringify({ secret }) }),
  uploadLinkedinExport: async (file: globalThis.File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/setup/linkedin/upload`, { method: "POST", body: fd });
    if (!res.ok) {
      let detail = `${res.status}`;
      try { detail = (await res.json()).detail ?? detail; } catch { /* keep status */ }
      throw new Error(detail);
    }
    return res.json() as Promise<{
      ok: boolean;
      counts: { connections?: number; messages?: number; imported?: number } | null;
      log_tail: string;
    }>;
  },
  regenerateToday: () =>
    jsonFetch<{ ok: true; plan: DailyPlan | null; log_tail: string }>(
      `/api/today/regenerate`,
      { method: "POST" },
    ),

  // --- projects -------------------------------------------------

  listProjects: (params: { status?: string } = {}, opts?: { cookieHeader?: string }) => {
    const sp = new URLSearchParams();
    if (params.status) sp.set("status", params.status);
    const qs = sp.toString();
    return jsonFetch<ProjectRow[]>(`/api/projects${qs ? `?${qs}` : ""}`, { cookieHeader: opts?.cookieHeader });
  },
  getProject: (slugOrId: string, opts?: { cookieHeader?: string }) =>
    jsonFetch<ProjectDetail>(`/api/projects/${slugOrId}`, { cookieHeader: opts?.cookieHeader }),
  listProjectRecaps: (projectId: string, opts?: { cookieHeader?: string }) =>
    jsonFetch<Array<{
      id: string; source: string; source_id: string; title: string | null;
      meeting_date: string | null; recap: string | null;
      attendees: Array<{ name?: string; email?: string }>; ingested_at: string;
    }>>(`/api/projects/${projectId}/recaps`, { cookieHeader: opts?.cookieHeader }),
  createProject: (body: { slug: string; name: string; description?: string; status?: ProjectStatus; color?: string }) =>
    jsonFetch<ProjectRow>(`/api/projects`, { method: "POST", body: JSON.stringify(body) }),
  patchProject: (id: string, body: Record<string, unknown>) =>
    jsonFetch<ProjectDetail>(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteProject: (id: string) =>
    jsonFetch<void>(`/api/projects/${id}`, { method: "DELETE" }),
  addProjectMember: (projectId: string, person_id: string, role?: string) =>
    jsonFetch<void>(`/api/projects/${projectId}/members`, {
      method: "POST", body: JSON.stringify({ person_id, role }),
    }),
  removeProjectMember: (projectId: string, personId: string) =>
    jsonFetch<void>(`/api/projects/${projectId}/members/${personId}`, { method: "DELETE" }),

  // --- tasks ---------------------------------------------------

  listTasks: (
    params: { project_id?: string; status?: string; with_person_id?: string; q?: string; limit?: number; offset?: number } = {},
    opts?: { cookieHeader?: string },
  ) => {
    const sp = new URLSearchParams();
    if (params.project_id) sp.set("project_id", params.project_id);
    if (params.status) sp.set("status", params.status);
    if (params.with_person_id) sp.set("with_person_id", params.with_person_id);
    if (params.q) sp.set("q", params.q);
    sp.set("limit", String(params.limit ?? 50));
    sp.set("offset", String(params.offset ?? 0));
    return jsonFetch<TaskRow[]>(`/api/tasks?${sp.toString()}`, { cookieHeader: opts?.cookieHeader });
  },
  createTask: (body: {
    title: string; description?: string; project_id?: string;
    opportunity_id?: string; with_person_id?: string;
    assignee_person_id?: string; parent_task_id?: string; person_ids?: string[];
    status?: TaskStatus; due_date?: string; due_time?: string; duration_min?: number;
  }) =>
    jsonFetch<TaskRow>(`/api/tasks`, { method: "POST", body: JSON.stringify(body) }),

  // --- task → calendar sync ---
  listCalendars: (opts?: { cookieHeader?: string }) =>
    jsonFetch<{ accounts: { account: string; calendars?: GoogleCalendar[] }[]; default: string | null }>(
      `/api/calendars`, { cookieHeader: opts?.cookieHeader }),
  addTaskToCalendar: (id: string, account: string, calendar_id?: string | null) =>
    jsonFetch<TaskRow>(`/api/tasks/${id}/calendar`, { method: "PUT", body: JSON.stringify({ account, calendar_id: calendar_id ?? null }) }),
  removeTaskFromCalendar: (id: string) =>
    jsonFetch<TaskRow>(`/api/tasks/${id}/calendar`, { method: "DELETE" }),

  // --- recurring routines ---
  listRoutines: (opts?: { cookieHeader?: string; projectId?: string }) =>
    jsonFetch<RecurringTaskRow[]>(
      opts?.projectId ? `/api/routines?project_id=${encodeURIComponent(opts.projectId)}` : `/api/routines`,
      { cookieHeader: opts?.cookieHeader }),
  createRoutine: (body: {
    title: string; description?: string; project_id?: string; with_person_id?: string;
    at_time?: string | null; duration_min?: number | null; freq: string; byweekday?: number[]; anchor_date?: string;
    participant_ids?: string[];
  }) =>
    jsonFetch<RecurringTaskRow>(`/api/routines`, { method: "POST", body: JSON.stringify(body) }),
  patchRoutine: (id: string, body: Record<string, unknown>) =>
    jsonFetch<RecurringTaskRow>(`/api/routines/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteRoutine: (id: string) =>
    jsonFetch<void>(`/api/routines/${id}`, { method: "DELETE" }),
  addRoutineToCalendar: (id: string, account: string, calendar_id?: string | null) =>
    jsonFetch<RecurringTaskRow>(`/api/routines/${id}/calendar`, { method: "PUT", body: JSON.stringify({ account, calendar_id: calendar_id ?? null }) }),
  removeRoutineFromCalendar: (id: string) =>
    jsonFetch<RecurringTaskRow>(`/api/routines/${id}/calendar`, { method: "DELETE" }),
  getTask: (id: string, opts?: { cookieHeader?: string }) =>
    jsonFetch<TaskDetail>(`/api/tasks/${id}`, { cookieHeader: opts?.cookieHeader }),
  patchTask: (id: string, body: Record<string, unknown>) =>
    jsonFetch<TaskRow>(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteTask: (id: string) =>
    jsonFetch<void>(`/api/tasks/${id}`, { method: "DELETE" }),
  addTaskPerson: (id: string, person_id: string) =>
    jsonFetch<void>(`/api/tasks/${id}/people`, { method: "POST", body: JSON.stringify({ person_id }) }),
  removeTaskPerson: (id: string, personId: string) =>
    jsonFetch<void>(`/api/tasks/${id}/people/${personId}`, { method: "DELETE" }),
  decomposeTask: (id: string) =>
    jsonFetch<{ subtasks: string[] }>(`/api/tasks/${id}/decompose`, { method: "POST" }),
  createSubtasks: (id: string, titles: string[]) =>
    jsonFetch<TaskDetail>(`/api/tasks/${id}/subtasks`, { method: "POST", body: JSON.stringify({ titles }) }),

  // --- opportunities ------------------------------------------

  listOpportunities: (
    params: { project_id?: string; stage?: string; counterparty_id?: string; q?: string; tags?: string[]; limit?: number; offset?: number } = {},
    opts?: { cookieHeader?: string },
  ) => {
    const sp = new URLSearchParams();
    if (params.project_id) sp.set("project_id", params.project_id);
    if (params.stage) sp.set("stage", params.stage);
    if (params.counterparty_id) sp.set("counterparty_id", params.counterparty_id);
    if (params.q) sp.set("q", params.q);
    if (params.tags?.length) sp.set("tags", params.tags.join(","));
    sp.set("limit", String(params.limit ?? 50));
    sp.set("offset", String(params.offset ?? 0));
    return jsonFetch<OpportunityRow[]>(`/api/opportunities?${sp.toString()}`, { cookieHeader: opts?.cookieHeader });
  },
  createOpportunity: (body: {
    title: string; description?: string; project_id?: string;
    counterparty_id?: string; company?: string; company_id?: string; responsible_person_id?: string;
    stage?: OpportunityStage; estimated_value?: string;
    award_usd?: number; award_note?: string; tags?: string[];
  }) =>
    jsonFetch<OpportunityRow>(`/api/opportunities`, { method: "POST", body: JSON.stringify(body) }),
  listOpportunityTags: (opts?: { cookieHeader?: string }) =>
    jsonFetch<string[]>(`/api/opportunities/tags`, { cookieHeader: opts?.cookieHeader }),
  getOpportunity: (id: string, opts?: { cookieHeader?: string }) =>
    jsonFetch<OpportunityDetail>(`/api/opportunities/${id}`, { cookieHeader: opts?.cookieHeader }),
  getPipeline: (params: { project_id?: string } = {}, opts?: { cookieHeader?: string }) => {
    const sp = new URLSearchParams();
    if (params.project_id) sp.set("project_id", params.project_id);
    const qs = sp.toString();
    return jsonFetch<PipelineSummary>(`/api/pipeline${qs ? `?${qs}` : ""}`, { cookieHeader: opts?.cookieHeader });
  },
  patchOpportunity: (id: string, body: Record<string, unknown>) =>
    jsonFetch<OpportunityRow>(`/api/opportunities/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  // --- deal-stage config -----------------------------------------
  listStages: (opts?: { cookieHeader?: string }) =>
    jsonFetch<StageConfig[]>(`/api/stages`, { cookieHeader: opts?.cookieHeader }),
  createStage: (body: { label: string; color?: string; terminal?: boolean; closes?: boolean }) =>
    jsonFetch<StageConfig>(`/api/stages`, { method: "POST", body: JSON.stringify(body) }),
  patchStage: (key: string, body: { label?: string; color?: string; terminal?: boolean; closes?: boolean }) =>
    jsonFetch<StageConfig>(`/api/stages/${key}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteStage: (key: string) =>
    jsonFetch<void>(`/api/stages/${key}`, { method: "DELETE" }),
  reorderStages: (keys: string[]) =>
    jsonFetch<StageConfig[]>(`/api/stages/reorder`, { method: "PUT", body: JSON.stringify({ keys }) }),

  changeOppStage: (id: string, body: { stage: OpportunityStage; next_step?: string; note?: string }) =>
    jsonFetch<OpportunityDetail>(`/api/opportunities/${id}/stage`, { method: "POST", body: JSON.stringify(body) }),
  addOppEvent: (id: string, body: { next_step?: string; note?: string }) =>
    jsonFetch<OpportunityDetail>(`/api/opportunities/${id}/events`, { method: "POST", body: JSON.stringify(body) }),
  deleteOpportunity: (id: string) =>
    jsonFetch<void>(`/api/opportunities/${id}`, { method: "DELETE" }),

  // --- companies (Phase 6) -------------------------------------

  listCompanies: (params: { q?: string; limit?: number } = {}, opts?: { cookieHeader?: string }) => {
    const sp = new URLSearchParams();
    if (params.q) sp.set("q", params.q);
    sp.set("limit", String(params.limit ?? 200));
    return jsonFetch<CompanyRow[]>(`/api/companies?${sp.toString()}`, { cookieHeader: opts?.cookieHeader });
  },
  getCompany: (id: string, opts?: { cookieHeader?: string }) =>
    jsonFetch<CompanyDetail>(`/api/companies/${id}`, { cookieHeader: opts?.cookieHeader }),
  createCompany: (body: { name: string; country?: string; website?: string; description?: string }) =>
    jsonFetch<CompanyRow>(`/api/companies`, { method: "POST", body: JSON.stringify(body) }),
  patchCompany: (id: string, body: Record<string, unknown>) =>
    jsonFetch<CompanyDetail>(`/api/companies/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  setCompanySharing: (id: string, body: { visibility: "shared" | "private"; owner_member_id?: string | null }) =>
    jsonFetch<CompanyDetail>(`/api/companies/${id}/sharing`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteCompany: (id: string) =>
    jsonFetch<void>(`/api/companies/${id}`, { method: "DELETE" }),
  addCompanyPerson: (id: string, person_id: string, role?: string, is_current = true) =>
    jsonFetch<void>(`/api/companies/${id}/people`, { method: "POST", body: JSON.stringify({ person_id, role, is_current }) }),
  removeCompanyPerson: (id: string, personId: string) =>
    jsonFetch<void>(`/api/companies/${id}/people/${personId}`, { method: "DELETE" }),
  mergeCompany: (id: string, into_id: string) =>
    jsonFetch<{ ok: true }>(`/api/companies/${id}/merge`, { method: "POST", body: JSON.stringify({ into_id }) }),

  // LinkedIn link-review queue — fuzzy person↔company suggestions to vet.
  listLinkSuggestions: (params: { limit?: number } = {}, opts?: { cookieHeader?: string }) =>
    jsonFetch<LinkSuggestion[]>(
      `/api/companies/link-suggestions?limit=${params.limit ?? 100}`,
      { cookieHeader: opts?.cookieHeader },
    ),
  countLinkSuggestions: (opts?: { cookieHeader?: string }) =>
    jsonFetch<{ count: number }>(`/api/companies/link-suggestions/count`, { cookieHeader: opts?.cookieHeader }),
  dismissLinkSuggestion: (person_id: string, company_id: string) =>
    jsonFetch<void>(`/api/companies/link-suggestions/dismiss`, {
      method: "POST", body: JSON.stringify({ person_id, company_id }),
    }),

  countPersons: (params: { q?: string; company_id?: string; circle?: string }, opts?: { cookieHeader?: string }) => {
    const sp = new URLSearchParams();
    if (params.q) sp.set("q", params.q);
    if (params.company_id) sp.set("company_id", params.company_id);
    if (params.circle) sp.set("circle", params.circle);
    const qs = sp.toString();
    return jsonFetch<{ count: number }>(
      `/api/persons/count${qs ? `?${qs}` : ""}`,
      { cookieHeader: opts?.cookieHeader },
    );
  },

  softDeletePerson: (personId: string) =>
    jsonFetch<{ ok: true }>(
      `/api/persons/${personId}/delete`,
      { method: "POST" },
    ),

  restorePerson: (personId: string) =>
    jsonFetch<{ ok: true }>(
      `/api/persons/${personId}/restore`,
      { method: "POST" },
    ),

  listCleanupCandidates: (
    params: { q?: string; limit?: number; offset?: number },
    opts?: { cookieHeader?: string },
  ) =>
    jsonFetch<CleanupCandidate[]>(
      `/api/cleanup/candidates?` +
        new URLSearchParams({
          ...(params.q ? { q: params.q } : {}),
          limit: String(params.limit ?? 50),
          offset: String(params.offset ?? 0),
        }),
      { cookieHeader: opts?.cookieHeader },
    ),

  countCleanupCandidates: (
    params: { q?: string },
    opts?: { cookieHeader?: string },
  ) =>
    jsonFetch<{ count: number }>(
      `/api/cleanup/count` + (params.q ? `?q=${encodeURIComponent(params.q)}` : ""),
      { cookieHeader: opts?.cookieHeader },
    ),

  listTelegramGroups: (
    params: {
      q?: string; kind?: string; enabled?: boolean;
      min_members?: number; max_members?: number;
      sort?: string;
      limit?: number; offset?: number;
    },
    opts?: { cookieHeader?: string },
  ) => {
    const sp = new URLSearchParams();
    if (params.q) sp.set("q", params.q);
    if (params.kind) sp.set("kind", params.kind);
    if (params.enabled !== undefined) sp.set("enabled", String(params.enabled));
    if (params.min_members !== undefined) sp.set("min_members", String(params.min_members));
    if (params.max_members !== undefined) sp.set("max_members", String(params.max_members));
    if (params.sort) sp.set("sort", params.sort);
    sp.set("limit", String(params.limit ?? 50));
    sp.set("offset", String(params.offset ?? 0));
    return jsonFetch<TelegramGroup[]>(
      `/api/telegram/groups?${sp.toString()}`,
      { cookieHeader: opts?.cookieHeader },
    );
  },

  countTelegramGroups: (
    params: {
      q?: string; kind?: string; enabled?: boolean;
      min_members?: number; max_members?: number;
    },
    opts?: { cookieHeader?: string },
  ) => {
    const sp = new URLSearchParams();
    if (params.q) sp.set("q", params.q);
    if (params.kind) sp.set("kind", params.kind);
    if (params.enabled !== undefined) sp.set("enabled", String(params.enabled));
    if (params.min_members !== undefined) sp.set("min_members", String(params.min_members));
    if (params.max_members !== undefined) sp.set("max_members", String(params.max_members));
    return jsonFetch<{ count: number }>(
      `/api/telegram/groups/count?${sp.toString()}`,
      { cookieHeader: opts?.cookieHeader },
    );
  },

  getTelegramGroup: (
    chatId: number,
    params: { limit?: number; offset?: number } = {},
    opts?: { cookieHeader?: string },
  ) => {
    const sp = new URLSearchParams();
    sp.set("limit", String(params.limit ?? 50));
    sp.set("offset", String(params.offset ?? 0));
    return jsonFetch<{
      group: TelegramGroup & { msg_count: number };
      senders: Array<{
        sender_telegram_id: string;
        person_id: string | null;
        display_name: string;
        msg_count: number;
        latest_at: string;
      }>;
      recent_messages: Array<{
        id: number;
        message_date: string;
        kind: string;
        body_excerpt: string;
        sender_telegram_id: string;
        sender_person_id: string | null;
        sender_display_name: string;
      }>;
    }>(
      `/api/telegram/groups/${chatId}?${sp.toString()}`,
      { cookieHeader: opts?.cookieHeader },
    );
  },

  toggleTelegramGroup: (chatId: number, enabled?: boolean) =>
    jsonFetch<{ ok: true; chat_id: number; enabled: boolean }>(
      `/api/telegram/groups/${chatId}/toggle`,
      {
        method: "POST",
        body: enabled !== undefined ? JSON.stringify({ enabled }) : undefined,
      },
    ),

  // group follow suggestions (backlog #3)
  listGroupSuggestions: (opts?: { cookieHeader?: string }) =>
    jsonFetch<GroupSuggestion[]>(`/api/telegram/groups/suggestions`, { cookieHeader: opts?.cookieHeader }),
  groupSuggestionsCount: (opts?: { cookieHeader?: string }) =>
    jsonFetch<{ count: number }>(`/api/telegram/groups/suggestions/count`, { cookieHeader: opts?.cookieHeader }),
  dismissGroupSuggestion: (chatId: number) =>
    jsonFetch<void>(`/api/telegram/groups/${chatId}/dismiss`, { method: "POST" }),

  backfillTelegramGroup: (chatId: number, sinceDays?: number) =>
    jsonFetch<{ ok: true; chat_id: number; title: string | null; seen: number; new: number; log_tail: string }>(
      `/api/telegram/groups/${chatId}/backfill`,
      {
        method: "POST",
        body: sinceDays !== undefined ? JSON.stringify({ since_days: sinceDays }) : undefined,
      },
    ),

  discoverTelegramGroups: () =>
    jsonFetch<{ ok: true; total: number; log_tail: string }>(
      `/api/telegram/groups/discover`,
      { method: "POST" },
    ),

  cleanupDeleteBatch: (ids: string[]) =>
    jsonFetch<{ deleted: number; requested: number }>(
      `/api/cleanup/delete-batch`,
      { method: "POST", body: JSON.stringify({ ids }) },
    ),

  getPerson: (id: string, opts?: { cookieHeader?: string }) =>
    jsonFetch<PersonDetail>(`/api/persons/${id}`, { cookieHeader: opts?.cookieHeader }),
  setPersonSharing: (id: string, body: { visibility: "shared" | "private"; owner_member_id?: string | null }) =>
    jsonFetch<PersonDetail>(`/api/persons/${id}/sharing`, { method: "PATCH", body: JSON.stringify(body) }),
  // sensitivity routing: sensitive contacts are processed by the LOCAL LLM only
  setPersonSensitivity: (id: string, sensitive: boolean) =>
    jsonFetch<PersonDetail>(`/api/persons/${id}/sensitivity`, { method: "PATCH", body: JSON.stringify({ sensitive }) }),

  // mail (read-only Gmail reader — backlog #2 Phase 1)
  listMailAccounts: (opts?: { cookieHeader?: string }) =>
    jsonFetch<MailAccount[]>(`/api/mail/accounts`, { cookieHeader: opts?.cookieHeader }),
  listMailThreads: (params: { q?: string; account?: string; category?: string; content?: string; archived?: "hide" | "only" | "all"; starred?: boolean; trashed?: "hide" | "only" | "all"; snoozed?: "hide" | "only" | "all"; limit?: number; offset?: number } = {}, opts?: { cookieHeader?: string }) => {
    const qs = new URLSearchParams();
    if (params.q) qs.set("q", params.q);
    if (params.account) qs.set("account", params.account);
    if (params.category) qs.set("category", params.category);
    if (params.content) qs.set("content", params.content);
    if (params.archived) qs.set("archived", params.archived);
    if (params.starred) qs.set("starred", "true");
    if (params.trashed) qs.set("trashed", params.trashed);
    if (params.snoozed) qs.set("snoozed", params.snoozed);
    if (params.limit != null) qs.set("limit", String(params.limit));
    if (params.offset != null) qs.set("offset", String(params.offset));
    const s = qs.toString();
    return jsonFetch<MailThreadRow[]>(`/api/mail/threads${s ? `?${s}` : ""}`, { cookieHeader: opts?.cookieHeader });
  },
  scanMailSpam: (account?: string) =>
    jsonFetch<{ scanned: number; spam: number; errors: number }>(
      `/api/mail/scan-spam${account ? `?account=${encodeURIComponent(account)}` : ""}`,
      { method: "POST" }),
  listMailSenders: (params: { account?: string; q?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.account) qs.set("account", params.account);
    if (params.q) qs.set("q", params.q);
    if (params.limit != null) qs.set("limit", String(params.limit));
    const s = qs.toString();
    return jsonFetch<MailSenderRow[]>(`/api/mail/senders${s ? `?${s}` : ""}`);
  },
  actOnMailSender: (body: { from_address: string; account?: string; action: "read" | "archive" | "trash" }) =>
    jsonFetch<{ threads: number; gmail_pushed: number; gmail_errors: string[] | null }>(
      `/api/mail/sender/act`, { method: "POST", body: JSON.stringify(body) }),
  listMailCleanupSenders: (params: { account?: string; q?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (params.account) qs.set("account", params.account);
    if (params.q) qs.set("q", params.q);
    if (params.limit != null) qs.set("limit", String(params.limit));
    const s = qs.toString();
    return jsonFetch<MailCleanupSenderRow[]>(`/api/mail/cleanup/senders${s ? `?${s}` : ""}`);
  },
  bulkActOnMailSenders: (body: { from_addresses: string[]; account?: string; action: "read" | "archive" | "trash" }) =>
    jsonFetch<{ senders: number; threads: number; gmail_pushed: number; gmail_errors: string[] | null }>(
      `/api/mail/senders/bulk-act`, { method: "POST", body: JSON.stringify(body) }),
  unsubscribeMailSender: (body: { from_address: string; account?: string }) =>
    jsonFetch<{ ok: boolean; method: "one-click" | "link" | "mailto" | "none"; url: string | null; status?: number; error?: string }>(
      `/api/mail/sender/unsubscribe`, { method: "POST", body: JSON.stringify(body) }),
  keepMailSender: (body: { from_address: string; keep: boolean }) =>
    jsonFetch<{ from_address: string; kept: boolean; on_clear_list: boolean }>(
      `/api/mail/sender/keep`, { method: "POST", body: JSON.stringify(body) }),
  clearListMailSender: (body: { from_address: string; clear: boolean }) =>
    jsonFetch<{ from_address: string; on_clear_list: boolean; kept: boolean }>(
      `/api/mail/sender/clear`, { method: "POST", body: JSON.stringify(body) }),
  listMailClearList: (params: { account?: string } = {}) =>
    jsonFetch<MailCleanupSenderRow[]>(
      `/api/mail/cleanup/clear-list${params.account ? `?account=${encodeURIComponent(params.account)}` : ""}`),
  // snoozed_until is tri-state on the wire: omitted (undefined, dropped by
  // JSON.stringify) = untouched; explicit null = unsnooze; ISO string = snooze.
  setMailState: (body: { account_email: string; thread_key: string; archived?: boolean; starred?: boolean; read?: boolean; trashed?: boolean; snoozed_until?: string | null }) =>
    jsonFetch<{ account_email: string; thread_key: string; archived: boolean; starred: boolean; read: boolean;
      trashed: boolean; snoozed_until: string | null;
      gmail_synced: boolean; gmail_reason: string | null }>(
      `/api/mail/thread/state`, { method: "POST", body: JSON.stringify(body) }),
  // user correction of a thread's content class → ground truth (model_version='user')
  setMailClass: (body: { account_email: string; thread_key: string; content_class: "newsletter" | "transactional" | "personal" }) =>
    jsonFetch<{ thread_key: string; content_class: string; messages: number }>(
      `/api/mail/thread/class`, { method: "POST", body: JSON.stringify(body) }),
  markAllMailRead: (account?: string) =>
    jsonFetch<{ marked: number }>(
      `/api/mail/mark-all-read${account ? `?account=${encodeURIComponent(account)}` : ""}`,
      { method: "POST" }),
  getMailThread: (key: string, account?: string, opts?: { cookieHeader?: string }) => {
    const qs = new URLSearchParams({ key });
    if (account) qs.set("account", account);
    return jsonFetch<MailMessage[]>(`/api/mail/thread?${qs.toString()}`, { cookieHeader: opts?.cookieHeader });
  },
  sendMail: (body: { account_email: string; to: string; subject?: string | null; body: string; html?: string | null; in_reply_to?: string | null; references?: string | null; thread_id?: string | null }) =>
    jsonFetch<{ ok: true; message_id: string | null }>(`/api/mail/send`, { method: "POST", body: JSON.stringify(body) }),

  addIdentity: (id: string, body: { source: string; source_id: string }) =>
    jsonFetch<IdentityRow>(`/api/persons/${id}/identities`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  removeIdentity: (personId: string, identityId: number) =>
    jsonFetch<void>(`/api/persons/${personId}/identities/${identityId}`, {
      method: "DELETE",
    }),

  listCandidates: (params: { limit?: number; offset?: number }, opts?: { cookieHeader?: string }) =>
    jsonFetch<MergeCandidate[]>(
      `/api/merge/candidates?` +
        new URLSearchParams({
          limit: String(params.limit ?? 20),
          offset: String(params.offset ?? 0),
        }),
      { cookieHeader: opts?.cookieHeader },
    ),

  getCandidate: (candidateId: number, opts?: { cookieHeader?: string }) =>
    jsonFetch<MergeCandidate>(`/api/merge/candidates/${candidateId}`, {
      cookieHeader: opts?.cookieHeader,
    }),

  listRelatedCandidates: (candidateId: number, limit = 8) =>
    jsonFetch<MergeCandidate[]>(
      `/api/merge/candidates/${candidateId}/related?limit=${limit}`,
    ),

  listPendingForPerson: (personId: string, opts?: { cookieHeader?: string }) =>
    jsonFetch<PendingMergeForPerson[]>(
      `/api/persons/${personId}/pending-merges`,
      { cookieHeader: opts?.cookieHeader },
    ),

  listSimilarPersons: (personId: string, opts?: { cookieHeader?: string }) =>
    jsonFetch<SimilarPerson[]>(
      `/api/persons/${personId}/similar`,
      { cookieHeader: opts?.cookieHeader },
    ),

  directMerge: (personId: string, otherId: string, winner: "this" | "other") =>
    jsonFetch<{ ok: true; winner_id: string; loser_id: string }>(
      `/api/persons/${personId}/merge-with/${otherId}?winner=${winner}`,
      { method: "POST" },
    ),

  listDrafts: (personId: string, opts?: { cookieHeader?: string }) =>
    jsonFetch<DraftRow[]>(`/api/persons/${personId}/drafts`, { cookieHeader: opts?.cookieHeader }),
  generateDraft: (personId: string, body: { channel: DraftChannel; task_id?: string; opportunity_id?: string }) =>
    jsonFetch<DraftRow>(`/api/persons/${personId}/drafts/generate`, { method: "POST", body: JSON.stringify(body) }),
  patchDraft: (draftId: string, body: { body?: string; subject?: string; status?: DraftStatus }) =>
    jsonFetch<DraftRow>(`/api/drafts/${draftId}`, { method: "PATCH", body: JSON.stringify(body) }),
  sendDraft: (draftId: string) =>
    jsonFetch<{ ok: true; queued?: boolean; sent?: boolean; to?: string; outbox_id?: string; message_id?: string }>(
      `/api/drafts/${draftId}/send`, { method: "POST" }),

  nameSuggestions: (personId: string, opts?: { cookieHeader?: string }) =>
    jsonFetch<NameSuggestionsResponse>(
      `/api/persons/${personId}/name-suggestions`,
      { cookieHeader: opts?.cookieHeader },
    ),

  renamePerson: (personId: string, display_name: string) =>
    jsonFetch<{ ok: true; display_name: string }>(
      `/api/persons/${personId}/rename`,
      { method: "POST", body: JSON.stringify({ display_name }) },
    ),

  setPersonBirthday: (personId: string, birthday: string | null) =>
    jsonFetch<{ ok: true; birthday: string | null }>(
      `/api/persons/${personId}/birthday`,
      { method: "PUT", body: JSON.stringify({ birthday }) },
    ),

  dismissSimilar: (personId: string, otherId: string) =>
    jsonFetch<void>(`/api/persons/${personId}/similar/${otherId}/dismiss`, {
      method: "POST",
    }),

  regenerateCandidates: () =>
    jsonFetch<{
      ok: true;
      live_pending: number;
      auto_rejected: { zombie: number; incompatible: number; weak_fuzzy: number };
      log_tail: string;
    }>(`/api/merge/regenerate`, { method: "POST" }),

  decideCandidate: (id: number, body: { decision: "approve" | "reject" | "defer"; winner?: "left" | "right"; note?: string }) =>
    jsonFetch<{ ok: true; decision: string }>(`/api/merge/candidates/${id}/decision`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  login: (token: string) =>
    jsonFetch<{ ok: true; role: "admin" | "budget" }>(`/api/login`, {
      method: "POST",
      body: JSON.stringify({ token }),
    }),

  logout: () =>
    jsonFetch<{ ok: true }>(`/api/logout`, { method: "POST" }),

  // --- finance / budget --------------------------------------------
  finance: {
    listAssets: (opts?: { cookieHeader?: string }) =>
      jsonFetch<FinAsset[]>(`/api/finance/assets`, { cookieHeader: opts?.cookieHeader }),
    createAsset: (body: Partial<FinAsset> & { code: string }) =>
      jsonFetch<FinAsset>(`/api/finance/assets`, { method: "POST", body: JSON.stringify(body) }),

    listAccounts: (params: { include_archived?: boolean; account_class?: "operational" | "investment" } = {}, opts?: { cookieHeader?: string }) => {
      const sp = new URLSearchParams({ include_archived: params.include_archived ? "true" : "false" });
      if (params.account_class) sp.set("account_class", params.account_class);
      return jsonFetch<FinAccount[]>(`/api/finance/accounts?${sp.toString()}`, { cookieHeader: opts?.cookieHeader });
    },
    getCryptoCutoff: (opts?: { cookieHeader?: string }) =>
      jsonFetch<{ cutoff: string }>(`/api/finance/crypto-cutoff`, { cookieHeader: opts?.cookieHeader }),
    setCryptoCutoff: (cutoff: string) =>
      jsonFetch<{ cutoff: string }>(`/api/finance/crypto-cutoff`, { method: "PUT", body: JSON.stringify({ cutoff }) }),
    createAccount: (body: Record<string, unknown>) =>
      jsonFetch<FinAccount>(`/api/finance/accounts`, { method: "POST", body: JSON.stringify(body) }),
    patchAccount: (id: string, body: Record<string, unknown>) =>
      jsonFetch<FinAccount>(`/api/finance/accounts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    deleteAccount: (id: string) =>
      jsonFetch<void>(`/api/finance/accounts/${id}`, { method: "DELETE" }),

    // members + sharing (PR1; owner/admin only)
    listMembers: (opts?: { cookieHeader?: string }) =>
      jsonFetch<FinMember[]>(`/api/finance/members`, { cookieHeader: opts?.cookieHeader }),
    createMember: (body: { display_name: string; actor: string; email?: string | null; person_id?: string | null; role?: "owner" | "member"; is_active?: boolean }) =>
      jsonFetch<FinMember>(`/api/finance/members`, { method: "POST", body: JSON.stringify(body) }),
    patchMember: (id: string, body: Record<string, unknown>) =>
      jsonFetch<FinMember>(`/api/finance/members/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

    listCategories: (opts?: { cookieHeader?: string }) =>
      jsonFetch<FinCategory[]>(`/api/finance/categories`, { cookieHeader: opts?.cookieHeader }),
    createCategory: (body: Record<string, unknown>) =>
      jsonFetch<FinCategory>(`/api/finance/categories`, { method: "POST", body: JSON.stringify(body) }),
    patchCategory: (key: string, body: Record<string, unknown>) =>
      jsonFetch<FinCategory>(`/api/finance/categories/${key}`, { method: "PATCH", body: JSON.stringify(body) }),
    deleteCategory: (key: string) =>
      jsonFetch<void>(`/api/finance/categories/${key}`, { method: "DELETE" }),

    netWorth: (opts?: { cookieHeader?: string }) =>
      jsonFetch<NetWorth>(`/api/finance/net-worth`, { cookieHeader: opts?.cookieHeader }),

    listTransactions: (params: {
      account_id?: string; category_key?: string; txn_type?: string;
      date_from?: string; date_to?: string; q?: string; limit?: number; offset?: number;
    } = {}, opts?: { cookieHeader?: string }) => {
      const sp = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) if (v != null && v !== "") sp.set(k, String(v));
      return jsonFetch<FinTransaction[]>(`/api/finance/transactions?${sp.toString()}`, { cookieHeader: opts?.cookieHeader });
    },
    createTransaction: (body: Record<string, unknown>) =>
      jsonFetch<FinTransaction>(`/api/finance/transactions`, { method: "POST", body: JSON.stringify(body) }),
    patchTransaction: (id: string, body: Record<string, unknown>) =>
      jsonFetch<FinTransaction | PendingApproval>(`/api/finance/transactions/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    deleteTransaction: (id: string) =>
      jsonFetch<void | PendingApproval>(`/api/finance/transactions/${id}`, { method: "DELETE" }),

    // approvals (sharing PR3)
    listApprovals: (params: { status?: string } = {}, opts?: { cookieHeader?: string }) =>
      jsonFetch<FinApproval[]>(`/api/finance/approvals${params.status ? `?status_filter=${params.status}` : ""}`, { cookieHeader: opts?.cookieHeader }),
    pendingApprovalsCount: (opts?: { cookieHeader?: string }) =>
      jsonFetch<{ count: number }>(`/api/finance/approvals/pending-count`, { cookieHeader: opts?.cookieHeader }),
    approveApproval: (id: string, note?: string) =>
      jsonFetch<FinApproval>(`/api/finance/approvals/${id}/approve`, { method: "POST", body: JSON.stringify({ note: note ?? null }) }),
    rejectApproval: (id: string, note?: string) =>
      jsonFetch<FinApproval>(`/api/finance/approvals/${id}/reject`, { method: "POST", body: JSON.stringify({ note: note ?? null }) }),

    reportSpending: (date_from: string, date_to: string, opts?: { cookieHeader?: string }) =>
      jsonFetch<SpendingLine[]>(`/api/finance/reports/spending?date_from=${date_from}&date_to=${date_to}`,
        { cookieHeader: opts?.cookieHeader }),
    reportCashflow: (months: number, opts?: { cookieHeader?: string }) =>
      jsonFetch<CashflowMonth[]>(`/api/finance/reports/cashflow?months=${months}`, { cookieHeader: opts?.cookieHeader }),

    listPayees: (params: { q?: string } = {}, opts?: { cookieHeader?: string }) =>
      jsonFetch<FinPayee[]>(`/api/finance/payees${params.q ? `?q=${encodeURIComponent(params.q)}` : ""}`,
        { cookieHeader: opts?.cookieHeader }),
    createPayee: (body: { name: string; company_id?: string | null; person_id?: string | null }) =>
      jsonFetch<FinPayee>(`/api/finance/payees`, { method: "POST", body: JSON.stringify(body) }),
    patchPayee: (id: string, body: { name?: string; company_id?: string | null; person_id?: string | null }) =>
      jsonFetch<FinPayee>(`/api/finance/payees/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

    listHoldings: (params: { account_id?: string } = {}, opts?: { cookieHeader?: string }) =>
      jsonFetch<FinHolding[]>(`/api/finance/holdings${params.account_id ? `?account_id=${params.account_id}` : ""}`,
        { cookieHeader: opts?.cookieHeader }),
    upsertHolding: (body: { account_id: string; asset_id?: string; asset_code?: string; asset_kind?: string; quantity: number; cost_basis_usd?: number | null }) =>
      jsonFetch<FinHolding>(`/api/finance/holdings`, { method: "PUT", body: JSON.stringify(body) }),
    deleteHolding: (id: string) =>
      jsonFetch<void>(`/api/finance/holdings/${id}`, { method: "DELETE" }),
    syncWallets: () =>
      jsonFetch<{ wallets: number; synced: number; transfers?: number; gas?: number; results: { account: string; chain?: string; assets?: number; error?: string }[] }>(
        `/api/finance/sync-wallets`, { method: "POST" }),
    getWalletsSummary: (opts?: { cookieHeader?: string }) =>
      jsonFetch<WalletsSummary>(`/api/finance/wallets/summary`, { cookieHeader: opts?.cookieHeader }),

    // investments: FIFO cost lots (Phase 2)
    getPositions: (params: { account_id: string }, opts?: { cookieHeader?: string }) =>
      jsonFetch<FinPosition[]>(`/api/finance/positions?account_id=${params.account_id}`,
        { cookieHeader: opts?.cookieHeader }),
    createLot: (body: { account_id: string; asset_id?: string; asset_code?: string; asset_kind?: string; open_date: string; quantity: number; cost_per_unit_usd: number; note?: string | null }) =>
      jsonFetch<FinLot>(`/api/finance/lots`, { method: "POST", body: JSON.stringify(body) }),
    deleteLot: (id: string) =>
      jsonFetch<void>(`/api/finance/lots/${id}`, { method: "DELETE" }),
    createSale: (body: { account_id: string; asset_id: string; sale_date: string; quantity: number; proceeds_per_unit_usd: number; note?: string | null }) =>
      jsonFetch<FinSale>(`/api/finance/sales`, { method: "POST", body: JSON.stringify(body) }),
    deleteSale: (id: string) =>
      jsonFetch<void>(`/api/finance/sales/${id}`, { method: "DELETE" }),

    listPlanned: (opts?: { cookieHeader?: string }) =>
      jsonFetch<FinPlanned[]>(`/api/finance/planned`, { cookieHeader: opts?.cookieHeader }),
    createPlanned: (body: Record<string, unknown>) =>
      jsonFetch<FinPlanned>(`/api/finance/planned`, { method: "POST", body: JSON.stringify(body) }),
    patchPlanned: (id: string, body: Record<string, unknown>) =>
      jsonFetch<FinPlanned>(`/api/finance/planned/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    deletePlanned: (id: string) =>
      jsonFetch<void>(`/api/finance/planned/${id}`, { method: "DELETE" }),
    postPlanned: (id: string) =>
      jsonFetch<FinPlanned>(`/api/finance/planned/${id}/post`, { method: "POST" }),

    listBudgets: (month: string | undefined, opts?: { cookieHeader?: string }) =>
      jsonFetch<FinBudget[]>(`/api/finance/budgets${month ? `?month=${month}` : ""}`, { cookieHeader: opts?.cookieHeader }),
    upsertBudget: (category_key: string, limit_usd: number) =>
      jsonFetch<FinBudget>(`/api/finance/budgets`, { method: "PUT", body: JSON.stringify({ category_key, limit_usd }) }),
    deleteBudget: (category_key: string) =>
      jsonFetch<void>(`/api/finance/budgets/${category_key}`, { method: "DELETE" }),

    listImports: (opts?: { cookieHeader?: string }) =>
      jsonFetch<FinImportBatch[]>(`/api/finance/imports`, { cookieHeader: opts?.cookieHeader }),
    syncZenmoney: () =>
      jsonFetch<{ ok: true; transactions: number; accounts: number; categories: number; payees: number }>(
        `/api/finance/import/zenmoney`, { method: "POST" }),
    confirmImport: (batchId: string, body: { account_id?: string; skip_indices?: number[] }) =>
      jsonFetch<{ ok: true; created: number; skipped: number }>(`/api/finance/imports/${batchId}/confirm`,
        { method: "POST", body: JSON.stringify(body) }),
    discardImport: (batchId: string) =>
      jsonFetch<{ ok: true }>(`/api/finance/imports/${batchId}/discard`, { method: "POST" }),
    // PDF/CSV upload uses raw fetch (multipart, not jsonFetch)
    uploadStatement: async (file: File, accountId?: string) => {
      const fd = new FormData();
      fd.append("file", file);
      if (accountId) fd.append("account_id", accountId);
      const res = await fetch(`/api/finance/import/pdf`, { method: "POST", body: fd, credentials: "include" });
      if (!res.ok) throw new Error(`Import failed: ${(await res.text()).slice(0, 200)}`);
      return (await res.json()) as FinImportBatch;
    },
  },
};

// --- finance types ---------------------------------------------------
export type FinAsset = {
  id: string; code: string; name: string | null; kind: "fiat" | "crypto" | "stock";
  decimals: number; symbol: string | null; chain: string | null;
  contract_address: string | null; is_active: boolean; usd_rate: number | null;
};

export type FinBalance = {
  asset_id: string; asset_code: string; asset_kind: string;
  balance: number; usd_value: number | null;
};

export type FinAccount = {
  id: string; name: string; kind: "bank" | "cash" | "crypto_wallet" | "cex" | "dex" | "brokerage" | "debt";
  account_class: "operational" | "investment";
  currency_asset_id: string | null; currency_code: string | null;
  owner: "me" | "wife" | "son"; account_group: string | null; institution: string | null;
  wallet_address: string | null; chain: string | null;
  person_id: string | null; person_name: string | null;
  visibility: "shared" | "private"; owner_member_id: string | null; owner_member_name: string | null;
  opening_balance: number; include_in_net_worth: boolean; archived: boolean; sort: number;
  source_kind: string | null; balances: FinBalance[];
  created_at: string; updated_at: string;
};

export type FinMember = {
  id: string; display_name: string; email: string | null;
  person_id: string | null; person_name: string | null;
  role: "owner" | "member"; actor: string; is_active: boolean;
  created_at: string; updated_at: string;
};

export type PendingApproval = { status: "pending_approval"; approval_id: string };

export type FinApproval = {
  id: string; action: "update_txn" | "delete_txn"; target_table: string; target_id: string;
  payload: Record<string, unknown>; status: "pending" | "approved" | "rejected"; note: string | null;
  requested_by: string; requested_by_name: string | null;
  decided_by: string | null; decided_by_name: string | null;
  created_at: string; decided_at: string | null;
  txn_date: string | null; payee_text: string | null;
  amount: number | null; asset_code: string | null; account_name: string | null;
};

export type FinCategory = {
  key: string; label: string; parent_key: string | null;
  kind: "expense" | "income" | "both"; sort: number; color: string; icon: string | null; in_use: number;
};

export type FinTransaction = {
  id: string; txn_date: string; txn_type: "expense" | "income" | "transfer";
  outflow_account_id: string | null; outflow_account_name: string | null;
  outflow_asset_id: string | null; outflow_asset_code: string | null; outflow_amount: number | null;
  inflow_account_id: string | null; inflow_account_name: string | null;
  inflow_asset_id: string | null; inflow_asset_code: string | null; inflow_amount: number | null;
  category_key: string | null; category_label: string | null; category_color: string | null;
  payee_id: string | null; payee_text: string | null;
  person_id: string | null; person_name: string | null;
  note: string | null; tags: string[]; source_kind: string | null;
  usd_value: number | null;
  created_at: string; updated_at: string;
};

export type NetWorth = {
  total_usd: number; total_thb: number; operational_usd: number; investment_usd: number; usd_thb_rate: number;
  by_asset: { asset_id: string; asset_code: string; asset_kind: string; balance: number; usd_value: number }[];
  by_group: { group: string; usd_value: number }[];
  by_owner: { group: string; usd_value: number }[];
};

export type SpendingLine = { category_key: string; label: string; color: string; usd_total: number; txn_count: number };
export type CashflowMonth = { month: string; income: number; expense: number };

export type FinPayee = {
  id: string; name: string;
  person_id: string | null; person_name: string | null;
  company_id: string | null; company_name: string | null;
  txn_count: number;
};

export type FinBudget = {
  id: string; category_key: string; category_label: string;
  color: string; limit_usd: number; actual_usd: number;
};

export type FinHolding = {
  id: string; account_id: string; account_name: string | null;
  asset_id: string; asset_code: string; asset_kind: string; chain: string | null;
  quantity: number; cost_basis_usd: number | null; source: string;
  usd_value: number; updated_at: string;
};

// investments: FIFO cost lots (Phase 2)
export type FinLot = {
  id: string; account_id: string; asset_id: string; asset_code: string; asset_kind: string;
  open_date: string; quantity: number; cost_per_unit_usd: number;
  note: string | null; created_at: string;
};

export type FinSale = {
  id: string; account_id: string; asset_id: string; asset_code: string;
  sale_date: string; quantity: number; proceeds_per_unit_usd: number;
  realized_gain_usd: number | null; note: string | null; created_at: string;
};

export type FinPositionLot = {
  id: string; open_date: string; quantity: number; remaining_quantity: number; cost_per_unit_usd: number;
};

export type FinPosition = {
  account_id: string; asset_id: string; asset_code: string; asset_kind: string;
  remaining_quantity: number; open_cost_usd: number; avg_cost_per_unit_usd: number | null;
  current_price_usd: number | null; market_value_usd: number | null;
  unrealized_gain_usd: number | null; realized_gain_usd: number;
  lots: FinPositionLot[];
};

export type WalletAssetRollup = {
  asset_code: string; asset_kind: string; quantity: number; usd_value: number;
  wallets: { account_id: string; account_name: string | null; chain: string | null; quantity: number; usd_value: number }[];
};
export type WalletRollup = {
  account_id: string; account_name: string | null; chain: string | null;
  usd_value: number; gas_usd: number; transfers: number;
  last_synced_at: string | null; sync_status: "ok" | "partial" | "error" | null; sync_error: string | null;
};
export type WalletsSummary = {
  total_usd: number; total_gas_usd: number;
  by_asset: WalletAssetRollup[]; by_wallet: WalletRollup[];
};

export type FinPlanned = {
  id: string; name: string | null; txn_type: "expense" | "income" | "transfer";
  outflow_account_id: string | null; outflow_account_name: string | null;
  outflow_asset_id: string | null; outflow_asset_code: string | null; outflow_amount: number | null;
  inflow_account_id: string | null; inflow_account_name: string | null;
  inflow_asset_id: string | null; inflow_asset_code: string | null; inflow_amount: number | null;
  category_key: string | null; category_label: string | null;
  payee_text: string | null; note: string | null;
  freq: "daily" | "weekly" | "monthly" | "yearly"; byweekday: number[];
  next_date: string; auto_post: boolean; active: boolean;
};

export type FinImportBatch = {
  id: string; kind: string; filename: string | null; account_id: string | null;
  status: "pending" | "confirmed" | "discarded"; row_count: number;
  parsed: { kind?: string; currency?: string | null; rows?: Array<{ date: string; amount: number; direction: string; description: string; balance: number | null }> } | null;
  note: string | null; created_at: string; decided_at: string | null;
};

export type MailAccount = { account_email: string; messages: number; last_at: string | null; can_send: boolean };

export type MailSenderRow = {
  from_address: string;
  from_name: string | null;
  messages: number;
  unread: number;
  last_at: string | null;
  content_class: string | null;
  unsubscribe_url: string | null;
};

export type MailCleanupSenderRow = {
  from_address: string;
  from_name: string | null;
  messages: number;
  unread: number;
  first_at: string | null;
  last_at: string | null;
  content_class: string | null;
  unsubscribe_url: string | null;
  one_click: boolean;
  replied: boolean;
  kept: boolean;
  on_clear_list: boolean;
};

export type MailTriageSignals = {
  automated: boolean;
  bulk: boolean;
  mailing_list: boolean;
  has_unsubscribe: boolean;
  unsubscribe_url: string | null;
};

export type MailThreadRow = {
  thread_key: string;
  account_email: string;
  from_address: string | null;
  from_name: string | null;
  subject: string | null;
  snippet: string | null;
  internal_date: string;
  labels: string[];
  category: string;
  unread: boolean;
  archived: boolean;
  starred: boolean;
  trashed: boolean;
  snoozed_until: string | null;
  msg_count: number;
  signals: MailTriageSignals;
  spam_score: number | null;
  spam_action: string | null;
  is_spam: boolean;
  content_class: string | null;
  content_confidence: number | null;
};

export type MailAttachment = {
  filename: string;
  mime_type: string;
  size: number | null;
  attachment_id: string;
};

export type MailMessage = {
  message_id: string;
  thread_id: string | null;
  rfc822_message_id: string | null;
  account_email: string;
  from_address: string | null;
  from_name: string | null;
  to_addresses: string[] | null;
  cc_addresses: string[] | null;
  subject: string | null;
  body_text: string | null;
  body_html: string | null;
  internal_date: string;
  labels: string[];
  attachments: MailAttachment[];
  signals: MailTriageSignals;
  spam_score: number | null;
  spam_action: string | null;
  is_spam: boolean;
  content_class: string | null;
  content_confidence: number | null;
};

export type GroupSuggestion = {
  chat_id: string;
  title: string | null;
  kind: string;
  member_count: number | null;
  last_message_at: string | null;
  last_seen_at: string | null;
};
