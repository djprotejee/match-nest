import { apiBaseUrl } from "./api";
import type { FollowLevel, MatchEvent } from "./types";

export type FeedMode = "main" | "starred" | "all";
export type ImportanceMode = "all" | "main" | "significant";
export type WatchStatus = "none" | "will_watch" | "watching_live" | "watched" | "skip";
export type NotifyPreset = "start" | "15m" | "1h" | "morning";

export interface AppState {
  follows: Record<string, FollowLevel>;
  watch: Record<string, WatchStatus>;
  revealed: Record<string, boolean>;
  manualPins: MatchEvent[];
  f1Sessions: Record<string, boolean>;
  notify: Record<NotifyPreset, boolean>;
  hideSpoilers: boolean;
  apiUrl: string;
}

export const STORAGE_KEY = "matchnest.state.v2";
export const CACHE_PREFIX = "matchnest.timeline.cache.v2.";
export const CALENDAR_CACHE_PREFIX = "matchnest.calendar.cache.v1.";

export const DEFAULT_STATE: AppState = {
  follows: {},
  watch: {},
  revealed: {},
  manualPins: [],
  f1Sessions: {
    race: true,
    qualifying: true,
    sprint: true,
    practice: false,
  },
  notify: {
    start: true,
    "15m": true,
    "1h": false,
    morning: false,
  },
  hideSpoilers: true,
  apiUrl: apiBaseUrl(),
};

export function loadAppState(): AppState {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (!stored) {
    return DEFAULT_STATE;
  }
  try {
    return { ...DEFAULT_STATE, ...JSON.parse(stored) };
  } catch {
    return DEFAULT_STATE;
  }
}
