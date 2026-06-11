import type { AuthResponse, AuthUser, DayGroup, EntityItem, EntitySearchResult, EventDetails, FollowLevel, RangeFilter, RegisterResponse, Sport } from "./types";

const DEFAULT_API_URL = import.meta.env.DEV ? `${window.location.origin}/api/` : `${window.location.origin}/`;
const AUTH_TOKEN_KEY = "matchnest.auth.token";

export function apiBaseUrl(): string {
  if (import.meta.env.DEV && !import.meta.env.VITE_API_URL) {
    return DEFAULT_API_URL;
  }

  const stored = localStorage.getItem("matchnest.apiUrl");

  if (!stored || isLocalhostUrl(stored)) {
    return import.meta.env.VITE_API_URL || DEFAULT_API_URL;
  }

  return stored;
}

export function saveApiBaseUrl(value: string): void {
  localStorage.setItem("matchnest.apiUrl", normalizeApiBaseUrl(value));
}

export async function fetchTimeline(range: RangeFilter, revealSpoilers: boolean): Promise<DayGroup[]> {
  const url = apiUrl("timeline");
  url.searchParams.set("range", range);
  url.searchParams.set("reveal_spoilers", revealSpoilers ? "true" : "false");
  return getJson<DayGroup[]>(url);
}

export async function fetchCalendar(year: number, month: number, revealSpoilers: boolean): Promise<DayGroup[]> {
  const url = apiUrl(`calendar/${year}/${month}`);
  url.searchParams.set("reveal_spoilers", revealSpoilers ? "true" : "false");
  return getJson<DayGroup[]>(url);
}

export async function fetchEntities(): Promise<EntityItem[]> {
  return getJson<EntityItem[]>(apiUrl("entities"));
}

export async function searchEntities(query: string): Promise<EntitySearchResult> {
  const url = apiUrl("entities/search");
  url.searchParams.set("q", query);
  return getJson<EntitySearchResult>(url);
}

export async function fetchEntityDetail(entityId: string): Promise<EntityItem> {
  return getJson<EntityItem>(apiUrl(`entities/${entityId}`));
}

export async function createCustomEntity(input: {
  name: string;
  sport: Sport;
  kind: string;
  level: FollowLevel;
  aliases: string[];
  bindings: Array<{ provider: string; binding_type: string; value: string }>;
}): Promise<EntityItem> {
  return postJson<EntityItem>("entities/custom", input);
}

export async function updateEntity(
  entityId: string,
  input: {
    name: string;
    sport: Sport;
    kind: string;
    color?: string;
    aliases: string[];
    bindings: Array<{ provider: string; binding_type: string; value: string }>;
  },
): Promise<EntityItem> {
  const response = await fetch(apiUrl(`entities/${entityId}`), {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `API request failed: ${response.status}`);
  }
  return response.json() as Promise<EntityItem>;
}

export async function deleteEntity(entityId: string): Promise<{ ok: boolean; result: string }> {
  const response = await fetch(apiUrl(`entities/${entityId}`), {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `API request failed: ${response.status}`);
  }
  return response.json() as Promise<{ ok: boolean; result: string }>;
}

export async function fetchEventDetails(eventId: string): Promise<EventDetails> {
  return getJson<EventDetails>(apiUrl(`events/${eventId}/details`));
}

export async function saveFollowLevel(entityId: string, level: FollowLevel): Promise<void> {
  const response = await fetch(apiUrl(`follows/${entityId}`), {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ level }),
  });
  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }
}

export function authToken(): string | null {
  return localStorage.getItem(AUTH_TOKEN_KEY);
}

export function saveAuthToken(token: string): void {
  localStorage.setItem(AUTH_TOKEN_KEY, token);
}

export function clearAuthToken(): void {
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

export async function registerAccount(email: string, password: string): Promise<RegisterResponse> {
  return postJson<RegisterResponse>("auth/register", { email, password });
}

export async function loginAccount(email: string, password: string): Promise<AuthResponse> {
  return postJson<AuthResponse>("auth/login", { email, password });
}

export async function verifyEmailToken(token: string): Promise<AuthResponse> {
  return postJson<AuthResponse>("auth/verify-email", { token });
}

export async function fetchCurrentUser(): Promise<AuthUser> {
  const response = await getJson<{ user: AuthUser }>(apiUrl("auth/me"));
  return response.user;
}

export async function logoutAccount(): Promise<void> {
  await postJson("auth/logout", {});
  clearAuthToken();
}

export async function fetchGoogleLoginUrl(): Promise<string> {
  const response = await getJson<{ url: string }>(apiUrl("auth/google/start"));
  return response.url;
}

export async function saveF1Sessions(sessions: string[]): Promise<void> {
  await fetch(apiUrl("settings/f1-sessions"), {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ sessions }),
  }).then((response) => {
    if (!response.ok) {
      throw new Error(`API request failed: ${response.status}`);
    }
  });
}

function apiUrl(path: string): URL {
  return new URL(path.replace(/^\//, ""), normalizeApiBaseUrl(apiBaseUrl()));
}

async function getJson<T>(url: URL): Promise<T> {
  const response = await fetch(url, { headers: authHeaders() });
  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(apiUrl(path), {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail || `API request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

function authHeaders(headers: Record<string, string> = {}): Record<string, string> {
  const token = authToken();
  return token ? { ...headers, Authorization: `Bearer ${token}` } : headers;
}

function isLocalhostUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.hostname === "localhost" || url.hostname === "127.0.0.1";
  } catch {
    return false;
  }
}

function normalizeApiBaseUrl(value: string): string {
  return value.endsWith("/") ? value : `${value}/`;
}
