import { apiBaseUrl } from "./api";
import type { FollowLevel, MatchEvent } from "./types";

export type FeedMode = "main" | "starred" | "custom";
export type ImportanceMode = "all" | "main" | "significant";
export type WatchStatus = "none" | "will_watch" | "watching_live" | "watched" | "skip";
export type NotifyPreset = "start" | "15m" | "1h" | "morning";

export interface FollowCategory {
  id: FollowLevel;
  name: string;
  color: string;
  system?: boolean;
}

export interface AppState {
  follows: Record<string, FollowLevel>;
  categories: FollowCategory[];
  customFeedLevels: FollowLevel[];
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
export const CALENDAR_CACHE_PREFIX = "matchnest.calendar.cache.v2.";

export const DEFAULT_CATEGORIES: FollowCategory[] = [
  { id: "main", name: "Main", color: "#ff8a3d", system: true },
  { id: "starred", name: "Starred", color: "#8b5cf6", system: true },
  { id: "muted", name: "Muted", color: "#64748b", system: true },
  { id: "hidden", name: "Hidden", color: "#475569", system: true },
  { id: "explore", name: "Explore", color: "#2ecc71", system: true },
];

export const DEFAULT_STATE: AppState = {
  follows: {},
  categories: DEFAULT_CATEGORIES,
  customFeedLevels: ["main", "starred"],
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
    const parsed = JSON.parse(stored) as Partial<AppState>;
    return {
      ...DEFAULT_STATE,
      ...parsed,
      categories: normalizeCategories(parsed.categories),
      customFeedLevels: Array.isArray(parsed.customFeedLevels) ? parsed.customFeedLevels : DEFAULT_STATE.customFeedLevels,
    };
  } catch {
    return DEFAULT_STATE;
  }
}

export function normalizeCategories(categories: FollowCategory[] | undefined): FollowCategory[] {
  const byId = new Map(DEFAULT_CATEGORIES.map((category) => [category.id, category]));
  for (const category of categories || []) {
    if (!category?.id || !category.name) {
      continue;
    }
    byId.set(category.id, { ...category, system: DEFAULT_CATEGORIES.some((item) => item.id === category.id) || category.system });
  }
  return Array.from(byId.values());
}
