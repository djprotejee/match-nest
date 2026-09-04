import type { DayGroup, EventStatus } from "./types";

export const CALENDAR_DEFAULTS_VERSION = 1;
export const CALENDAR_STATUSES: EventStatus[] = ["past", "live", "delayed", "upcoming", "tbd"];

export function restoredCalendarStatuses(uiState: Record<string, unknown>): Set<EventStatus> {
  // Older releases persisted the timeline's future-only defaults as calendar
  // filters, even after the global defaults migration had completed.
  if (uiState.calendarStatusDefaultsVersion !== CALENDAR_DEFAULTS_VERSION || !Array.isArray(uiState.calendarStatuses)) {
    return new Set(CALENDAR_STATUSES);
  }
  return new Set(uiState.calendarStatuses.filter((value): value is EventStatus => CALENDAR_STATUSES.includes(value as EventStatus)));
}

export function calendarLoadMessage(loading: boolean, refreshing: boolean, error: string | null, groups: DayGroup[]): string | null {
  if (error) return `Could not load this month: ${error}`;
  if (loading) return "Loading this month…";
  if (refreshing) return groups.length ? "Updating this month in the background…" : "Fetching matches for this month…";
  return null;
}

export function shiftedCalendarMonth(cursor: Date, offset: number): Date {
  // Starting from day 1 avoids Jan 31 + one month jumping into March.
  return new Date(cursor.getFullYear(), cursor.getMonth() + offset, 1);
}
