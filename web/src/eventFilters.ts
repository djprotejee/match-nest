import type { AppState, FeedMode, ImportanceMode } from "./appState";
import { localDateKey } from "./dateUtils";
import type { DayGroup, EntityItem, EventStatus, FollowLevel, MatchEvent, RangeFilter, Sport } from "./types";

const INACTIVE_LEVELS = new Set<FollowLevel>(["hidden", "muted", "explore"]);

export function followLevelsForFeedMode(feedMode: FeedMode, customFeedLevels: FollowLevel[] = []): FollowLevel[] {
  if (feedMode === "main") {
    return ["main"];
  }
  if (feedMode === "starred") {
    return ["main", "starred"];
  }
  return customFeedLevels.length ? customFeedLevels : ["main", "starred"];
}

export function mergeFollowOverrides(entities: EntityItem[], follows: Record<string, FollowLevel>): EntityItem[] {
  return entities.map((entity) => ({ ...entity, follow: follows[entity.id] || entity.follow }));
}

export function appendManualPins(groups: DayGroup[], pins: MatchEvent[], range: RangeFilter): DayGroup[] {
  const map = new Map(groups.map((group) => [group.date, [...group.events]]));
  const now = new Date();
  for (const pin of pins) {
    if (!isInRange(pin, range, now)) {
      continue;
    }
    const key = pin.starts_at ? localDateKey(new Date(pin.starts_at)) : "tbd";
    map.set(key, [...(map.get(key) || []), pin]);
  }
  return Array.from(map.entries())
    .map(([date, events]) => ({ date, events: sortEvents(events) }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

export function filterGroups(
  groups: DayGroup[],
  options: {
    sports: Set<Sport>;
    statuses: Set<EventStatus>;
    feedMode: FeedMode;
    customFeedLevels: FollowLevel[];
    importanceMode: ImportanceMode;
    state: AppState;
    entityMap: Map<string, EntityItem>;
  },
): DayGroup[] {
  return groups
    .map((group) => ({
      ...group,
      events: group.events
        .map((event) => applyLocalFollow(event, options.entityMap))
        .filter((event) => shouldShowEvent(event, options)),
    }))
    .filter((group) => group.events.length > 0);
}

export function findConflicts(events: MatchEvent[]): MatchEvent[][] {
  const timed = events
    .filter((event) => event.starts_at && event.status !== "past")
    .sort((a, b) => new Date(a.starts_at || 0).getTime() - new Date(b.starts_at || 0).getTime());
  const conflicts: MatchEvent[][] = [];
  for (let index = 0; index < timed.length - 1; index += 1) {
    const current = timed[index];
    const next = timed[index + 1];
    const currentTime = new Date(current.starts_at || 0).getTime();
    const nextTime = new Date(next.starts_at || 0).getTime();
    if (Math.abs(nextTime - currentTime) <= 2 * 60 * 60 * 1000 && current.sport !== next.sport) {
      conflicts.push([current, next]);
    }
  }
  return conflicts;
}

export function flattenGroups(groups: DayGroup[]): MatchEvent[] {
  return groups.flatMap((group) => group.events);
}

function shouldShowEvent(
  event: MatchEvent,
  options: {
    sports: Set<Sport>;
    statuses: Set<EventStatus>;
    feedMode: FeedMode;
    customFeedLevels: FollowLevel[];
    importanceMode: ImportanceMode;
    state: AppState;
  },
): boolean {
  if (!options.sports.has(event.sport) || !options.statuses.has(event.status)) {
    return false;
  }
  if (event.follow_level === "hidden") {
    return false;
  }
  if (!followLevelsForFeedMode(options.feedMode, options.customFeedLevels).includes(event.follow_level)) {
    return false;
  }
  if (options.importanceMode === "main" && event.follow_level !== "main") {
    return false;
  }
  if (options.importanceMode === "significant" && event.importance < 75) {
    return false;
  }
  if (event.sport === "formula" && event.session_type && options.state.f1Sessions[event.session_type] === false) {
    return false;
  }
  if (options.state.watch[event.id] === "skip") {
    return false;
  }
  return true;
}

function applyLocalFollow(event: MatchEvent, entityMap: Map<string, EntityItem>): MatchEvent {
  const levels = event.entity_ids.map((id) => entityMap.get(id)?.follow).filter(Boolean) as FollowLevel[];
  if (levels.includes("hidden")) {
    return { ...event, follow_level: "hidden" };
  }
  if (levels.includes("main")) {
    return { ...event, follow_level: "main" };
  }
  if (levels.includes("starred")) {
    return { ...event, follow_level: "starred" };
  }
  const customLevel = levels.find((level) => !INACTIVE_LEVELS.has(level));
  if (customLevel) {
    return { ...event, follow_level: customLevel };
  }
  if (levels.includes("muted")) {
    return { ...event, follow_level: "muted" };
  }
  return event;
}

function isInRange(event: MatchEvent, range: RangeFilter, now: Date): boolean {
  if (!event.starts_at) {
    return true;
  }
  const date = new Date(event.starts_at);
  if (range === "today") {
    return localDateKey(date) === localDateKey(now);
  }
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const end = new Date(start);
  end.setDate(end.getDate() + (range === "week" ? 7 : 35));
  return date >= start && date < end;
}

function sortEvents(events: MatchEvent[]): MatchEvent[] {
  return [...events].sort((a, b) => (a.starts_at || "").localeCompare(b.starts_at || ""));
}
