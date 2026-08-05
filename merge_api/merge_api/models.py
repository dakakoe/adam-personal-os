from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, Field


IdentitySource = Literal["telegram", "email", "linkedin", "phone", "x", "instagram", "github", "website", "telegram_handle"]
MergeDecision = Literal["approve", "reject", "defer"]


class PersonRow(BaseModel):
    """Row in the persons list — minimal for fast rendering."""
    person_id: str
    display_name: str
    visibility: Literal["shared", "private"] = "private"
    sensitive: bool = False
    telegram_username: str | None = None
    email: str | None = None
    linkedin: str | None = None
    total_interactions: int
    last_interaction_at: datetime | None = None


class ProspectRow(BaseModel):
    """A reconnect candidate — ranked by relationship depth × how cold it's gone."""
    person_id: str
    display_name: str
    telegram_username: str | None = None
    email: str | None = None
    total_interactions: int
    last_interaction_at: datetime | None = None
    days_since: int | None = None
    has_profile: bool = False
    summary: str | None = None
    in_pipeline: bool = False
    dismissed: bool = False
    score: int
    distance: float | None = None    # ICP semantic distance (search mode only; lower = closer fit)


class ContactSharing(BaseModel):
    """Owner sets a contact's sharing (unified-sharing — contacts)."""
    visibility: Literal["shared", "private"] = "private"
    owner_member_id: str | None = None


class IdentityRow(BaseModel):
    identity_id: int
    source: str
    source_id: str
    evidence: dict[str, Any] | None = None
    created_at: datetime


class SignalRow(BaseModel):
    signal_type: str
    value: str
    confidence: str
    source: str


class LinkedInInfo(BaseModel):
    vanity: str                    # 'chykhradze' → https://linkedin.com/in/chykhradze
    url: str
    company: str | None = None
    position: str | None = None
    connected_on: str | None = None
    location: str | None = None    # from linkedin_imported_contact when joined by email


class BioRow(BaseModel):
    """A bio-ish blurb from one source. `kind` is informational only:
    'bio' (free-text self-describing), 'role' (position + company), 'title'
    (job title), 'notes' (free-text from contact notes field)."""
    source: str
    kind: str
    text: str
    fetched_at: datetime | None = None


# Deal stages are config-driven (memory.opp_stage) — any text key; the API
# validates against the config table, not a fixed Literal.
OpportunityStage = str
TaskStatus = Literal["open", "doing", "done", "cancelled"]
ProjectStatus = Literal["active", "paused", "archived"]


class ProjectMember(BaseModel):
    person_id: str
    role: str | None = None
    added_at: datetime
    display_name: str


class ProjectRow(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None = None
    status: ProjectStatus
    color: str | None = None
    created_at: datetime
    updated_at: datetime
    member_count: int = 0
    open_task_count: int = 0
    live_opp_count: int = 0


class ProjectDetail(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None = None
    status: ProjectStatus
    color: str | None = None
    created_at: datetime
    updated_at: datetime
    members: list[ProjectMember] = Field(default_factory=list)


class ProjectCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    status: ProjectStatus = "active"
    color: str | None = None


class ProjectPatch(BaseModel):
    slug: str | None = None
    name: str | None = None
    description: str | None = None
    status: ProjectStatus | None = None
    color: str | None = None


class ProjectMemberIn(BaseModel):
    person_id: str
    role: str | None = None


class TaskPerson(BaseModel):
    person_id: str
    display_name: str
    added_at: datetime | None = None


class TaskRow(BaseModel):
    id: str
    title: str
    description: str | None = None
    project_id: str | None = None
    project_slug: str | None = None
    project_name: str | None = None
    project_color: str | None = None
    opportunity_id: str | None = None
    opportunity_title: str | None = None       # the deal this task belongs to
    with_person_id: str | None = None
    with_person_name: str | None = None
    assignee_person_id: str | None = None      # null = "Me"
    assignee_name: str | None = None
    parent_task_id: str | None = None
    subtask_total: int = 0
    subtask_done: int = 0
    people_count: int = 0
    status: TaskStatus
    due_date: date | None = None
    due_time: time | None = None
    duration_min: int | None = None
    source_kind: str | None = None
    source_ref: str | None = None
    gcal_account: str | None = None        # connected calendar this task syncs to
    gcal_event_id: str | None = None
    gcal_html_link: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class TaskDetail(TaskRow):
    people: list[TaskPerson] = Field(default_factory=list)
    subtasks: list[TaskRow] = Field(default_factory=list)


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    project_id: str | None = None
    opportunity_id: str | None = None
    with_person_id: str | None = None
    assignee_person_id: str | None = None
    parent_task_id: str | None = None
    person_ids: list[str] = Field(default_factory=list)
    status: TaskStatus = "open"
    due_date: date | None = None
    due_time: time | None = None
    duration_min: int | None = None
    source_kind: str | None = None
    source_ref: str | None = None


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    project_id: str | None = None
    opportunity_id: str | None = None
    with_person_id: str | None = None
    assignee_person_id: str | None = None
    parent_task_id: str | None = None
    status: TaskStatus | None = None
    due_date: date | None = None
    due_time: time | None = None
    duration_min: int | None = None


class RoutineParticipant(BaseModel):
    person_id: str
    display_name: str
    email: str | None = None    # null = no email on file → can't be invited
    sensitive: bool = False     # sensitive contacts are never put on a Google invite


class RecurringTaskRow(BaseModel):
    id: str
    title: str
    description: str | None = None
    project_id: str | None = None
    project_slug: str | None = None
    project_name: str | None = None
    project_color: str | None = None
    with_person_id: str | None = None
    with_person_name: str | None = None
    at_time: time | None = None
    duration_min: int | None = None
    freq: Literal["daily", "weekly", "monthly", "yearly"]
    byweekday: list[int] = Field(default_factory=list)   # 0=Mon … 6=Sun (weekly)
    anchor_date: date
    active: bool
    gcal_account: str | None = None        # connected calendar the recurring event lives on
    gcal_event_id: str | None = None
    gcal_html_link: str | None = None
    participants: list[RoutineParticipant] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class RecurringTaskIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    project_id: str | None = None
    with_person_id: str | None = None
    at_time: time | None = None          # null = all-day reminder
    duration_min: int | None = None
    freq: Literal["daily", "weekly", "monthly", "yearly"]
    byweekday: list[int] = Field(default_factory=list)
    anchor_date: date | None = None      # defaults to today server-side
    participant_ids: list[str] = Field(default_factory=list)   # people to invite


class RecurringTaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    project_id: str | None = None
    with_person_id: str | None = None
    at_time: time | None = None
    duration_min: int | None = None
    freq: Literal["daily", "weekly", "monthly", "yearly"] | None = None
    byweekday: list[int] | None = None
    anchor_date: date | None = None
    active: bool | None = None
    participant_ids: list[str] | None = None   # None = leave participants unchanged


class CalendarTargetIn(BaseModel):
    account: str = Field(..., min_length=1)   # connected Google account email
    calendar_id: str | None = None            # sub-calendar id; None = primary


class TaskPersonIn(BaseModel):
    person_id: str = Field(..., min_length=1)


class SubtasksCreateIn(BaseModel):
    titles: list[str] = Field(default_factory=list)


class OpportunityRow(BaseModel):
    id: str
    title: str
    description: str | None = None
    project_id: str | None = None
    project_slug: str | None = None
    project_name: str | None = None
    project_color: str | None = None
    counterparty_id: str | None = None
    counterparty_name: str | None = None
    company: str | None = None
    company_id: str | None = None
    company_name: str | None = None
    responsible_person_id: str | None = None     # null = "Me"
    responsible_name: str | None = None
    stage: OpportunityStage
    estimated_value: str | None = None            # legacy free-text
    award_usd: float | None = None
    award_note: str | None = None
    # Free-form stream labels ('job', 'consulting', …) so one board can hold
    # unrelated pipelines and still be filtered per stream.
    tags: list[str] = Field(default_factory=list)
    source_kind: str | None = None
    source_ref: str | None = None
    task_count: int = 0
    open_task_count: int = 0
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


class OpportunityEvent(BaseModel):
    id: str
    kind: str                                     # 'stage_change' | 'note'
    from_stage: OpportunityStage | None = None
    to_stage: OpportunityStage | None = None
    next_step: str | None = None
    note: str | None = None
    created_at: datetime


class OpportunityDetail(OpportunityRow):
    events: list[OpportunityEvent] = Field(default_factory=list)
    tasks: list["TaskRow"] = Field(default_factory=list)


class OpportunityCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    project_id: str | None = None
    counterparty_id: str | None = None
    company: str | None = None
    company_id: str | None = None
    responsible_person_id: str | None = None
    stage: OpportunityStage = "intro"
    estimated_value: str | None = None
    award_usd: float | None = None
    award_note: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_kind: str | None = None
    source_ref: str | None = None


class OpportunityPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    project_id: str | None = None
    counterparty_id: str | None = None
    company: str | None = None
    company_id: str | None = None
    responsible_person_id: str | None = None
    estimated_value: str | None = None
    award_usd: float | None = None
    award_note: str | None = None
    tags: list[str] | None = None      # None = leave tags unchanged


class OpportunityStageIn(BaseModel):
    stage: OpportunityStage
    next_step: str | None = None
    note: str | None = None


class StageConfigRow(BaseModel):
    key: str
    label: str
    sort: int
    terminal: bool
    closes: bool
    color: str
    in_use: int = 0


class StageCreateIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=60)
    color: str = Field("slate", max_length=20)
    terminal: bool = False
    closes: bool = False


class StagePatchIn(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=60)
    color: str | None = Field(None, max_length=20)
    terminal: bool | None = None
    closes: bool | None = None


class StageReorderIn(BaseModel):
    keys: list[str] = Field(..., min_length=1)


class OpportunityEventIn(BaseModel):
    next_step: str | None = None
    note: str | None = None


SuggestionKind = Literal["task", "opportunity", "person_mention"]
SuggestionStatus = Literal["pending", "accepted", "dismissed"]


class GranolaAttendee(BaseModel):
    name: str | None = None
    email: str | None = None


class GranolaIngest(BaseModel):
    """Payload pushed to /api/ingest/granola — one meeting. `summary` is
    Granola's AI summary markdown (preferred); `transcript` optional raw
    fallback if no summary exists."""
    source_id: str = Field(..., min_length=1)        # Granola meeting UUID
    title: str | None = None
    meeting_date: datetime | None = None
    attendees: list[GranolaAttendee] = Field(default_factory=list)
    summary: str | None = None
    transcript: str | None = None
    # When false (default), a meeting whose recap was already processed is
    # skipped (no re-extraction) — lets a recurring poll cron pass the same
    # recent window cheaply. Set true to force re-extraction.
    force: bool = False


class SourceStatusIn(BaseModel):
    """A DB-less background worker (Granola) reporting its own health to
    POST /api/sources/{key}/status."""
    status: str                       # 'ok' | 'needs_attention'
    reason: str | None = None         # short human-readable reason


class TelegramSignInIn(BaseModel):
    """Setup wizard: finish the Telegram sign-in. phone_code_hash round-trips
    from /api/setup/telegram/send-code; password only when the account has 2FA."""
    code: str = Field(..., min_length=1)
    phone_code_hash: str = Field(..., min_length=1)
    phone: str | None = None
    password: str | None = None


class SourceSecretIn(BaseModel):
    """Setup wizard: store a per-source secret (write-only; never echoed)."""
    secret: str = Field(..., min_length=1)


class SuggestionRow(BaseModel):
    id: str
    kind: SuggestionKind
    status: SuggestionStatus
    title: str
    detail: str | None = None
    project_id: str | None = None
    project_slug: str | None = None
    project_name: str | None = None
    project_color: str | None = None
    person_id: str | None = None
    person_name_raw: str | None = None
    person_name: str | None = None
    suggested_stage: OpportunityStage | None = None
    estimated_value: str | None = None
    due_date: date | None = None
    owner_hint: str | None = None
    confidence: str
    source_kind: str
    source_ref: str | None = None
    created_at: datetime
    decided_at: datetime | None = None
    context_at: datetime | None = None      # when it was actually discussed
    accepted_entity_kind: str | None = None
    accepted_entity_id: str | None = None
    recap_title: str | None = None
    recap_date: datetime | None = None


class FocusItem(BaseModel):
    title: str
    reason: str | None = None
    kind: str                       # task|opportunity|reply|suggestion|other
    ref_id: str | None = None
    project_slug: str | None = None


class DailyPlanRow(BaseModel):
    id: str
    plan_date: date
    narrative: str | None = None
    # The full structured payload (focus list + counts + raw input
    # snapshots). Kept loose so the worker can evolve its shape without a
    # model migration; the UI reads known keys defensively.
    structured: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime


class SuggestionReassignIn(BaseModel):
    person_id: str = Field(..., min_length=1)


class MeetingRecapRow(BaseModel):
    id: str
    source: str
    source_id: str
    title: str | None = None
    meeting_date: datetime | None = None
    recap: str | None = None
    attendees: list[dict[str, Any]] = Field(default_factory=list)
    ingested_at: datetime


DraftChannel = Literal["telegram", "email"]
DraftStatus = Literal["draft", "sent", "discarded"]


class DraftRow(BaseModel):
    id: str
    person_id: str
    channel: DraftChannel
    subject: str | None = None
    body: str
    status: DraftStatus
    task_id: str | None = None
    opportunity_id: str | None = None
    model: str | None = None
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None = None


class DraftGenerateIn(BaseModel):
    channel: DraftChannel = "telegram"
    task_id: str | None = None
    opportunity_id: str | None = None


class DraftPatch(BaseModel):
    body: str | None = None
    subject: str | None = None
    status: DraftStatus | None = None


class NameSuggestion(BaseModel):
    """A candidate display name found in a raw.* table. The UI surfaces
    these when the current display_name looks synthetic."""
    source: str         # 'linkedin' | 'google_contacts' | 'telegram'
    suggested: str
    evidence: str       # short human label of where it came from


class PersonSensitivity(BaseModel):
    """Sensitivity-routing opt-in: process this contact's messages with the
    local LLM only (never a cloud provider)."""
    sensitive: bool


class PersonDetail(BaseModel):
    person_id: str
    display_name: str
    notes: str | None = None
    visibility: Literal["shared", "private"] = "private"
    sensitive: bool = False
    owner_member_id: str | None = None
    owner_member_name: str | None = None
    telegram_username: str | None = None
    telegram_bio: str | None = None
    phone: str | None = None
    birthday: str | None = None    # ISO date "YYYY-MM-DD" (year may be sentinel 1900)
    linkedin: LinkedInInfo | None = None
    structured: dict[str, Any] | None = None
    summary: str | None = None
    total_interactions: int
    inbound_count: int
    outbound_count: int
    first_interaction_at: datetime | None = None
    last_interaction_at: datetime | None = None
    channels: list[str] = Field(default_factory=list)
    identities: list[IdentityRow] = Field(default_factory=list)
    signals: list[SignalRow] = Field(default_factory=list)
    bios: list[BioRow] = Field(default_factory=list)
    tasks: list[TaskRow] = Field(default_factory=list)
    opportunities: list[OpportunityRow] = Field(default_factory=list)
    companies: list[PersonCompany] = Field(default_factory=list)
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)


class CompanyPersonRow(BaseModel):
    person_id: str
    display_name: str
    role: str | None = None
    is_current: bool = True
    added_at: datetime | None = None


class PersonCompany(BaseModel):
    company_id: str
    name: str
    role: str | None = None
    is_current: bool = True


class CompanyRow(BaseModel):
    id: str
    name: str
    country: str | None = None
    website: str | None = None
    domain: str | None = None
    description: str | None = None
    visibility: Literal["shared", "private"] = "private"
    created_at: datetime
    updated_at: datetime
    people_count: int = 0
    live_opp_count: int = 0
    pipeline_usd: float = 0.0


class CompanyDetail(BaseModel):
    id: str
    name: str
    country: str | None = None
    website: str | None = None
    domain: str | None = None
    description: str | None = None
    visibility: Literal["shared", "private"] = "private"
    owner_member_id: str | None = None
    owner_member_name: str | None = None
    created_at: datetime
    updated_at: datetime
    people: list[CompanyPersonRow] = Field(default_factory=list)
    opportunities: list[OpportunityRow] = Field(default_factory=list)


class CompanyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    country: str | None = None
    website: str | None = None
    description: str | None = None


class CompanyPatch(BaseModel):
    name: str | None = None
    country: str | None = None
    website: str | None = None
    description: str | None = None


class CompanyPersonIn(BaseModel):
    person_id: str = Field(..., min_length=1)
    role: str | None = None
    is_current: bool = True


class CompanyMergeIn(BaseModel):
    into_id: str = Field(..., min_length=1)   # fold THIS company into into_id


class LinkSuggestionDismissIn(BaseModel):
    person_id: str = Field(..., min_length=1)
    company_id: str = Field(..., min_length=1)


# --- Telegram bot capture (voice/text → task/opp/event) ---
CaptureType = Literal["task", "opportunity", "event", "expense", "income", "transfer"]


class CaptureIn(BaseModel):
    text: str = ""                  # optional when an image is supplied
    source: str = "telegram_text"   # 'telegram_text' | 'telegram_voice' | 'telegram_photo'
    image_b64: str | None = None    # base64 payment-slip photo (vision capture)
    image_media_type: str = "image/jpeg"
    owner: str = "me"               # household member who captured it: me|wife|son


class CaptureConfirmIn(BaseModel):
    override_type: CaptureType | None = None


class CaptureReclassifyIn(BaseModel):
    target_type: CaptureType


class CaptureInviteIn(BaseModel):
    # Index into the capture's stored invite_emails; None / out-of-range = don't invite.
    index: int | None = None


class CapturePatchIn(BaseModel):
    """Edit a still-pending capture's fields in place (bot Edit menu). Every
    field is optional; only the ones supplied are applied. No LLM call — these
    just mutate the stored `parsed` blob and re-render the card."""
    title: str | None = Field(None, min_length=1, max_length=500)
    # 'YYYY-MM-DD' (also accepts today/tomorrow-style strings the bot resolves);
    # validated server-side as a DATE only (no tz math). clear_due_date wins.
    due_date: str | None = None
    clear_due_date: bool = False
    # person: pick one of these. choice_idx indexes parsed['_person_choices'];
    # use_typed sets the raw typed name (parsed['_person_typed']) as a new contact.
    person_choice_idx: int | None = None
    use_typed_person: bool = False
    clear_person: bool = False
    # project_idx indexes list_projects(status=None) (same stable order the bot
    # renders); clear_project unassigns.
    project_idx: int | None = None
    clear_project: bool = False
    # event-only edits. event_time accepts 'HH:MM' or 'HH:MM-HH:MM' (sets the end
    # too). toggle_conference flips the Google-Meet flag; clear_location drops the
    # place. All re-assemble the event's start/end server-side.
    event_date: str | None = None
    event_time: str | None = None
    location: str | None = None
    clear_location: bool = False
    toggle_conference: bool = False
    # expense/income edits. account_choice_idx indexes parsed['_account_choices'];
    # category_choice_idx indexes parsed['_category_choices'] (set by the search).
    amount: float | None = None
    currency: str | None = None
    payee_text: str | None = None
    txn_date: str | None = None
    account_choice_idx: int | None = None
    # transfer legs — explicit source / destination (else account_choice_idx
    # fills whichever leg is still empty). Both index parsed['_account_choices'].
    from_account_choice_idx: int | None = None
    to_account_choice_idx: int | None = None
    category_choice_idx: int | None = None
    clear_category: bool = False
    # task scheduling
    task_time: str | None = None         # 'HH:MM'
    clear_task_time: bool = False
    repeat: Literal["none", "daily", "weekdays", "weekly", "monthly", "yearly"] | None = None
    toggle_weekday: int | None = None    # 0=Mon … 6=Sun (sets repeat=weekly)
    toggle_all_day: bool = False         # events: flip all-day on/off
    duration_min: int | None = None
    clear_duration: bool = False
    calendar_account: str | None = None  # connected calendar to sync to on confirm
    clear_calendar: bool = False


class RenameIn(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=200)


class BirthdayIn(BaseModel):
    # 'YYYY-MM-DD', or 'MM-DD' (year stored as 1900 sentinel), or null/empty to
    # clear the manual override. Validated server-side.
    birthday: str | None = None


class WeeklyGoalIn(BaseModel):
    # Tasks-per-week target behind the /today goal ring.
    goal: int = Field(..., ge=1, le=500)


class IdentityCreate(BaseModel):
    source: IdentitySource
    source_id: str = Field(..., min_length=1, max_length=512)


class IdentityRolePatch(BaseModel):
    """Manual role/company for a hand-added identity (LinkedIn vanities carry
    no data of their own). Empty string clears; omitted leaves unchanged."""
    position: str | None = None
    company: str | None = None


class MergeCandidateRow(BaseModel):
    id: int
    left: PersonDetail
    right: PersonDetail
    source: str
    confidence: str
    score: float | None = None
    evidence: dict[str, Any]
    created_at: datetime


class MergeDecisionIn(BaseModel):
    decision: MergeDecision
    winner: Literal["left", "right"] | None = None   # required for 'approve'
    note: str | None = None


class LoginIn(BaseModel):
    token: str


# --- finance / budget module ------------------------------------------------

AssetKind = Literal["fiat", "crypto", "stock"]
AccountKind = Literal["bank", "cash", "crypto_wallet", "cex", "dex", "brokerage", "debt"]
AccountOwner = Literal["me", "wife", "son"]
CategoryKind = Literal["expense", "income", "both"]


class FinAssetRow(BaseModel):
    id: str
    code: str
    name: str | None = None
    kind: AssetKind
    decimals: int
    symbol: str | None = None
    chain: str | None = None
    contract_address: str | None = None
    is_active: bool = True
    usd_rate: float | None = None     # latest fin_fx_rate to USD (computed)


class FinAssetCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=20)
    name: str | None = None
    kind: AssetKind = "fiat"
    decimals: int = Field(2, ge=0, le=18)
    symbol: str | None = None
    chain: str | None = None
    contract_address: str | None = None


class FinAssetPatch(BaseModel):
    name: str | None = None
    symbol: str | None = None
    decimals: int | None = Field(None, ge=0, le=18)
    chain: str | None = None
    contract_address: str | None = None
    is_active: bool | None = None


AccountClass = Literal["operational", "investment"]
AccountVisibility = Literal["shared", "private"]


class FinAccountRow(BaseModel):
    id: str
    name: str
    kind: AccountKind
    account_class: AccountClass = "operational"
    currency_asset_id: str | None = None
    currency_code: str | None = None         # joined from fin_asset
    owner: AccountOwner = "me"
    account_group: str | None = None
    institution: str | None = None
    wallet_address: str | None = None
    chain: str | None = None
    person_id: str | None = None
    person_name: str | None = None
    visibility: AccountVisibility = "shared"      # shared (all members) | private (owner only)
    owner_member_id: str | None = None
    owner_member_name: str | None = None         # joined from fin_member
    opening_balance: float = 0
    include_in_net_worth: bool = True
    archived: bool = False
    sort: int = 0
    source_kind: str | None = None
    # computed balances: one entry per asset held in the account
    balances: list["FinBalance"] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class FinBalance(BaseModel):
    asset_id: str
    asset_code: str
    asset_kind: AssetKind
    balance: float
    usd_value: float | None = None


class FinAccountCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    kind: AccountKind = "bank"
    account_class: AccountClass = "operational"
    currency_asset_id: str | None = None
    currency_code: str | None = None         # convenience: resolve code → id server-side
    owner: AccountOwner = "me"
    account_group: str | None = None
    institution: str | None = None
    wallet_address: str | None = None
    chain: str | None = None
    person_id: str | None = None
    visibility: AccountVisibility = "shared"
    owner_member_id: str | None = None
    opening_balance: float = 0
    include_in_net_worth: bool = True


class FinAccountPatch(BaseModel):
    name: str | None = None
    kind: AccountKind | None = None
    account_class: AccountClass | None = None
    currency_asset_id: str | None = None
    owner: AccountOwner | None = None
    account_group: str | None = None
    institution: str | None = None
    wallet_address: str | None = None
    chain: str | None = None
    person_id: str | None = None
    visibility: AccountVisibility | None = None
    owner_member_id: str | None = None
    opening_balance: float | None = None
    include_in_net_worth: bool | None = None
    archived: bool | None = None
    sort: int | None = None


class FinMemberRow(BaseModel):
    id: str
    display_name: str
    email: str | None = None
    person_id: str | None = None
    person_name: str | None = None
    role: Literal["owner", "member"] = "member"
    actor: str
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class FinMemberCreate(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=120)
    email: str | None = None
    person_id: str | None = None
    role: Literal["owner", "member"] = "member"
    actor: str = Field(..., min_length=1, max_length=60)
    is_active: bool = True


class FinMemberPatch(BaseModel):
    display_name: str | None = None
    email: str | None = None
    person_id: str | None = None
    role: Literal["owner", "member"] | None = None
    actor: str | None = None
    is_active: bool | None = None


class FinApprovalRow(BaseModel):
    id: str
    action: Literal["update_txn", "delete_txn"]
    target_table: str = "fin_transaction"
    target_id: str
    payload: dict = Field(default_factory=dict)
    status: Literal["pending", "approved", "rejected"] = "pending"
    note: str | None = None
    requested_by: str
    requested_by_name: str | None = None
    decided_by: str | None = None
    decided_by_name: str | None = None
    created_at: datetime
    decided_at: datetime | None = None
    # transaction summary (joined; null if the txn was hard-removed)
    txn_date: date | None = None
    payee_text: str | None = None
    amount: float | None = None
    asset_code: str | None = None
    account_name: str | None = None


class FinApprovalDecision(BaseModel):
    note: str | None = None


class FinCategoryRow(BaseModel):
    key: str
    label: str
    parent_key: str | None = None
    kind: CategoryKind
    sort: int = 0
    color: str = "slate"
    icon: str | None = None
    in_use: int = 0          # transaction count (delete guard)


class FinCategoryCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    parent_key: str | None = None
    kind: CategoryKind = "expense"
    color: str = "slate"
    icon: str | None = None


class FinCategoryPatch(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=100)
    parent_key: str | None = None
    kind: CategoryKind | None = None
    color: str | None = None
    icon: str | None = None
    sort: int | None = None


class FinPayeeRow(BaseModel):
    id: str
    name: str
    person_id: str | None = None
    person_name: str | None = None
    company_id: str | None = None
    company_name: str | None = None
    txn_count: int = 0


class FinPayeeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    person_id: str | None = None
    company_id: str | None = None


class FinPayeePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    person_id: str | None = None
    company_id: str | None = None


class FinBudgetRow(BaseModel):
    id: str
    category_key: str
    category_label: str
    color: str = "slate"
    limit_usd: float
    actual_usd: float = 0


class FinBudgetUpsert(BaseModel):
    category_key: str
    limit_usd: float = Field(..., ge=0)


class FinHoldingRow(BaseModel):
    id: str
    account_id: str
    account_name: str | None = None
    asset_id: str
    asset_code: str
    asset_kind: str
    chain: str | None = None
    quantity: float
    cost_basis_usd: float | None = None
    source: str
    usd_value: float = 0
    updated_at: datetime


class FinHoldingUpsert(BaseModel):
    account_id: str
    asset_id: str | None = None
    asset_code: str | None = None         # convenience — resolved/created server-side
    asset_kind: AssetKind = "stock"       # used only when creating a new asset by code
    quantity: float
    cost_basis_usd: float | None = None


# --- investments: FIFO cost lots (Phase 2) ---------------------------------

class FinLotRow(BaseModel):
    id: str
    account_id: str
    asset_id: str
    asset_code: str
    asset_kind: str
    open_date: date
    quantity: float
    cost_per_unit_usd: float
    note: str | None = None
    created_at: datetime


class FinLotCreate(BaseModel):
    account_id: str
    asset_id: str | None = None
    asset_code: str | None = None         # convenience — resolved/created server-side
    asset_kind: AssetKind = "stock"       # used only when creating a new asset by code
    open_date: date
    quantity: float = Field(..., gt=0)
    cost_per_unit_usd: float = Field(..., ge=0)
    note: str | None = None


class FinSaleRow(BaseModel):
    id: str
    account_id: str
    asset_id: str
    asset_code: str
    sale_date: date
    quantity: float
    proceeds_per_unit_usd: float
    realized_gain_usd: float | None = None   # FIFO-derived at read time
    note: str | None = None
    created_at: datetime


class FinSaleCreate(BaseModel):
    account_id: str
    asset_id: str
    sale_date: date
    quantity: float = Field(..., gt=0)
    proceeds_per_unit_usd: float = Field(..., ge=0)
    note: str | None = None


class FinPositionLot(BaseModel):
    id: str
    open_date: date
    quantity: float                 # original buy quantity
    remaining_quantity: float       # after FIFO matching against sells
    cost_per_unit_usd: float


class FinPositionRow(BaseModel):
    """Derived investment position for one (account, asset)."""
    account_id: str
    asset_id: str
    asset_code: str
    asset_kind: str
    remaining_quantity: float
    open_cost_usd: float                     # cost basis of the still-open lots
    avg_cost_per_unit_usd: float | None = None
    current_price_usd: float | None = None   # latest fin_fx_rate
    market_value_usd: float | None = None
    unrealized_gain_usd: float | None = None
    realized_gain_usd: float                 # summed across sells
    lots: list[FinPositionLot] = []


PlannedFreq = Literal["daily", "weekly", "monthly", "yearly"]


class FinPlannedRow(BaseModel):
    id: str
    name: str | None = None
    txn_type: str
    outflow_account_id: str | None = None
    outflow_account_name: str | None = None
    outflow_asset_id: str | None = None
    outflow_asset_code: str | None = None
    outflow_amount: float | None = None
    inflow_account_id: str | None = None
    inflow_account_name: str | None = None
    inflow_asset_id: str | None = None
    inflow_asset_code: str | None = None
    inflow_amount: float | None = None
    category_key: str | None = None
    category_label: str | None = None
    payee_text: str | None = None
    note: str | None = None
    freq: PlannedFreq
    byweekday: list[int] = Field(default_factory=list)
    next_date: date
    auto_post: bool = True
    active: bool = True


class FinPlannedCreate(BaseModel):
    name: str | None = None
    outflow_account_id: str | None = None
    outflow_asset_id: str | None = None
    outflow_amount: float | None = None
    inflow_account_id: str | None = None
    inflow_asset_id: str | None = None
    inflow_amount: float | None = None
    category_key: str | None = None
    payee_text: str | None = None
    note: str | None = None
    freq: PlannedFreq = "monthly"
    byweekday: list[int] = Field(default_factory=list)
    next_date: date
    auto_post: bool = True


class FinPlannedPatch(BaseModel):
    name: str | None = None
    outflow_account_id: str | None = None
    outflow_asset_id: str | None = None
    outflow_amount: float | None = None
    inflow_account_id: str | None = None
    inflow_asset_id: str | None = None
    inflow_amount: float | None = None
    category_key: str | None = None
    payee_text: str | None = None
    note: str | None = None
    freq: PlannedFreq | None = None
    byweekday: list[int] | None = None
    next_date: date | None = None
    auto_post: bool | None = None
    active: bool | None = None


class FinTransactionRow(BaseModel):
    id: str
    txn_date: date
    txn_type: str                            # expense | income | transfer (derived)
    outflow_account_id: str | None = None
    outflow_account_name: str | None = None
    outflow_asset_id: str | None = None
    outflow_asset_code: str | None = None
    outflow_amount: float | None = None
    inflow_account_id: str | None = None
    inflow_account_name: str | None = None
    inflow_asset_id: str | None = None
    inflow_asset_code: str | None = None
    inflow_amount: float | None = None
    category_key: str | None = None
    category_label: str | None = None
    category_color: str | None = None
    payee_id: str | None = None
    payee_text: str | None = None
    person_id: str | None = None
    person_name: str | None = None
    note: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_kind: str | None = None
    usd_value: float | None = None           # block-time USD value (on-chain rows)
    created_at: datetime
    updated_at: datetime


class FinTransactionCreate(BaseModel):
    txn_date: date
    outflow_account_id: str | None = None
    outflow_asset_id: str | None = None      # defaults to outflow account currency
    outflow_amount: float | None = None
    inflow_account_id: str | None = None
    inflow_asset_id: str | None = None       # defaults to inflow account currency
    inflow_amount: float | None = None
    category_key: str | None = None
    payee_id: str | None = None
    payee_text: str | None = None
    person_id: str | None = None
    note: str | None = None
    tags: list[str] = Field(default_factory=list)


class FinTransactionPatch(BaseModel):
    txn_date: date | None = None
    outflow_account_id: str | None = None
    outflow_asset_id: str | None = None
    outflow_amount: float | None = None
    inflow_account_id: str | None = None
    inflow_asset_id: str | None = None
    inflow_amount: float | None = None
    # Settle a leg in a chosen currency by code (e.g. pay a USD debt from a
    # USDT on-chain send) — resolved to an asset id server-side when the id
    # isn't given. Lets the counter-leg differ from the account's default asset.
    outflow_asset_code: str | None = None
    inflow_asset_code: str | None = None
    category_key: str | None = None
    payee_id: str | None = None
    payee_text: str | None = None
    person_id: str | None = None
    note: str | None = None
    tags: list[str] | None = None


class NetWorthAssetLine(BaseModel):
    asset_id: str
    asset_code: str
    asset_kind: AssetKind
    balance: float
    usd_value: float


class NetWorthGroup(BaseModel):
    group: str
    usd_value: float


class NetWorthRow(BaseModel):
    total_usd: float
    total_thb: float
    operational_usd: float = 0
    investment_usd: float = 0
    usd_thb_rate: float
    by_asset: list[NetWorthAssetLine] = Field(default_factory=list)
    by_group: list[NetWorthGroup] = Field(default_factory=list)
    by_owner: list[NetWorthGroup] = Field(default_factory=list)


class FinImportBatchRow(BaseModel):
    id: str
    kind: str
    filename: str | None = None
    account_id: str | None = None
    status: str
    row_count: int
    parsed: dict[str, Any] | None = None
    note: str | None = None
    created_at: datetime
    decided_at: datetime | None = None


class ImportConfirmIn(BaseModel):
    # account to attach the parsed rows to (defaults to batch.account_id)
    account_id: str | None = None
    # row indices to skip (user unchecked them in review)
    skip_indices: list[int] = Field(default_factory=list)


# --- mail (read-only Gmail reader — backlog #2 Phase 1) ---
class MailAccount(BaseModel):
    account_email: str
    messages: int = 0
    last_at: datetime | None = None
    can_send: bool = False    # granted scopes include gmail.send (compose From-picker)


class MailTriageSignals(BaseModel):
    """Header-heuristic triage signals (Phase B). High precision / low recall —
    they complement the Gmail-label `category`, never replace it."""
    automated: bool = False        # Auto-Submitted != 'no' (RFC 3834)
    bulk: bool = False             # Precedence: bulk|list|junk
    mailing_list: bool = False     # List-Id present
    has_unsubscribe: bool = False  # List-Unsubscribe present
    unsubscribe_url: str | None = None  # https one-click link, if offered


class MailThreadRow(BaseModel):
    thread_key: str
    account_email: str
    from_address: str | None = None
    from_name: str | None = None
    subject: str | None = None
    snippet: str | None = None
    internal_date: datetime
    labels: list[str] = Field(default_factory=list)
    category: str = "personal"   # spam|promotions|updates|forums|social|personal
    unread: bool = False
    archived: bool = False
    starred: bool = False
    trashed: bool = False
    snoozed_until: datetime | None = None
    msg_count: int = 1
    signals: MailTriageSignals = Field(default_factory=MailTriageSignals)
    spam_score: float | None = None      # Rspamd numeric score (Phase B2), if scanned
    spam_action: str | None = None       # Rspamd action, if scanned
    is_spam: bool = False                # action ∈ spammy set
    content_class: str | None = None     # SetFit class (Phase C): newsletter|transactional|personal
    content_confidence: float | None = None


class MailAttachment(BaseModel):
    """Attachment metadata for a mail message (Phase 3b). The bytes are fetched
    on demand from GET /api/mail/attachment using message_id + attachment_id."""
    filename: str
    mime_type: str = "application/octet-stream"
    size: int | None = None
    attachment_id: str


class MailMessage(BaseModel):
    message_id: str
    thread_id: str | None = None
    rfc822_message_id: str | None = None
    account_email: str
    from_address: str | None = None
    from_name: str | None = None
    to_addresses: list[str] | None = None
    cc_addresses: list[str] | None = None
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    internal_date: datetime
    labels: list[str] = Field(default_factory=list)
    attachments: list[MailAttachment] = Field(default_factory=list)
    signals: MailTriageSignals = Field(default_factory=MailTriageSignals)
    spam_score: float | None = None
    spam_action: str | None = None
    is_spam: bool = False
    content_class: str | None = None
    content_confidence: float | None = None


class MailSendIn(BaseModel):
    """Send/reply from the mail client (Phase 2). account_email must be
    gmail.send-authorized; in_reply_to/references/thread_id thread a reply."""
    account_email: str
    to: str = Field(..., min_length=1)
    subject: str | None = None
    body: str = ""                    # plain text (also the multipart text fallback)
    html: str | None = None           # optional rich HTML body (compose v2 / P5)
    in_reply_to: str | None = None
    references: str | None = None
    thread_id: str | None = None


class MailStateIn(BaseModel):
    """Local archive/star/read/trash/snooze state for a mail thread (Phase 3 +
    mail polish). Only provided fields change; account_email + thread_key
    identify the thread. snoozed_until is tri-state: omitted = untouched,
    explicit null = unsnooze (distinguished via model_fields_set)."""
    account_email: str
    thread_key: str
    archived: bool | None = None
    starred: bool | None = None
    read: bool | None = None
    trashed: bool | None = None
    snoozed_until: datetime | None = None


class MailClassIn(BaseModel):
    """User correction of a thread's content class (triage iteration). Applies
    to every message in the thread, stored as model_version='user' ground truth
    the classifier/refiner never overwrite."""
    account_email: str
    thread_key: str
    content_class: Literal["newsletter", "transactional", "personal"]


class MailSenderRow(BaseModel):
    """One row of the senders cleanup view (round 2 / P3): mail grouped by
    from_address, highest volume first."""
    from_address: str
    from_name: str | None = None
    messages: int = 0
    unread: int = 0
    last_at: datetime | None = None
    content_class: str | None = None      # the newest message's SetFit class
    unsubscribe_url: str | None = None    # https one-click, when offered


class MailSenderActIn(BaseModel):
    """Bulk action on everything from one sender. `read`/`archive` apply the
    app-local overlay AND push to Gmail (batchModify); `trash` moves the sender's
    mail to Gmail Trash (recoverable 30 days) and sets the local trashed flag."""
    from_address: str = Field(..., min_length=3)
    account: str | None = None            # limit to one account; None = all
    action: Literal["read", "archive", "trash"]


class MailCleanupSenderRow(BaseModel):
    """One row of the Mail-Cleanup page: a sender with the signals it's ranked and
    recommended on (highest-volume first)."""
    from_address: str
    from_name: str | None = None
    messages: int = 0
    unread: int = 0
    first_at: datetime | None = None
    last_at: datetime | None = None
    content_class: str | None = None      # the newest message's SetFit class
    unsubscribe_url: str | None = None    # https (or mailto) target, when offered
    one_click: bool = False               # RFC 8058 one-click POST available
    replied: bool = False                 # I've emailed this address → keep it
    kept: bool = False                    # user pinned as 'keep' → never recommend
    on_clear_list: bool = False           # flagged to bulk-trash (the clear list)


class MailSenderUnsubIn(BaseModel):
    """One-click unsubscribe from a sender (RFC 8058). When the sender offers a
    one-click POST target the server hits it; otherwise it returns the link/mailto
    for the user to complete."""
    from_address: str = Field(..., min_length=3)
    account: str | None = None


class MailBulkActIn(BaseModel):
    """The same bulk action across MANY senders at once — the cleanup page's
    'trash selected' — applied per sender (overlay + Gmail batchModify)."""
    from_addresses: list[str] = Field(..., min_length=1, max_length=500)
    account: str | None = None
    action: Literal["read", "archive", "trash"]


class MailSenderKeepIn(BaseModel):
    """Pin/unpin a sender as 'keep' — excluded from cleanup recommendations."""
    from_address: str = Field(..., min_length=3)
    keep: bool = True


class MailSenderClearIn(BaseModel):
    """Add/remove a sender on the 'clear list' — flagged to bulk-trash later."""
    from_address: str = Field(..., min_length=3)
    clear: bool = True
