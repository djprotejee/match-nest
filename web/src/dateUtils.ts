import type { EventStatus } from "./types";
import { KYIV_TIME_ZONE } from "./config";

export function localDateKey(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function visibleCalendarDays(cursor: Date): Date[] {
  const start = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  const mondayBasedOffset = (start.getDay() + 6) % 7;
  start.setDate(start.getDate() - mondayBasedOffset);

  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    return date;
  });
}

export function statusFromDate(date: Date): EventStatus {
  return date.getTime() <= Date.now() ? "past" : "upcoming";
}

export function formatGroupDate(value: string): string {
  if (value === "tbd") {
    return "Time not confirmed";
  }
  return new Date(`${value}T12:00:00`).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: KYIV_TIME_ZONE,
  });
}

export function formatMonthTitle(date: Date): string {
  return date.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

export function formatEventTime(value: string | null): string {
  if (!value) {
    return "TBD";
  }
  return new Date(value).toLocaleTimeString(undefined, {
    hour: "2-digit",
    hour12: false,
    hourCycle: "h23",
    minute: "2-digit",
    timeZone: KYIV_TIME_ZONE,
  });
}

export function formatRelativeTime(timestamp: number): string {
  const minutes = Math.max(1, Math.round((Date.now() - timestamp) / 60000));
  if (minutes < 60) {
    return `${minutes} min ago`;
  }
  return `${Math.round(minutes / 60)} h ago`;
}
