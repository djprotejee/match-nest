export type Sport = "formula" | "cs2" | "football";
export type EventStatus = "past" | "live" | "delayed" | "upcoming" | "tbd";
export type FollowLevel = "main" | "starred" | "muted" | "hidden" | "explore";
export type RangeFilter = "today" | "week" | "month";

export interface MatchEvent {
  id: string;
  title: string;
  sport: Sport;
  starts_at: string | null;
  status: EventStatus;
  entity_ids: string[];
  source: string;
  competition: string | null;
  session_type: string | null;
  result_summary: string | null;
  importance: number;
  follow_level: FollowLevel;
  result_hidden: boolean;
}

export interface DayGroup {
  date: string;
  events: MatchEvent[];
}

export interface EntityItem {
  id: string;
  name: string;
  sport: Sport;
  kind: string;
  color: string;
  follow: FollowLevel;
  aliases?: string[];
  bindings?: EntityBinding[];
  is_seed?: boolean;
}

export interface EntityBinding {
  provider: string;
  binding_type: string;
  value: string;
  metadata?: Record<string, unknown>;
}

export interface EntitySearchResult {
  query: string;
  local: EntityItem[];
  candidates: Array<{
    provider: string;
    name: string;
    sport: Sport;
    kind: string;
    aliases: string[];
    bindings: EntityBinding[];
  }>;
}

export interface EventDetailsSection {
  title: string;
  columns: string[];
  rows: string[][];
}

export interface EventDetails {
  event_id: string;
  sport: Sport;
  source: string;
  summary: string;
  facts: Array<{ label: string; value: string }>;
  sections: EventDetailsSection[];
}

export interface AuthUser {
  id: number;
  email: string;
  email_verified: boolean;
  created_at: string;
}

export interface AuthResponse {
  ok: boolean;
  token: string;
  user: AuthUser;
}

export interface RegisterResponse {
  ok: boolean;
  user: AuthUser;
  email_delivery: { delivery: string; path: string | null };
  verification_url?: string;
}

export interface AccountSettings {
  f1_sessions: string[];
  hide_spoilers: boolean;
  ui_state: Record<string, unknown>;
}
