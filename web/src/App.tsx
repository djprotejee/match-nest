import {
  CalendarDays,
  Eye,
  EyeOff,
  ListChecks,
  LogOut,
  Plus,
  RefreshCcw,
  Search,
  Settings,
  Star,
  User,
  WifiOff,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  authToken,
  clearAuthToken,
  createCustomEntity,
  deleteEntity,
  deleteNotificationRule,
  fetchAccountSettings,
  fetchEntityDetail,
  fetchCalendar,
  fetchCurrentUser,
  fetchEntities,
  fetchEvent,
  fetchEventDetails,
  fetchGoogleLoginUrl,
  fetchNotificationSettings,
  fetchTimeline,
  loginAccount,
  logoutAccount,
  registerAccount,
  saveApiBaseUrl,
  saveAccountSettings,
  saveAuthToken,
  saveF1Sessions,
  saveFollowLevel,
  saveNotificationRule,
  savePushSubscription,
  searchEntities,
  updateEntity,
  verifyEmailToken,
} from "./api";
import {
  CACHE_PREFIX,
  CALENDAR_CACHE_PREFIX,
  type AppState,
  type FeedMode,
  type FollowCategory,
  type ImportanceMode,
  type NotifyPreset,
  type SpoilerMode,
  STORAGE_KEY,
  type WatchStatus,
  loadAppState,
  normalizeCategories,
} from "./appState";
import { SPORTS, STATUSES } from "./config";
import {
  formatEventTime,
  formatGroupDate,
  formatMonthTitle,
  formatRelativeTime,
  localDateKey,
  statusFromDate,
  visibleCalendarDays,
} from "./dateUtils";
import { fallbackEntities, fallbackTimeline } from "./demoData";
import { appendManualPins, filterGroups, findConflicts, flattenGroups, followLevelsForFeedMode, mergeFollowOverrides } from "./eventFilters";
import type { AuthUser, DayGroup, EntityItem, EntitySearchResult, EventDetails, EventStatus, FollowLevel, MatchEvent, NotificationRule, NotificationSettings, RangeFilter, RegisterResponse, Sport } from "./types";

type Tab = "timeline" | "calendar" | "explore" | "settings";

const STATUS_DEFAULTS_VERSION = 3;
const DEFAULT_VISIBLE_STATUSES: EventStatus[] = ["live", "delayed", "upcoming"];

export function App() {
  const [tab, setTab] = useState<Tab>("timeline");
  const [range, setRange] = useState<RangeFilter>("week");
  const [feedMode, setFeedMode] = useState<FeedMode>("main");
  const [importanceMode, setImportanceMode] = useState<ImportanceMode>("all");
  const [timelineStatuses, setTimelineStatuses] = useState<Set<EventStatus>>(new Set(DEFAULT_VISIBLE_STATUSES));
  const [calendarStatuses, setCalendarStatuses] = useState<Set<EventStatus>>(new Set(DEFAULT_VISIBLE_STATUSES));
  const [sports, setSports] = useState<Set<Sport>>(new Set(SPORTS));
  const [state, setState] = useState<AppState>(loadAppState);
  const [monthCursor, setMonthCursor] = useState(() => new Date());
  const [timeline, setTimeline] = useState<DayGroup[]>(() => loadInitialTimelineCache(state)?.timeline || []);
  const [calendarGroups, setCalendarGroups] = useState<DayGroup[]>(() => loadInitialCalendarCache(state) || []);
  const [entities, setEntities] = useState<EntityItem[]>(() => loadInitialTimelineCache(state)?.entities || []);
  const [loading, setLoading] = useState(false);
  const [offline, setOffline] = useState(false);
  const [cacheNote, setCacheNote] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [authLoading, setAuthLoading] = useState(Boolean(authToken()));
  const [authError, setAuthError] = useState<string | null>(null);
  const [accountSettingsLoaded, setAccountSettingsLoaded] = useState(false);
  const timelineRetryRef = useRef<number | null>(null);
  const calendarRetryRef = useRef<number | null>(null);
  const timelineRetryCountsRef = useRef<Record<string, number>>({});
  const calendarRetryCountsRef = useRef<Record<string, number>>({});

  useEffect(() => {
    const callbackToken = new URLSearchParams(window.location.search).get("auth_token");
    if (callbackToken) {
      saveAuthToken(callbackToken);
      window.history.replaceState(null, "", window.location.pathname);
    }
    const token = authToken();
    if (!token) {
      setAuthLoading(false);
      return;
    }
    fetchCurrentUser()
      .then((user) => {
        setCurrentUser(user);
        setAuthError(null);
      })
      .catch((error) => {
        clearAuthToken();
        setCurrentUser(null);
        setAuthError(readErrorMessage(error));
      })
      .finally(() => setAuthLoading(false));
  }, []);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }, [state]);

  useEffect(() => {
    if (!currentUser) {
      setAccountSettingsLoaded(false);
      return;
    }
    setAccountSettingsLoaded(false);
    fetchAccountSettings()
      .then((settings) => {
        setState((current) => mergeAccountSettings(current, settings));
        syncViewSettings(settings.ui_state);
        setLastError(null);
      })
      .catch((error) => {
        setLastError(readErrorMessage(error));
      })
      .finally(() => setAccountSettingsLoaded(true));
  }, [currentUser?.id]);

  useEffect(() => {
    if (!currentUser || !accountSettingsLoaded) {
      return;
    }
    const timeout = window.setTimeout(() => {
      void saveAccountSettings(accountSettingsPayload(state, {
        range,
        feedMode,
        importanceMode,
        sports: Array.from(sports),
        timelineStatuses: Array.from(timelineStatuses),
        calendarStatuses: Array.from(calendarStatuses),
        statusDefaultsVersion: STATUS_DEFAULTS_VERSION,
      })).catch((error) => {
        setLastError(readErrorMessage(error));
      });
    }, 700);
    return () => window.clearTimeout(timeout);
  }, [state, range, feedMode, importanceMode, sports, timelineStatuses, calendarStatuses, currentUser, accountSettingsLoaded]);

  useEffect(() => {
    if (currentUser && accountSettingsLoaded) {
      void loadTimeline();
    }
  }, [range, feedMode, state.hideSpoilers, state.spoilerMode, state.spoilerSports, state.spoilerLevels, state.spoilerEntities, state.customFeedLevels, currentUser, accountSettingsLoaded]);

  useEffect(() => {
    if (currentUser && accountSettingsLoaded) {
      void loadCalendar();
    }
  }, [monthCursor, feedMode, state.hideSpoilers, state.spoilerMode, state.spoilerSports, state.spoilerLevels, state.spoilerEntities, state.customFeedLevels, currentUser, accountSettingsLoaded]);

  async function loadTimeline() {
    const cacheKey = timelineCacheKey(range, state, feedMode, state.customFeedLevels);
    const cached = loadCachedData(cacheKey);
    if (cached && timeline.length === 0) {
      setTimeline(cached.timeline);
      setEntities(cached.entities);
      setCacheNote(`Cached ${formatRelativeTime(cached.at)}`);
    }
    setLoading(true);
    try {
      const [nextTimeline, nextEntities] = await Promise.all([
        fetchTimeline(range, !state.hideSpoilers, followLevelsForFeedMode(feedMode, state.customFeedLevels)),
        fetchEntities(),
      ]);
      if (nextTimeline.length) {
        setTimeline(nextTimeline);
      } else if (!cached && timeline.length === 0) {
        setTimeline([]);
      }
      setEntities(nextEntities);
      syncFollowsFromEntities(nextEntities);
      setOffline(false);
      setCacheNote(nextTimeline.length ? null : cached ? `Cached ${formatRelativeTime(cached.at)}. Refreshing...` : "Refreshing events...");
      setLastError(null);
      if (nextTimeline.length) {
        localStorage.setItem(cacheKey, JSON.stringify({ at: Date.now(), timeline: nextTimeline, entities: nextEntities }));
        timelineRetryCountsRef.current[cacheKey] = 0;
      }
      if (!nextTimeline.length) {
        scheduleTimelineRetry();
      }
    } catch (error) {
      setLastError(readErrorMessage(error));
      if (cached) {
        setTimeline(cached.timeline);
        setEntities(cached.entities);
        setCacheNote(`Cached ${formatRelativeTime(cached.at)}`);
      } else if (timeline.length === 0) {
        setTimeline(fallbackTimeline);
        setEntities(fallbackEntities);
        setCacheNote("Local preview data");
      }
      setOffline(true);
    } finally {
      setLoading(false);
    }
  }

  async function loadCalendar() {
    const year = monthCursor.getFullYear();
    const month = monthCursor.getMonth() + 1;
    const levels = followLevelsForFeedMode(feedMode, state.customFeedLevels);
    const cacheKey = calendarCacheKey(year, month, levels);
    const cached = loadCachedCalendar(cacheKey);
    if (cached) {
      setCalendarGroups(cached);
    }
    try {
      const nextGroups = await fetchCalendar(year, month, !state.hideSpoilers, levels);
      if (nextGroups.length) {
        setCalendarGroups(nextGroups);
      } else if (!cached && calendarGroups.length === 0) {
        setCalendarGroups([]);
      }
      if (nextGroups.length) {
        localStorage.setItem(cacheKey, JSON.stringify({ at: Date.now(), groups: nextGroups }));
        calendarRetryCountsRef.current[cacheKey] = 0;
      }
      if (!nextGroups.length) {
        scheduleCalendarRetry();
      }
    } catch {
      if (!cached && calendarGroups.length === 0) {
        setCalendarGroups(fallbackTimeline);
      }
    }
  }

  async function loadEntitiesOnly() {
    try {
      const nextEntities = await fetchEntities();
      setEntities(nextEntities);
      syncFollowsFromEntities(nextEntities);
      setLastError(null);
    } catch (error) {
      setLastError(readErrorMessage(error));
    }
  }

  const allEntities = useMemo(() => mergeFollowOverrides(entities, state.follows), [entities, state.follows]);
  const entityMap = useMemo(() => new Map(allEntities.map((entity) => [entity.id, entity])), [allEntities]);
  const mergedTimeline = useMemo(
    () => appendManualPins(timeline, state.manualPins, range),
    [timeline, state.manualPins, range],
  );
  const mergedCalendar = useMemo(
    () => appendManualPins(calendarGroups, state.manualPins, "month"),
    [calendarGroups, state.manualPins],
  );
  const filteredTimeline = useMemo(
    () =>
      filterGroups(mergedTimeline, {
        sports,
        statuses: timelineStatuses,
        feedMode,
        customFeedLevels: state.customFeedLevels,
        importanceMode,
        state,
        entityMap,
      }),
    [mergedTimeline, sports, timelineStatuses, feedMode, state.customFeedLevels, importanceMode, state, entityMap],
  );
  const filteredCalendar = useMemo(
    () =>
      filterGroups(mergedCalendar, {
        sports,
        statuses: calendarStatuses,
        feedMode,
        customFeedLevels: state.customFeedLevels,
        importanceMode,
        state,
        entityMap,
      }),
    [mergedCalendar, sports, calendarStatuses, feedMode, state.customFeedLevels, importanceMode, state, entityMap],
  );
  const conflicts = useMemo(() => findConflicts(flattenGroups(filteredTimeline)), [filteredTimeline]);

  function updateState(patch: Partial<AppState>) {
    setState((current) => ({ ...current, ...patch }));
  }

  function syncViewSettings(uiState: Record<string, unknown>) {
    const shouldMigrateStatusDefaults = uiState.statusDefaultsVersion !== STATUS_DEFAULTS_VERSION;
    if (isRangeFilter(uiState.range)) {
      setRange(uiState.range);
    }
    const nextFeedMode = normalizeFeedMode(uiState.feedMode);
    if (nextFeedMode) {
      setFeedMode(nextFeedMode);
    }
    if (isImportanceMode(uiState.importanceMode)) {
      setImportanceMode(uiState.importanceMode);
    }
    if (Array.isArray(uiState.sports)) {
      setSports(new Set(uiState.sports.filter(isSport)));
    }
    if (Array.isArray(uiState.timelineStatuses)) {
      setTimelineStatuses(new Set(normalizeStoredStatuses(uiState.timelineStatuses, shouldMigrateStatusDefaults)));
    }
    if (Array.isArray(uiState.calendarStatuses)) {
      setCalendarStatuses(new Set(normalizeStoredStatuses(uiState.calendarStatuses, shouldMigrateStatusDefaults)));
    }
    if (Array.isArray(uiState.customFeedLevels)) {
      updateState({ customFeedLevels: uiState.customFeedLevels.filter((value): value is FollowLevel => typeof value === "string") });
    }
  }

  function syncFollowsFromEntities(nextEntities: EntityItem[]) {
    const follows = followsFromEntities(nextEntities);
    setState((current) => ({ ...current, follows }));
  }

  function scheduleTimelineRetry() {
    const retryKey = timelineCacheKey(range, state, feedMode, state.customFeedLevels);
    const count = timelineRetryCountsRef.current[retryKey] || 0;
    if (timelineRetryRef.current !== null || count >= 6) {
      return;
    }
    timelineRetryCountsRef.current[retryKey] = count + 1;
    timelineRetryRef.current = window.setTimeout(() => {
      timelineRetryRef.current = null;
      void loadTimeline();
    }, count < 2 ? 3500 : 8000);
  }

  function scheduleCalendarRetry() {
    const retryKey = calendarCacheKey(monthCursor.getFullYear(), monthCursor.getMonth() + 1, followLevelsForFeedMode(feedMode, state.customFeedLevels));
    const count = calendarRetryCountsRef.current[retryKey] || 0;
    if (calendarRetryRef.current !== null || count >= 6) {
      return;
    }
    calendarRetryCountsRef.current[retryKey] = count + 1;
    calendarRetryRef.current = window.setTimeout(() => {
      calendarRetryRef.current = null;
      void loadCalendar();
    }, count < 2 ? 3500 : 8000);
  }

  function setFollow(entityId: string, level: FollowLevel) {
    updateState({ follows: { ...state.follows, [entityId]: level } });
    void saveFollowLevel(entityId, level)
      .then(() => Promise.all([loadTimeline(), loadCalendar()]))
      .catch((error) => {
        setOffline(true);
        setLastError(readErrorMessage(error));
      });
  }

  function toggleCustomFeedLevel(level: FollowLevel) {
    const selected = new Set(state.customFeedLevels);
    if (selected.has(level)) {
      selected.delete(level);
    } else {
      selected.add(level);
    }
    updateState({ customFeedLevels: Array.from(selected) });
  }

  function renameCategory(categoryId: FollowLevel, name: string) {
    const trimmed = name.trim();
    if (!trimmed) {
      return;
    }
    updateState({
      categories: state.categories.map((category) => (category.id === categoryId ? { ...category, name: trimmed } : category)),
    });
  }

  function updateCategoryColor(categoryId: FollowLevel, color: string) {
    if (!/^#[0-9a-f]{6}$/i.test(color)) {
      return;
    }
    updateState({
      categories: state.categories.map((category) => (category.id === categoryId ? { ...category, color } : category)),
    });
  }

  function createCategory(name: string) {
    const trimmed = name.trim();
    if (!trimmed) {
      return;
    }
    const id = uniqueCategoryId(slugifyCategory(trimmed), state.categories);
    updateState({
      categories: [...state.categories, { id, name: trimmed, color: nextCategoryColor(state.categories.length) }],
      customFeedLevels: [...new Set([...state.customFeedLevels, id])],
    });
  }

  function deleteCategory(categoryId: FollowLevel) {
    const category = state.categories.find((item) => item.id === categoryId);
    if (!category || category.system) {
      return;
    }
    const follows = { ...state.follows };
    const affectedEntityIds = allEntities.filter((entity) => entity.follow === categoryId).map((entity) => entity.id);
    for (const entityId of affectedEntityIds) {
      follows[entityId] = "explore";
    }
    updateState({
      categories: state.categories.filter((item) => item.id !== categoryId),
      customFeedLevels: state.customFeedLevels.filter((level) => level !== categoryId),
      follows,
    });
    void Promise.all(affectedEntityIds.map((entityId) => saveFollowLevel(entityId, "explore"))).catch((error) => {
      setOffline(true);
      setLastError(readErrorMessage(error));
    });
  }

  function setF1Session(session: string, enabled: boolean) {
    const f1Sessions = { ...state.f1Sessions, [session]: enabled };
    updateState({ f1Sessions });
    void saveF1Sessions(Object.entries(f1Sessions).filter(([, value]) => value).map(([key]) => key))
      .then(() => Promise.all([loadTimeline(), loadCalendar()]))
      .catch((error) => {
        setOffline(true);
        setLastError(readErrorMessage(error));
      });
  }

  function setWatch(eventId: string, value: WatchStatus) {
    updateState({ watch: { ...state.watch, [eventId]: value } });
  }

  async function toggleReveal(eventId: string) {
    try {
      const revealedEvent = await fetchEvent(eventId, true);
      setTimeline((current) => replaceEventInGroups(current, revealedEvent));
      setCalendarGroups((current) => replaceEventInGroups(current, revealedEvent));
      updateState({ revealed: { ...state.revealed, [eventId]: true } });
      setLastError(null);
    } catch (error) {
      setLastError(readErrorMessage(error));
    }
  }

  function addManualPin(event: MatchEvent) {
    updateState({ manualPins: [...state.manualPins, event] });
  }

  function removeManualPin(eventId: string) {
    updateState({ manualPins: state.manualPins.filter((event) => event.id !== eventId) });
  }

  async function handleAuthSuccess(token: string, user: AuthUser) {
    saveAuthToken(token);
    setCurrentUser(user);
    setAuthError(null);
    setAccountSettingsLoaded(false);
    updateState({ follows: {} });
  }

  async function handleLogout() {
    await logoutAccount().catch(() => undefined);
    clearAuthToken();
    setCurrentUser(null);
    updateState({ follows: {}, revealed: {}, watch: {} });
  }

  if (authLoading) {
    return <div className="auth-shell"><div className="auth-card">Checking session...</div></div>;
  }

  if (!currentUser) {
    return (
      <AuthScreen
        authError={authError}
        onLogin={async (email, password) => {
          const response = await loginAccount(email, password);
          await handleAuthSuccess(response.token, response.user);
        }}
        onRegister={registerAccount}
        onVerify={async (token) => {
          const response = await verifyEmailToken(token);
          await handleAuthSuccess(response.token, response.user);
        }}
        onGoogleLogin={async () => {
          const url = await fetchGoogleLoginUrl();
          window.location.assign(url);
        }}
      />
    );
  }

  return (
    <div className="app-shell">
      <header className="top-bar">
        <div>
          <p className="eyebrow">Personal watch hub</p>
          <h1>MatchNest</h1>
        </div>
        <button className="icon-button" type="button" onClick={() => void loadTimeline()} aria-label="Refresh">
          <RefreshCcw size={20} />
        </button>
      </header>

      {offline ? (
        <div className="offline-banner">
          <WifiOff size={16} />
          <span>{lastError ? `API error: ${lastError}` : cacheNote || "Offline cache active"}</span>
          <button type="button" onClick={() => void resetLocalData()}>
            Reset local data
          </button>
        </div>
      ) : null}

      {tab === "timeline" ? (
        <TimelineScreen
          groups={filteredTimeline}
          range={range}
          setRange={setRange}
          feedMode={feedMode}
          setFeedMode={setFeedMode}
          categories={state.categories}
          customFeedLevels={state.customFeedLevels}
          toggleCustomFeedLevel={toggleCustomFeedLevel}
          importanceMode={importanceMode}
          setImportanceMode={setImportanceMode}
          statuses={timelineStatuses}
          toggleStatus={(status) => setTimelineStatuses(toggleSet(timelineStatuses, status))}
          sports={sports}
          toggleSport={(sport) => setSports(toggleSet(sports, sport))}
          hideSpoilers={state.hideSpoilers}
          setHideSpoilers={(value) => updateState({ hideSpoilers: value })}
          loading={loading}
          conflicts={conflicts}
          watch={state.watch}
          revealed={state.revealed}
          onWatch={setWatch}
          onReveal={toggleReveal}
          onRemoveManual={removeManualPin}
        />
      ) : null}

      {tab === "calendar" ? (
        <CalendarScreen
          groups={filteredCalendar}
          monthCursor={monthCursor}
          setMonthCursor={setMonthCursor}
          feedMode={feedMode}
          setFeedMode={setFeedMode}
          categories={state.categories}
          customFeedLevels={state.customFeedLevels}
          toggleCustomFeedLevel={toggleCustomFeedLevel}
          importanceMode={importanceMode}
          setImportanceMode={setImportanceMode}
          statuses={calendarStatuses}
          toggleStatus={(status) => setCalendarStatuses(toggleSet(calendarStatuses, status))}
          sports={sports}
          toggleSport={(sport) => setSports(toggleSet(sports, sport))}
          hideSpoilers={state.hideSpoilers}
          setHideSpoilers={(value) => updateState({ hideSpoilers: value })}
          watch={state.watch}
          revealed={state.revealed}
          onWatch={setWatch}
          onReveal={toggleReveal}
          onRemoveManual={removeManualPin}
        />
      ) : null}

      {tab === "explore" ? (
        <ExploreScreen
          entities={allEntities}
          categories={state.categories}
          setFollow={setFollow}
          refreshEntities={loadEntitiesOnly}
          renameCategory={renameCategory}
          updateCategoryColor={updateCategoryColor}
          createCategory={createCategory}
          deleteCategory={deleteCategory}
        />
      ) : null}

      {tab === "settings" ? (
        <SettingsScreen
          state={state}
          setState={updateState}
          entities={allEntities}
          categories={state.categories}
          currentUser={currentUser}
          onLogout={handleLogout}
          setF1Session={setF1Session}
          addManualPin={addManualPin}
        />
      ) : null}

      <nav className="bottom-nav" aria-label="Primary navigation">
        <NavButton icon={<ListChecks size={20} />} label="Timeline" active={tab === "timeline"} onClick={() => setTab("timeline")} />
        <NavButton icon={<CalendarDays size={20} />} label="Calendar" active={tab === "calendar"} onClick={() => setTab("calendar")} />
        <NavButton icon={<Search size={20} />} label="Explore" active={tab === "explore"} onClick={() => setTab("explore")} />
        <NavButton icon={<Settings size={20} />} label="Settings" active={tab === "settings"} onClick={() => setTab("settings")} />
      </nav>
    </div>
  );
}

function AuthScreen(props: {
  authError: string | null;
  onLogin: (email: string, password: string) => Promise<void>;
  onRegister: (email: string, password: string) => Promise<RegisterResponse>;
  onVerify: (token: string) => Promise<void>;
  onGoogleLogin: () => Promise<void>;
}) {
  const [mode, setMode] = useState<"login" | "register" | "verify">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [verificationToken, setVerificationToken] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(props.authError);

  useEffect(() => {
    setError(props.authError);
  }, [props.authError]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") {
        await props.onLogin(email, password);
      } else if (mode === "register") {
        const response = await props.onRegister(email, password);
        const token = response.verification_url ? new URL(response.verification_url).searchParams.get("token") : null;
        setVerificationToken(token || "");
        setMessage(
          response.verification_url
            ? "Dev mode: verification link was generated below. Verify once, then the app will log you in."
            : "Check your email for the verification link.",
        );
        setMode("verify");
      } else {
        await props.onVerify(verificationToken.trim());
      }
    } catch (nextError) {
      setError(readErrorMessage(nextError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <p className="eyebrow">Personal watch hub</p>
        <h1>MatchNest</h1>
        <p className="auth-copy">Sign in to sync teams, tournaments, filters and settings across your devices.</p>

        <div className="auth-tabs">
          <button className={mode === "login" ? "is-active" : ""} type="button" onClick={() => setMode("login")}>
            Login
          </button>
          <button className={mode === "register" ? "is-active" : ""} type="button" onClick={() => setMode("register")}>
            Register
          </button>
          <button className={mode === "verify" ? "is-active" : ""} type="button" onClick={() => setMode("verify")}>
            Verify
          </button>
        </div>

        {mode !== "verify" ? (
          <>
            <input value={email} onChange={(event) => setEmail(event.target.value)} placeholder="Email" autoComplete="email" />
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Password"
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
          </>
        ) : (
          <input
            value={verificationToken}
            onChange={(event) => setVerificationToken(event.target.value)}
            placeholder="Email verification token"
            autoComplete="one-time-code"
          />
        )}

        {message ? <div className="auth-note">{message}</div> : null}
        {error ? <div className="auth-error">{error}</div> : null}

        <button className="primary-action" type="button" onClick={() => void submit()} disabled={busy}>
          {busy ? "Working..." : mode === "login" ? "Login" : mode === "register" ? "Create account" : "Verify email"}
        </button>
        <button
          className="secondary-action"
          type="button"
          onClick={() => {
            setBusy(true);
            setError(null);
            props.onGoogleLogin().catch((nextError) => {
              setError(readErrorMessage(nextError));
              setBusy(false);
            });
          }}
          disabled={busy}
        >
          Continue with Google
        </button>
      </section>
    </main>
  );
}

function TimelineScreen(props: {
  groups: DayGroup[];
  range: RangeFilter;
  setRange: (range: RangeFilter) => void;
  feedMode: FeedMode;
  setFeedMode: (mode: FeedMode) => void;
  categories: FollowCategory[];
  customFeedLevels: FollowLevel[];
  toggleCustomFeedLevel: (level: FollowLevel) => void;
  importanceMode: ImportanceMode;
  setImportanceMode: (mode: ImportanceMode) => void;
  statuses: Set<EventStatus>;
  toggleStatus: (status: EventStatus) => void;
  sports: Set<Sport>;
  toggleSport: (sport: Sport) => void;
  hideSpoilers: boolean;
  setHideSpoilers: (value: boolean) => void;
  loading: boolean;
  conflicts: MatchEvent[][];
  watch: Record<string, WatchStatus>;
  revealed: Record<string, boolean>;
  onWatch: (eventId: string, value: WatchStatus) => void;
  onReveal: (eventId: string) => void;
  onRemoveManual: (eventId: string) => void;
}) {
  return (
    <main className="screen">
      <section className="controls">
        <SegmentedControl
          value={props.range}
          options={[
            ["today", "Today"],
            ["week", "7 days"],
            ["month", "Month"],
          ]}
          onChange={(value) => props.setRange(value as RangeFilter)}
        />
        <SegmentedControl
          value={props.feedMode}
          options={[
            ["main", "Main"],
            ["starred", "Starred"],
            ["custom", "Custom"],
          ]}
          onChange={(value) => props.setFeedMode(value as FeedMode)}
        />
        {props.feedMode === "custom" ? (
          <CategoryChecklist categories={props.categories} selected={props.customFeedLevels} onToggle={props.toggleCustomFeedLevel} />
        ) : null}
        <SegmentedControl
          value={props.importanceMode}
          options={[
            ["all", "All"],
            ["main", "Main only"],
            ["significant", "Big only"],
          ]}
          onChange={(value) => props.setImportanceMode(value as ImportanceMode)}
        />

        <button className="spoiler-toggle" type="button" onClick={() => props.setHideSpoilers(!props.hideSpoilers)}>
          {props.hideSpoilers ? <EyeOff size={18} /> : <Eye size={18} />}
          {props.hideSpoilers ? "Hide spoilers" : "Scores visible"}
        </button>

        <div className="sport-row">
          {SPORTS.map((sport) => (
            <button
              className={`sport-chip sport-${sport} ${props.sports.has(sport) ? "is-active" : ""}`}
              key={sport}
              type="button"
              onClick={() => props.toggleSport(sport)}
            >
              {sportLabel(sport)}
            </button>
          ))}
        </div>

        <div className="status-row">
          {STATUSES.map((status) => (
            <button
              className={`mini-chip ${props.statuses.has(status) ? "is-active" : ""}`}
              key={status}
              type="button"
              onClick={() => props.toggleStatus(status)}
            >
              {status}
            </button>
          ))}
        </div>
      </section>

      {props.conflicts.length ? <ConflictPanel conflicts={props.conflicts} /> : null}

      {props.loading && props.groups.length ? <div className="refresh-note">Refreshing in background...</div> : null}

      {props.loading && !props.groups.length ? (
        <div className="loading-card">Refreshing events...</div>
      ) : (
        <EventGroups
          groups={props.groups}
          categories={props.categories}
          watch={props.watch}
          revealed={props.revealed}
          onWatch={props.onWatch}
          onReveal={props.onReveal}
          onRemoveManual={props.onRemoveManual}
        />
      )}
    </main>
  );
}

function CalendarScreen(props: {
  groups: DayGroup[];
  monthCursor: Date;
  setMonthCursor: (date: Date) => void;
  feedMode: FeedMode;
  setFeedMode: (mode: FeedMode) => void;
  categories: FollowCategory[];
  customFeedLevels: FollowLevel[];
  toggleCustomFeedLevel: (level: FollowLevel) => void;
  importanceMode: ImportanceMode;
  setImportanceMode: (mode: ImportanceMode) => void;
  statuses: Set<EventStatus>;
  toggleStatus: (status: EventStatus) => void;
  sports: Set<Sport>;
  toggleSport: (sport: Sport) => void;
  hideSpoilers: boolean;
  setHideSpoilers: (value: boolean) => void;
  watch: Record<string, WatchStatus>;
  revealed: Record<string, boolean>;
  onWatch: (eventId: string, value: WatchStatus) => void;
  onReveal: (eventId: string) => void;
  onRemoveManual: (eventId: string) => void;
}) {
  const days = visibleCalendarDays(props.monthCursor);
  const eventsByDate = new Map(props.groups.map((group) => [group.date, group.events]));
  const todayKey = localDateKey(new Date());
  const [selectedStart, setSelectedStart] = useState<string | null>(() => defaultCalendarStart(props.monthCursor, props.statuses));
  const [selectedEnd, setSelectedEnd] = useState<string | null>(() => defaultCalendarEnd(props.monthCursor, props.statuses));
  const selectedGroups = filterGroupsByDateRange(props.groups, selectedStart, selectedEnd);
  const selectedEventCount = selectedGroups.reduce((total, group) => total + group.events.length, 0);

  useEffect(() => {
    setSelectedStart(defaultCalendarStart(props.monthCursor, props.statuses));
    setSelectedEnd(defaultCalendarEnd(props.monthCursor, props.statuses));
  }, [props.monthCursor, props.statuses]);

  function shiftMonth(offset: number) {
    const next = new Date(props.monthCursor);
    next.setMonth(next.getMonth() + offset);
    props.setMonthCursor(next);
    setSelectedStart(defaultCalendarStart(next, props.statuses));
    setSelectedEnd(defaultCalendarEnd(next, props.statuses));
  }

  function selectDate(dateKey: string) {
    if (selectedStart === dateKey && selectedEnd === null) {
      setSelectedStart(null);
      return;
    }
    if (!selectedStart || selectedEnd) {
      setSelectedStart(dateKey);
      setSelectedEnd(null);
      return;
    }
    const [start, end] = sortDateKeys(selectedStart, dateKey);
    setSelectedStart(start);
    setSelectedEnd(end === start ? null : end);
  }

  function clearSelection() {
    setSelectedStart(null);
    setSelectedEnd(null);
  }

  return (
    <main className="screen">
      <section className="controls">
        <SegmentedControl
          value={props.feedMode}
          options={[
            ["main", "Main"],
            ["starred", "Starred"],
            ["custom", "Custom"],
          ]}
          onChange={(value) => props.setFeedMode(value as FeedMode)}
        />
        {props.feedMode === "custom" ? (
          <CategoryChecklist categories={props.categories} selected={props.customFeedLevels} onToggle={props.toggleCustomFeedLevel} />
        ) : null}
        <SegmentedControl
          value={props.importanceMode}
          options={[
            ["all", "All"],
            ["main", "Main only"],
            ["significant", "Big only"],
          ]}
          onChange={(value) => props.setImportanceMode(value as ImportanceMode)}
        />

        <button className="spoiler-toggle" type="button" onClick={() => props.setHideSpoilers(!props.hideSpoilers)}>
          {props.hideSpoilers ? <EyeOff size={18} /> : <Eye size={18} />}
          {props.hideSpoilers ? "Hide spoilers" : "Scores visible"}
        </button>

        <div className="sport-row">
          {SPORTS.map((sport) => (
            <button
              className={`sport-chip sport-${sport} ${props.sports.has(sport) ? "is-active" : ""}`}
              key={sport}
              type="button"
              onClick={() => props.toggleSport(sport)}
            >
              {sportLabel(sport)}
            </button>
          ))}
        </div>

        <div className="status-row">
          {STATUSES.map((status) => (
            <button
              className={`mini-chip ${props.statuses.has(status) ? "is-active" : ""}`}
              key={status}
              type="button"
              onClick={() => props.toggleStatus(status)}
            >
              {status}
            </button>
          ))}
        </div>
      </section>

      <section className="month-header">
        <button className="icon-button" type="button" onClick={() => shiftMonth(-1)} aria-label="Previous month">
          {"<"}
        </button>
        <h2>{formatMonthTitle(props.monthCursor)}</h2>
        <button className="icon-button" type="button" onClick={() => shiftMonth(1)} aria-label="Next month">
          {">"}
        </button>
      </section>

      <section className="calendar-grid" aria-label="Month calendar">
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => (
          <span className="weekday" key={day}>
            {day}
          </span>
        ))}
        {days.map((day) => {
          const key = localDateKey(day);
          const events = eventsByDate.get(key) || [];
          const dimmed = day.getMonth() !== props.monthCursor.getMonth();
          return (
            <CalendarDay
              key={key}
              date={day}
              events={events}
              dimmed={dimmed}
              today={key === todayKey}
              selected={isDateSelected(key, selectedStart, selectedEnd)}
              onSelect={() => selectDate(key)}
            />
          );
        })}
      </section>

      <section className="day-report">
        <div>
          <p className="eyebrow">{selectedStart ? "Selection report" : "Month report"}</p>
          <h2>{formatSelectionTitle(selectedStart, selectedEnd, props.monthCursor)}</h2>
        </div>
        <span>{selectedEventCount} events</span>
        {selectedStart ? (
          <button className="text-action" type="button" onClick={clearSelection}>
            Clear
          </button>
        ) : null}
      </section>

      <EventGroups
        groups={selectedGroups}
        categories={props.categories}
        watch={props.watch}
        revealed={props.revealed}
        onWatch={props.onWatch}
        onReveal={props.onReveal}
        onRemoveManual={props.onRemoveManual}
      />
    </main>
  );
}

function ExploreScreen({
  entities,
  categories,
  setFollow,
  refreshEntities,
  renameCategory,
  updateCategoryColor,
  createCategory,
  deleteCategory,
}: {
  entities: EntityItem[];
  categories: FollowCategory[];
  setFollow: (entityId: string, level: FollowLevel) => void;
  refreshEntities: () => Promise<void>;
  renameCategory: (categoryId: FollowLevel, name: string) => void;
  updateCategoryColor: (categoryId: FollowLevel, color: string) => void;
  createCategory: (name: string) => void;
  deleteCategory: (categoryId: FollowLevel) => void;
}) {
  const [query, setQuery] = useState("");
  const [sportFilter, setSportFilter] = useState<Sport | "all">("all");
  const [kindFilter, setKindFilter] = useState<string>("all");
  const [levelFilter, setLevelFilter] = useState<FollowLevel | "all">("all");
  const [searchResult, setSearchResult] = useState<EntitySearchResult | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [selectedEntity, setSelectedEntity] = useState<EntityItem | null>(null);
  const [newName, setNewName] = useState("");
  const [newSport, setNewSport] = useState<Sport>("football");
  const [newKind, setNewKind] = useState("team");
  const [newLevel, setNewLevel] = useState<FollowLevel>("starred");
  const [newCategoryName, setNewCategoryName] = useState("");
  const [newAliases, setNewAliases] = useState("");
  const [newBindings, setNewBindings] = useState<Array<{ provider: string; binding_type: string; value: string }>>([]);
  const [busy, setBusy] = useState(false);
  const filteredEntities = useMemo(
    () =>
      entities.filter((entity) => {
        const text = `${entity.name} ${sportLabel(entity.sport)} ${entity.kind} ${entity.follow}`.toLowerCase();
        if (query.trim() && !text.includes(query.trim().toLowerCase())) {
          return false;
        }
        if (sportFilter !== "all" && entity.sport !== sportFilter) {
          return false;
        }
        if (kindFilter !== "all" && entity.kind !== kindFilter) {
          return false;
        }
        if (levelFilter !== "all" && entity.follow !== levelFilter) {
          return false;
        }
        return true;
      }),
    [entities, query, sportFilter, kindFilter, levelFilter],
  );
  const groups = groupEntities(filteredEntities);
  const levels = followLevels(categories);
  const stats = levels.map((level) => ({
    level,
    category: categoryForLevel(categories, level),
    count: entities.filter((entity) => entity.follow === level).length,
  }));

  async function runSearch() {
    if (!query.trim()) {
      setSearchResult(null);
      return;
    }
    setBusy(true);
    setSearchError(null);
    try {
      setSearchResult(await searchEntities(query.trim()));
    } catch (error) {
      setSearchError(readErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function addCustom() {
    if (!newName.trim()) {
      return;
    }
    setBusy(true);
    setSearchError(null);
    try {
      const created = await createCustomEntity({
        name: newName.trim(),
        sport: newSport,
        kind: newKind,
        level: newLevel,
        aliases: splitCsv(newAliases),
        bindings: newBindings,
      });
      setNewName("");
      setNewAliases("");
      setNewBindings([]);
      await refreshEntities();
      setSelectedEntity(created);
    } catch (error) {
      setSearchError(readErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function openDetails(entity: EntityItem) {
    setSelectedEntity(entity);
    try {
      setSelectedEntity(await fetchEntityDetail(entity.id));
    } catch {
      setSelectedEntity(entity);
    }
  }

  return (
    <main className="screen">
      <section className="section-title explore-title">
        <div>
          <h2>Explore</h2>
          <p>Search and organize teams, tournaments, drivers and players.</p>
        </div>
        <div className="explore-stats">
          {stats.map((item) => (
            <button
              className={`level-pill level-${item.level} ${levelFilter === item.level ? "is-active" : ""}`}
              key={item.level}
              type="button"
              onClick={() => setLevelFilter(levelFilter === item.level ? "all" : item.level)}
            >
              {item.category.name}
              <strong>{item.count}</strong>
            </button>
          ))}
        </div>
      </section>

      <section className="explore-tools">
        <label className="search-field">
          <Search size={18} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search teams, tournaments, players" />
        </label>
        <div className="filter-grid">
          <select value={sportFilter} onChange={(event) => setSportFilter(event.target.value as Sport | "all")}>
            <option value="all">All sports</option>
            {SPORTS.map((sport) => (
              <option key={sport} value={sport}>
                {sportLabel(sport)}
              </option>
            ))}
          </select>
          <select value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}>
            <option value="all">All types</option>
            {entityKinds(entities).map((kind) => (
              <option key={kind} value={kind}>
                {kindLabel(kind)}
              </option>
            ))}
          </select>
          <select value={levelFilter} onChange={(event) => setLevelFilter(event.target.value as FollowLevel | "all")}>
            <option value="all">All levels</option>
            {levels.map((level) => (
              <option key={level} value={level}>
                {categoryForLevel(categories, level).name}
              </option>
            ))}
          </select>
        </div>
        <button className="primary-action" type="button" onClick={() => void runSearch()} disabled={busy}>
          Search
        </button>
      </section>

      {searchError ? <div className="auth-error">{searchError}</div> : null}

      {searchResult ? (
        <section className="entity-category">
          <div className="entity-category-header">
            <h3>Search results</h3>
            <span>{searchResult.local.length + searchResult.candidates.length}</span>
          </div>
          <div className="entity-card-grid">
            {searchResult.local.map((entity) => (
              <EntityCard entity={entity} categories={categories} key={entity.id} setFollow={setFollow} onDetails={openDetails} />
            ))}
            {searchResult.candidates.map((candidate, index) => (
              <article className="entity-card" key={`${candidate.provider}-${candidate.name}-${index}`}>
                <span className="entity-dot" style={{ backgroundColor: defaultSportColor(candidate.sport) }} />
                <div className="entity-card-main">
                  <strong>{candidate.name}</strong>
                  <span>
                    {candidate.provider} - {kindLabel(candidate.kind)}
                  </span>
                </div>
                <button
                  className="text-action"
                  type="button"
                  onClick={() => {
                    setNewName(candidate.name);
                    setNewSport(candidate.sport);
                    setNewKind(candidate.kind);
                    setNewAliases(candidate.aliases.join(", "));
                    setNewBindings(candidate.bindings.map((binding) => ({ provider: binding.provider, binding_type: binding.binding_type, value: binding.value })));
                  }}
                >
                  Use
                </button>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      <section className="settings-card">
        <h3>Add custom</h3>
        <input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="Team, tournament, driver or player" />
        <div className="filter-grid">
          <select value={newSport} onChange={(event) => setNewSport(event.target.value as Sport)}>
            {SPORTS.map((sport) => (
              <option key={sport} value={sport}>
                {sportLabel(sport)}
              </option>
            ))}
          </select>
          <select value={newKind} onChange={(event) => setNewKind(event.target.value)}>
            <option value="team">Team</option>
            <option value="competition">Tournament</option>
            <option value="player">Player</option>
          </select>
          <FollowSelect categories={categories} value={newLevel} onChange={setNewLevel} />
        </div>
        <input value={newAliases} onChange={(event) => setNewAliases(event.target.value)} placeholder="Aliases, comma separated" />
        {newBindings.length ? (
          <div className="binding-preview">
            {newBindings.map((binding) => (
              <span key={`${binding.provider}-${binding.binding_type}-${binding.value}`}>
                {binding.provider}:{binding.binding_type}={binding.value}
              </span>
            ))}
          </div>
        ) : null}
        <button className="primary-action" type="button" onClick={() => void addCustom()} disabled={busy}>
          <Plus size={16} />
          Add to hub
        </button>
      </section>

      <section className="settings-card category-manager">
        <h3>Categories</h3>
        <div className="category-editor-list">
          {categories.map((category) => (
            <div className="category-editor-row" key={category.id}>
              <span className="entity-dot" style={{ backgroundColor: category.color }} />
              <input value={category.name} onChange={(event) => renameCategory(category.id, event.target.value)} aria-label={`${category.name} category name`} />
              <label className="color-field" aria-label={`${category.name} category color`}>
                <span style={{ backgroundColor: category.color }} />
                <input type="color" value={category.color} onChange={(event) => updateCategoryColor(category.id, event.target.value)} />
              </label>
              {category.system ? (
                <span className="category-lock">System</span>
              ) : (
                <button className="text-action danger" type="button" onClick={() => deleteCategory(category.id)}>
                  Delete
                </button>
              )}
            </div>
          ))}
        </div>
        <div className="category-create-row">
          <input value={newCategoryName} onChange={(event) => setNewCategoryName(event.target.value)} placeholder="New category name" />
          <button
            className="secondary-action"
            type="button"
            onClick={() => {
              createCategory(newCategoryName);
              setNewCategoryName("");
            }}
          >
            Add category
          </button>
        </div>
      </section>

      <div className="entity-hub">
        {levels.map((level) => {
          const items = groups.get(level) || [];
          const category = categoryForLevel(categories, level);
          return (
            <section className="entity-category" key={level}>
              <div className="entity-category-header">
                <h3>{category.name}</h3>
                <span>{items.length}</span>
              </div>
              {items.length ? (
                <div className="entity-card-grid">
                  {items.map((entity) => (
                    <EntityCard entity={entity} categories={categories} key={entity.id} setFollow={setFollow} onDetails={openDetails} />
                  ))}
                </div>
              ) : (
                <div className="empty-state compact">No items here.</div>
              )}
            </section>
          );
        })}
      </div>

      {selectedEntity ? (
        <EntityDrawer
          entity={selectedEntity}
          categories={categories}
          onClose={() => setSelectedEntity(null)}
          setFollow={setFollow}
          refreshEntities={refreshEntities}
          setSelectedEntity={setSelectedEntity}
        />
      ) : null}
    </main>
  );
}

function EntityCard({
  entity,
  categories,
  setFollow,
  onDetails,
}: {
  entity: EntityItem;
  categories: FollowCategory[];
  setFollow: (entityId: string, level: FollowLevel) => void;
  onDetails: (entity: EntityItem) => void;
}) {
  return (
    <article className="entity-card">
      <span className="entity-dot" style={{ backgroundColor: entity.color }} />
      <button className="entity-card-main entity-detail-button" type="button" onClick={() => onDetails(entity)}>
        <strong>{entity.name}</strong>
        <span>
          {sportLabel(entity.sport)} - {kindLabel(entity.kind)}
        </span>
      </button>
      <FollowSelect categories={categories} value={entity.follow} onChange={(level) => setFollow(entity.id, level)} />
    </article>
  );
}

function EntityDrawer({
  entity,
  categories,
  onClose,
  setFollow,
  refreshEntities,
  setSelectedEntity,
}: {
  entity: EntityItem;
  categories: FollowCategory[];
  onClose: () => void;
  setFollow: (entityId: string, level: FollowLevel) => void;
  refreshEntities: () => Promise<void>;
  setSelectedEntity: (entity: EntityItem | null) => void;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [name, setName] = useState(entity.name);
  const [sport, setSport] = useState<Sport>(entity.sport);
  const [kind, setKind] = useState(entity.kind);
  const [aliases, setAliases] = useState((entity.aliases || []).join(", "));
  const [bindings, setBindings] = useState(bindingsToText(entity.bindings || []));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canEdit = entity.is_seed === false;

  async function saveChanges() {
    setBusy(true);
    setError(null);
    try {
      const updated = await updateEntity(entity.id, {
        name,
        sport,
        kind,
        aliases: splitCsv(aliases),
        bindings: parseBindingsText(bindings),
      });
      await refreshEntities();
      setSelectedEntity(updated);
      setIsEditing(false);
    } catch (nextError) {
      setError(readErrorMessage(nextError));
    } finally {
      setBusy(false);
    }
  }

  async function removeEntity() {
    setBusy(true);
    setError(null);
    try {
      await deleteEntity(entity.id);
      await refreshEntities();
      onClose();
    } catch (nextError) {
      setError(readErrorMessage(nextError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="entity-drawer">
      <div className="entity-drawer-head">
        <div>
          <p className="eyebrow">{sportLabel(entity.sport)} - {kindLabel(entity.kind)}</p>
          <h2>{entity.name}</h2>
        </div>
        <div className="drawer-actions">
          {canEdit ? (
            <button className="text-action" type="button" onClick={() => setIsEditing(!isEditing)}>
              {isEditing ? "Cancel" : "Edit"}
            </button>
          ) : null}
          <button className="text-action" type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      <FollowSelect categories={categories} value={entity.follow} onChange={(level) => setFollow(entity.id, level)} />
      {isEditing ? (
        <div className="drawer-form">
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Name" />
          <div className="filter-grid">
            <select value={sport} onChange={(event) => setSport(event.target.value as Sport)}>
              {SPORTS.map((item) => (
                <option key={item} value={item}>
                  {sportLabel(item)}
                </option>
              ))}
            </select>
            <select value={kind} onChange={(event) => setKind(event.target.value)}>
              <option value="team">Team</option>
              <option value="competition">Tournament</option>
              <option value="player">Player</option>
            </select>
          </div>
          <input value={aliases} onChange={(event) => setAliases(event.target.value)} placeholder="Aliases, comma separated" />
          <textarea value={bindings} onChange={(event) => setBindings(event.target.value)} placeholder="provider:type=value, one per line" />
          {error ? <div className="auth-error">{error}</div> : null}
          <button className="primary-action" type="button" onClick={() => void saveChanges()} disabled={busy}>
            Save entity
          </button>
        </div>
      ) : (
        <div className="drawer-grid">
          <span>Source</span>
          <strong>{entity.is_seed ? "Seed + SQLite" : "Custom SQLite"}</strong>
          <span>Aliases</span>
          <strong>{entity.aliases?.length ? entity.aliases.join(", ") : "-"}</strong>
          <span>Bindings</span>
          <strong>
            {entity.bindings?.length
              ? entity.bindings.map((binding) => `${binding.provider}:${binding.binding_type}=${binding.value}`).join(", ")
              : "-"}
          </strong>
        </div>
      )}
      <button className="text-action danger" type="button" onClick={() => void removeEntity()} disabled={busy}>
        {entity.is_seed ? "Hide from my hub" : "Delete entity"}
      </button>
    </section>
  );
}

function SettingsScreen(props: {
  state: AppState;
  setState: (patch: Partial<AppState>) => void;
  entities: EntityItem[];
  categories: FollowCategory[];
  currentUser: AuthUser;
  onLogout: () => Promise<void>;
  setF1Session: (session: string, enabled: boolean) => void;
  addManualPin: (event: MatchEvent) => void;
}) {
  return (
    <main className="screen">
      <section className="settings-card account-card">
        <div>
          <h3>Account</h3>
          <p>
            <User size={16} />
            {props.currentUser.email}
          </p>
        </div>
        <button className="text-action" type="button" onClick={() => void props.onLogout()}>
          <LogOut size={16} />
          Logout
        </button>
      </section>

      <section className="settings-card">
        <label htmlFor="api-url">Backend URL</label>
        <input
          id="api-url"
          value={props.state.apiUrl}
          onChange={(event) => {
            const value = event.target.value;
            props.setState({ apiUrl: value });
            saveApiBaseUrl(value);
          }}
          placeholder="/api or hosted URL"
        />
      </section>

      <section className="settings-card">
        <h3>Spoilers</h3>
        <ToggleRow
          label="Spoiler-safe mode"
          enabled={props.state.hideSpoilers}
          onChange={(value) => props.setState({ hideSpoilers: value })}
        />
        <SpoilerPolicyControls state={props.state} setState={props.setState} entities={props.entities} categories={props.categories} />
      </section>

      <section className="settings-card">
        <h3>F1 sessions</h3>
        {Object.entries(props.state.f1Sessions).map(([session, enabled]) => (
          <ToggleRow
            key={session}
            label={session}
            enabled={enabled}
            onChange={(value) => props.setF1Session(session, value)}
          />
        ))}
      </section>

      <section className="settings-card">
        <h3>Notifications</h3>
        <NotificationSettingsPanel entities={props.entities} categories={props.categories} />
      </section>

      <ManualPinForm onAdd={props.addManualPin} />
    </main>
  );
}

function NotificationSettingsPanel({ entities, categories }: { entities: EntityItem[]; categories: FollowCategory[] }) {
  const [settings, setSettings] = useState<NotificationSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [targetType, setTargetType] = useState<NotificationRule["target_type"]>("sport");
  const [sportTarget, setSportTarget] = useState<Sport>("football");
  const [categoryTarget, setCategoryTarget] = useState<FollowLevel>("main");
  const [entityTarget, setEntityTarget] = useState("");
  const [minutesBefore, setMinutesBefore] = useState(10);
  const [ruleName, setRuleName] = useState("");

  useEffect(() => {
    void refreshNotificationSettings();
  }, []);

  async function refreshNotificationSettings() {
    try {
      setSettings(await fetchNotificationSettings());
      setError(null);
    } catch (requestError) {
      setError(readErrorMessage(requestError));
    }
  }

  async function enablePush() {
    setBusy(true);
    try {
      const currentSettings = settings || (await fetchNotificationSettings());
      if (!currentSettings.push.configured) {
        throw new Error(`Push is not configured: ${currentSettings.push.missing.join(", ")}`);
      }
      if (!("Notification" in window) || !("serviceWorker" in navigator) || !("PushManager" in window)) {
        throw new Error("Push notifications are not supported in this browser.");
      }
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        throw new Error("Notification permission was not granted.");
      }
      const registration = await navigator.serviceWorker.ready;
      const existing = await registration.pushManager.getSubscription();
      const subscription =
        existing ||
        (await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToArrayBuffer(currentSettings.push.public_key),
        }));
      await savePushSubscription(subscription);
      await refreshNotificationSettings();
      setError(null);
    } catch (requestError) {
      setError(readErrorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function addRule() {
    const targetId = selectedNotificationTargetId(targetType, sportTarget, categoryTarget, entityTarget, entities);
    if (!targetId) {
      setError("Select notification target.");
      return;
    }
    const name = ruleName.trim() || `${notificationTargetLabel(targetType, targetId, entities, categories)} - ${minutesBefore} min`;
    setBusy(true);
    try {
      await saveNotificationRule({
        name,
        enabled: true,
        target_type: targetType,
        target_id: targetId,
        minutes_before: minutesBefore,
      });
      setRuleName("");
      await refreshNotificationSettings();
      setError(null);
    } catch (requestError) {
      setError(readErrorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function updateRule(rule: NotificationRule, patch: Partial<NotificationRule>) {
    setBusy(true);
    try {
      await saveNotificationRule({
        id: rule.id,
        name: patch.name ?? rule.name,
        enabled: patch.enabled ?? rule.enabled,
        target_type: patch.target_type ?? rule.target_type,
        target_id: patch.target_id ?? rule.target_id,
        minutes_before: patch.minutes_before ?? rule.minutes_before,
      });
      await refreshNotificationSettings();
      setError(null);
    } catch (requestError) {
      setError(readErrorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function removeRule(ruleId: string) {
    setBusy(true);
    try {
      await deleteNotificationRule(ruleId);
      await refreshNotificationSettings();
      setError(null);
    } catch (requestError) {
      setError(readErrorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  const subscriptions = settings?.subscriptions.filter((subscription) => subscription.enabled) || [];

  return (
    <div className="notification-panel">
      <div className="notification-status">
        <span>{settings?.push.configured ? "Push configured" : "Push not configured"}</span>
        <strong>{subscriptions.length ? `${subscriptions.length} device${subscriptions.length === 1 ? "" : "s"}` : "No device"}</strong>
        <button className="secondary-action" type="button" onClick={() => void enablePush()} disabled={busy}>
          Enable on this device
        </button>
      </div>

      <div className="notification-rule-form">
        <input value={ruleName} onChange={(event) => setRuleName(event.target.value)} placeholder="Rule name, optional" />
        <div className="filter-grid">
          <select value={targetType} onChange={(event) => setTargetType(event.target.value as NotificationRule["target_type"])}>
            <option value="sport">Sport</option>
            <option value="category">Category</option>
            <option value="entity">Team / tournament / driver</option>
          </select>
          {targetType === "sport" ? (
            <select value={sportTarget} onChange={(event) => setSportTarget(event.target.value as Sport)}>
              {SPORTS.map((sport) => (
                <option key={sport} value={sport}>
                  {sportLabel(sport)}
                </option>
              ))}
            </select>
          ) : null}
          {targetType === "category" ? (
            <select value={categoryTarget} onChange={(event) => setCategoryTarget(event.target.value)}>
              {categories.map((category) => (
                <option key={category.id} value={category.id}>
                  {category.name}
                </option>
              ))}
            </select>
          ) : null}
          {targetType === "entity" ? (
            <select value={entityTarget} onChange={(event) => setEntityTarget(event.target.value)}>
              <option value="">Select entity</option>
              {entities.map((entity) => (
                <option key={entity.id} value={entity.id}>
                  {entity.name}
                </option>
              ))}
            </select>
          ) : null}
          <input
            type="number"
            min="0"
            max="10080"
            value={minutesBefore}
            onChange={(event) => setMinutesBefore(Number(event.target.value))}
            aria-label="Minutes before event"
          />
        </div>
        <button className="primary-action" type="button" onClick={() => void addRule()} disabled={busy}>
          <Plus size={16} />
          Add notification rule
        </button>
      </div>

      {error ? <div className="inline-error">{error}</div> : null}

      <div className="notification-rule-list">
        {(settings?.rules || []).map((rule) => (
          <article className="notification-rule" key={rule.id}>
            <div>
              <strong>{rule.name}</strong>
              <span>
                {notificationTargetLabel(rule.target_type, rule.target_id, entities, categories)} - {rule.minutes_before} min before
              </span>
            </div>
            <ToggleRow label={rule.enabled ? "On" : "Off"} enabled={rule.enabled} onChange={(enabled) => void updateRule(rule, { enabled })} />
            <button className="text-action danger" type="button" onClick={() => void removeRule(rule.id)} disabled={busy}>
              Delete
            </button>
          </article>
        ))}
        {settings && !settings.rules.length ? <div className="empty-state compact">No notification rules yet.</div> : null}
      </div>
    </div>
  );
}

function SpoilerPolicyControls({
  state,
  setState,
  entities,
  categories,
}: {
  state: AppState;
  setState: (patch: Partial<AppState>) => void;
  entities: EntityItem[];
  categories: FollowCategory[];
}) {
  const spoilerMode = state.spoilerMode || "all";
  const followedEntities = entities.filter((entity) => entity.follow !== "explore" && entity.follow !== "hidden");

  function toggleSpoilerSport(sport: Sport) {
    setState({ spoilerSports: { ...state.spoilerSports, [sport]: !state.spoilerSports[sport] } });
  }

  function toggleSpoilerLevel(level: FollowLevel) {
    setState({ spoilerLevels: { ...state.spoilerLevels, [level]: !state.spoilerLevels[level] } });
  }

  function toggleSpoilerEntity(entityId: string) {
    setState({ spoilerEntities: { ...state.spoilerEntities, [entityId]: !state.spoilerEntities[entityId] } });
  }

  return (
    <div className="spoiler-policy">
      <label htmlFor="spoiler-mode">Spoiler mode</label>
      <select
        id="spoiler-mode"
        value={spoilerMode}
        onChange={(event) => setState({ spoilerMode: event.target.value as SpoilerMode, hideSpoilers: event.target.value !== "off" })}
      >
        <option value="all">Hide all scores</option>
        <option value="past">Hide past only</option>
        <option value="past_live">Hide past and live</option>
        <option value="custom">Custom sports, categories, teams</option>
        <option value="off">Show scores</option>
      </select>

      {spoilerMode === "custom" ? (
        <div className="spoiler-custom-grid">
          <div>
            <strong>Sports</strong>
            {SPORTS.map((sport) => (
              <ToggleRow key={sport} label={sportLabel(sport)} enabled={Boolean(state.spoilerSports[sport])} onChange={() => toggleSpoilerSport(sport)} />
            ))}
          </div>
          <div>
            <strong>Categories</strong>
            {categories.map((category) => (
              <ToggleRow key={category.id} label={category.name} enabled={Boolean(state.spoilerLevels[category.id])} onChange={() => toggleSpoilerLevel(category.id)} />
            ))}
          </div>
          <div>
            <strong>Teams / tournaments</strong>
            {followedEntities.slice(0, 20).map((entity) => (
              <ToggleRow key={entity.id} label={entity.name} enabled={Boolean(state.spoilerEntities[entity.id])} onChange={() => toggleSpoilerEntity(entity.id)} />
            ))}
            {followedEntities.length > 20 ? <p>Showing first 20 followed entities. Use categories for broad rules.</p> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ManualPinForm({ onAdd }: { onAdd: (event: MatchEvent) => void }) {
  const [title, setTitle] = useState("");
  const [sport, setSport] = useState<Sport>("football");
  const [dateTime, setDateTime] = useState("");
  const [competition, setCompetition] = useState("");

  function submit() {
    if (!title.trim()) {
      return;
    }
    onAdd({
      id: `manual-${Date.now()}`,
      title: title.trim(),
      sport,
      starts_at: dateTime ? new Date(dateTime).toISOString() : null,
      status: dateTime ? statusFromDate(new Date(dateTime)) : "tbd",
      entity_ids: [`manual_${sport}`],
      source: "manual",
      competition: competition.trim() || null,
      session_type: null,
      result_summary: null,
      importance: 100,
      follow_level: "main",
      result_hidden: false,
    });
    setTitle("");
    setCompetition("");
    setDateTime("");
  }

  return (
    <section className="settings-card">
      <h3>Manual pin</h3>
      <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Event title" />
      <div className="form-grid">
        <select value={sport} onChange={(event) => setSport(event.target.value as Sport)}>
          {SPORTS.map((item) => (
            <option key={item} value={item}>
              {sportLabel(item)}
            </option>
          ))}
        </select>
        <label className="datetime-field">
          <span>Date and time</span>
          <input type="datetime-local" value={dateTime} onChange={(event) => setDateTime(event.target.value)} />
        </label>
      </div>
      <input value={competition} onChange={(event) => setCompetition(event.target.value)} placeholder="Competition, optional" />
      <button className="primary-action" type="button" onClick={submit}>
        <Plus size={16} />
        Add event
      </button>
    </section>
  );
}

function EventGroups(props: {
  groups: DayGroup[];
  categories: FollowCategory[];
  watch: Record<string, WatchStatus>;
  revealed: Record<string, boolean>;
  onWatch: (eventId: string, value: WatchStatus) => void;
  onReveal: (eventId: string) => void;
  onRemoveManual: (eventId: string) => void;
}) {
  if (!props.groups.length) {
    return <div className="empty-state">No followed events in this range.</div>;
  }

  return (
    <section className="event-groups">
      {props.groups.map((group) => (
        <div className="day-group" key={group.date}>
          <h2>{formatGroupDate(group.date)}</h2>
          {group.events.map((event) => (
            <EventCard
              event={event}
              categories={props.categories}
              key={event.id}
              watch={props.watch[event.id] || "none"}
              revealed={props.revealed[event.id] || false}
              onWatch={props.onWatch}
              onReveal={props.onReveal}
              onRemoveManual={props.onRemoveManual}
            />
          ))}
        </div>
      ))}
    </section>
  );
}

function EventCard(props: {
  event: MatchEvent;
  categories: FollowCategory[];
  watch: WatchStatus;
  revealed: boolean;
  onWatch: (eventId: string, value: WatchStatus) => void;
  onReveal: (eventId: string) => void;
  onRemoveManual: (eventId: string) => void;
}) {
  const event = props.event;
  const category = categoryForLevel(props.categories, event.follow_level);
  const [details, setDetails] = useState<EventDetails | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const shouldHideResult = event.result_hidden && !event.result_summary;

  async function toggleDetails() {
    if (detailsOpen) {
      setDetailsOpen(false);
      return;
    }
    setDetailsOpen(true);
    if (details || (event.sport !== "formula" && event.sport !== "cs2")) {
      return;
    }
    setDetailsLoading(true);
    setDetailsError(null);
    try {
      setDetails(await fetchEventDetails(event.id));
    } catch (error) {
      setDetailsError(readErrorMessage(error));
    } finally {
      setDetailsLoading(false);
    }
  }

  return (
    <article className={`event-card sport-border-${event.sport} ${props.watch === "skip" ? "is-skipped" : ""}`}>
      <div className="event-meta">
        <span className={`status status-${event.status}`}>{event.status}</span>
        <span>{formatEventTimeForEvent(event)}</span>
        <span className="timezone-badge">Kyiv</span>
        <span className="source-badge" title="Data source">
          <span aria-hidden="true" />
          {event.source}
        </span>
      </div>
      <h3>{event.title}</h3>
      <div className="event-tags">
        <span className={`sport-tag sport-${event.sport}`}>{sportLabel(event.sport)}</span>
        {event.competition ? <span>{event.competition}</span> : null}
        <span className={event.follow_level === "main" ? "main-badge" : "starred-badge"}>
          {category.name}
        </span>
      </div>

      {shouldHideResult ? (
        <button className="inline-action" type="button" onClick={() => props.onReveal(event.id)}>
          <EyeOff size={15} />
          Score hidden - reveal
        </button>
      ) : event.result_summary ? (
        <div className="result-line">{event.result_summary}</div>
      ) : null}

      <div className="event-actions">
        <WatchSelect value={props.watch} onChange={(value) => props.onWatch(event.id, value)} />
        {event.sport === "formula" || event.sport === "cs2" ? (
          <button className="text-action" type="button" onClick={() => void toggleDetails()}>
            {detailsOpen ? "Hide details" : "Details"}
          </button>
        ) : null}
        {event.source === "manual" ? (
          <button className="text-action danger" type="button" onClick={() => props.onRemoveManual(event.id)}>
            Remove
          </button>
        ) : null}
      </div>

      {detailsOpen ? (
        <EventDetailsPanel details={details} loading={detailsLoading} error={detailsError} />
      ) : null}
    </article>
  );
}

function EventDetailsPanel({
  details,
  loading,
  error,
}: {
  details: EventDetails | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return <div className="details-panel">Loading session details...</div>;
  }
  if (error) {
    return <div className="details-panel">Details unavailable: {error}</div>;
  }
  if (!details) {
    return null;
  }

  return (
    <div className="details-panel">
      <p>{details.summary}</p>
      {details.facts.length ? (
        <div className="facts-grid">
          {details.facts.map((fact) => (
            <span key={fact.label}>
              <strong>{fact.label}</strong>
              {fact.value}
            </span>
          ))}
        </div>
      ) : null}
      {details.sections.map((section) => (
        <SortableDetailsTable section={section} key={section.title} />
      ))}
    </div>
  );
}

function SortableDetailsTable({ section }: { section: EventDetails["sections"][number] }) {
  const [sort, setSort] = useState<{ columnIndex: number; direction: "asc" | "desc" } | null>(null);
  const sortedRows = useMemo(() => {
    if (!sort) {
      return section.rows;
    }
    return [...section.rows].sort((left, right) => {
      const result = compareTableCells(left[sort.columnIndex], right[sort.columnIndex]);
      return sort.direction === "asc" ? result : -result;
    });
  }, [section.rows, sort]);

  function toggleSort(columnIndex: number) {
    setSort((current) => {
      if (!current || current.columnIndex !== columnIndex) {
        return { columnIndex, direction: "asc" };
      }
      if (current.direction === "asc") {
        return { columnIndex, direction: "desc" };
      }
      return null;
    });
  }

  return (
    <div className="details-table-wrap">
      <h4>{section.title}</h4>
      <table className="details-table">
        <thead>
          <tr>
            {section.columns.map((column, columnIndex) => (
              <th className={tableColumnClass(column)} key={column}>
                <button
                  className={`table-sort-button ${sort?.columnIndex === columnIndex ? "is-active" : ""}`}
                  type="button"
                  onClick={() => toggleSort(columnIndex)}
                  title={`Sort by ${column}`}
                >
                  <span>{column}</span>
                  {sort?.columnIndex === columnIndex ? <small>{sort.direction}</small> : null}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, rowIndex) => (
            <tr key={`${section.title}-${rowIndex}-${row.join("|")}`}>
              {row.map((cell, cellIndex) => (
                <td className={tableColumnClass(section.columns[cellIndex])} key={`${section.title}-${rowIndex}-${cellIndex}`}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function tableColumnClass(column: string): string | undefined {
  if (column === "Pos") {
    return "details-sticky-pos";
  }
  if (column === "DRV") {
    return "details-sticky-drv";
  }
  if (["Tyres / stops", "Tyre stints", "Pit / changes", "URL", "Note"].includes(column)) {
    return "tyre-strategy-cell";
  }
  return undefined;
}

function compareTableCells(left: string | undefined, right: string | undefined): number {
  const leftValue = normalizedTableSortValue(left);
  const rightValue = normalizedTableSortValue(right);
  if (typeof leftValue === "number" && typeof rightValue === "number") {
    return leftValue - rightValue;
  }
  return String(leftValue).localeCompare(String(rightValue), undefined, { numeric: true, sensitivity: "base" });
}

function normalizedTableSortValue(value: string | undefined): number | string {
  const text = (value || "").trim();
  if (!text || text === "-") {
    return Number.POSITIVE_INFINITY;
  }
  const hltvRank = text.match(/^#(\d+)$/);
  if (hltvRank) {
    return Number(hltvRank[1]);
  }
  const signedNumber = text.match(/^([+-]?\d+(?:\.\d+)?)/);
  if (signedNumber) {
    return Number(signedNumber[1]);
  }
  const lapTime = text.match(/^(?:(\d+):)?(\d+):(\d{2}(?:\.\d+)?)$/);
  if (lapTime) {
    const hours = Number(lapTime[1] || 0);
    const minutes = Number(lapTime[2] || 0);
    const seconds = Number(lapTime[3] || 0);
    return hours * 3600 + minutes * 60 + seconds;
  }
  return text.toLowerCase();
}

function formatEventTimeForEvent(event: MatchEvent): string {
  if (event.source === "f4-calendar") {
    return "TBD";
  }
  return formatEventTime(event.starts_at);
}

function CalendarDay({
  date,
  events,
  dimmed,
  today,
  selected,
  onSelect,
}: {
  date: Date;
  events: MatchEvent[];
  dimmed: boolean;
  today: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  const summary = calendarSportSummary(events);

  return (
    <button
      className={`calendar-day ${dimmed ? "is-dimmed" : ""} ${events.length ? "has-events" : ""} ${today ? "is-today" : ""} ${selected ? "is-selected" : ""}`}
      type="button"
      onClick={onSelect}
    >
      <span>{date.getDate()}</span>
      <div className="calendar-markers" aria-label={`${events.length} events`}>
        {summary.map((item) => (
          <span
            className={`calendar-marker calendar-marker-${item.sport} ${item.count ? "has-count" : "is-empty"}`}
            key={item.sport}
          >
            {item.count >= 2 ? <strong>{item.count}</strong> : null}
          </span>
        ))}
      </div>
    </button>
  );
}

function calendarSportSummary(events: MatchEvent[]): Array<{ sport: Sport; count: number }> {
  const orderedSports: Sport[] = ["formula", "cs2", "football"];
  return orderedSports.map((sport) => ({
    sport,
    count: events.filter((event) => event.sport === sport).length,
  }));
}

function filterGroupsByDateRange(groups: DayGroup[], start: string | null, end: string | null): DayGroup[] {
  if (!start) {
    return groups;
  }
  const rangeEnd = end || start;
  return groups.filter((group) => group.date >= start && group.date <= rangeEnd);
}

function replaceEventInGroups(groups: DayGroup[], replacement: MatchEvent): DayGroup[] {
  return groups.map((group) => ({
    ...group,
    events: group.events.map((event) => (event.id === replacement.id ? replacement : event)),
  }));
}

function isDateSelected(date: string, start: string | null, end: string | null): boolean {
  if (!start) {
    return false;
  }
  const rangeEnd = end || start;
  return date >= start && date <= rangeEnd;
}

function sortDateKeys(a: string, b: string): [string, string] {
  return a <= b ? [a, b] : [b, a];
}

function formatSelectionTitle(start: string | null, end: string | null, monthCursor: Date): string {
  if (!start) {
    return formatMonthTitle(monthCursor);
  }
  if (!end || end === start) {
    return formatGroupDate(start);
  }
  return `${formatGroupDate(start)} - ${formatGroupDate(end)}`;
}

function defaultCalendarStart(monthCursor: Date, statuses: Set<EventStatus>): string | null {
  if (statuses.has("past")) {
    return null;
  }
  return isCurrentMonth(monthCursor) ? localDateKey(new Date()) : null;
}

function defaultCalendarEnd(monthCursor: Date, statuses: Set<EventStatus>): string | null {
  if (statuses.has("past")) {
    return null;
  }
  if (!isCurrentMonth(monthCursor)) {
    return null;
  }
  return localDateKey(new Date(monthCursor.getFullYear(), monthCursor.getMonth() + 1, 0));
}

function isCurrentMonth(monthCursor: Date): boolean {
  const now = new Date();
  return monthCursor.getFullYear() === now.getFullYear() && monthCursor.getMonth() === now.getMonth();
}

function ConflictPanel({ conflicts }: { conflicts: MatchEvent[][] }) {
  const [isOpen, setIsOpen] = useState(true);

  return (
    <section className="conflict-panel">
      <button className="conflict-toggle" type="button" onClick={() => setIsOpen(!isOpen)}>
        <span>Overlaps</span>
        <span>{isOpen ? "Hide" : `Show ${conflicts.length}`}</span>
      </button>
      {isOpen
        ? conflicts.slice(0, 5).map((group) => (
            <p key={group.map((event) => event.id).join("-")}>
              {group.map(formatConflictEvent).join(" + ")}
            </p>
          ))
        : null}
    </section>
  );
}

function formatConflictEvent(event: MatchEvent): string {
  return `${event.title} (${formatConflictDateTime(event.starts_at)})`;
}

function formatConflictDateTime(value: string | null): string {
  if (!value) {
    return "TBD";
  }
  const date = new Date(value);
  return `${formatGroupDate(localDateKey(date))}, ${formatEventTime(value)}`;
}

function SegmentedControl(props: {
  value: string;
  options: [string, string][];
  onChange: (value: string) => void;
}) {
  return (
    <div className="segmented-control">
      {props.options.map(([value, label]) => (
        <button className={props.value === value ? "is-active" : ""} key={value} type="button" onClick={() => props.onChange(value)}>
          {label}
        </button>
      ))}
    </div>
  );
}

function CategoryChecklist({
  categories,
  selected,
  onToggle,
}: {
  categories: FollowCategory[];
  selected: FollowLevel[];
  onToggle: (level: FollowLevel) => void;
}) {
  return (
    <div className="category-checklist">
      {categories.map((category) => (
        <label className="category-check" key={category.id}>
          <input type="checkbox" checked={selected.includes(category.id)} onChange={() => onToggle(category.id)} />
          <span style={{ backgroundColor: category.color }} />
          {category.name}
        </label>
      ))}
    </div>
  );
}

function ToggleRow(props: { label: string; enabled: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="toggle-row">
      <span>{props.label}</span>
      <input type="checkbox" checked={props.enabled} onChange={(event) => props.onChange(event.target.checked)} />
    </label>
  );
}

function FollowSelect({
  categories,
  value,
  onChange,
}: {
  categories: FollowCategory[];
  value: FollowLevel;
  onChange: (value: FollowLevel) => void;
}) {
  return (
    <select className="compact-select" value={value} onChange={(event) => onChange(event.target.value as FollowLevel)}>
      {followLevels(categories).map((level) => (
        <option key={level} value={level}>
          {categoryForLevel(categories, level).name}
        </option>
      ))}
    </select>
  );
}

function WatchSelect({ value, onChange }: { value: WatchStatus; onChange: (value: WatchStatus) => void }) {
  return (
    <select className="compact-select" value={value} onChange={(event) => onChange(event.target.value as WatchStatus)}>
      <option value="none">No status</option>
      <option value="will_watch">Will watch</option>
      <option value="watching_live">Watching live</option>
      <option value="watched">Watched</option>
      <option value="skip">Skip</option>
    </select>
  );
}

function NavButton(props: { icon: React.ReactNode; label: string; active: boolean; onClick: () => void }) {
  return (
    <button className={props.active ? "is-active" : ""} type="button" onClick={props.onClick}>
      {props.icon}
      <span>{props.label}</span>
    </button>
  );
}

function loadInitialTimelineCache(state: AppState): { at: number; timeline: DayGroup[]; entities: EntityItem[] } | null {
  return loadCachedData(timelineCacheKey("week", state, "main", state.customFeedLevels));
}

function loadInitialCalendarCache(state: AppState): DayGroup[] | null {
  const now = new Date();
  const levels = followLevelsForFeedMode("main", state.customFeedLevels);
  return loadCachedCalendar(calendarCacheKey(now.getFullYear(), now.getMonth() + 1, levels));
}

function loadCachedData(cacheKey: string): { at: number; timeline: DayGroup[]; entities: EntityItem[] } | null {
  const stored = localStorage.getItem(cacheKey);
  if (!stored) {
    return null;
  }
  try {
    return JSON.parse(stored) as { at: number; timeline: DayGroup[]; entities: EntityItem[] };
  } catch {
    return null;
  }
}

function timelineCacheKey(range: RangeFilter, state: AppState, feedMode: FeedMode, customFeedLevels: FollowLevel[]): string {
  return `${CACHE_PREFIX}${range}.${feedMode}.${customFeedLevels.join("_")}.${spoilerCacheSignature(state)}`;
}

function calendarCacheKey(year: number, month: number, levels: FollowLevel[] = []): string {
  return `${CALENDAR_CACHE_PREFIX}${year}-${String(month).padStart(2, "0")}.${levels.join("_") || "default"}`;
}

function spoilerCacheSignature(state: AppState): string {
  if (!state.hideSpoilers || state.spoilerMode === "off") {
    return "spoilers-off";
  }
  return [
    state.spoilerMode || "all",
    objectSignature(state.spoilerSports),
    objectSignature(state.spoilerLevels),
    objectSignature(state.spoilerEntities),
  ].join(".");
}

function objectSignature(value: Record<string, unknown> | undefined): string {
  return Object.entries(value || {})
    .filter(([, enabled]) => Boolean(enabled))
    .map(([key]) => key)
    .sort()
    .join("_") || "none";
}

function mergeAccountSettings(current: AppState, settings: {
  f1_sessions: string[];
  hide_spoilers: boolean;
  ui_state: Record<string, unknown>;
}): AppState {
  const uiState = settings.ui_state as Partial<AppState>;
  return {
    ...current,
    ...uiState,
    categories: normalizeCategories(uiState.categories),
    customFeedLevels: Array.isArray(uiState.customFeedLevels) ? uiState.customFeedLevels : current.customFeedLevels,
    follows: current.follows,
    apiUrl: current.apiUrl,
    f1Sessions: f1SessionsRecord(settings.f1_sessions),
    hideSpoilers: settings.hide_spoilers,
  };
}

function accountSettingsPayload(state: AppState, viewState: {
  range: RangeFilter;
  feedMode: FeedMode;
  importanceMode: ImportanceMode;
  sports: Sport[];
  timelineStatuses: EventStatus[];
  calendarStatuses: EventStatus[];
  statusDefaultsVersion: number;
}): {
  f1_sessions: string[];
  hide_spoilers: boolean;
  ui_state: Record<string, unknown>;
} {
  const { follows: _follows, apiUrl: _apiUrl, ...uiState } = state;
  return {
    f1_sessions: Object.entries(state.f1Sessions).filter(([, value]) => value).map(([key]) => key),
    hide_spoilers: state.hideSpoilers,
    ui_state: { ...uiState, ...viewState },
  };
}

function f1SessionsRecord(sessions: string[]): Record<string, boolean> {
  return {
    race: sessions.includes("race"),
    qualifying: sessions.includes("qualifying"),
    sprint: sessions.includes("sprint"),
    practice: sessions.includes("practice"),
  };
}

function followsFromEntities(entities: EntityItem[]): Record<string, FollowLevel> {
  const follows: Record<string, FollowLevel> = {};
  for (const entity of entities) {
    if (entity.follow !== "explore") {
      follows[entity.id] = entity.follow;
    }
  }
  return follows;
}

function isRangeFilter(value: unknown): value is RangeFilter {
  return value === "today" || value === "week" || value === "month";
}

function isFeedMode(value: unknown): value is FeedMode {
  return value === "main" || value === "starred" || value === "custom";
}

function normalizeFeedMode(value: unknown): FeedMode | null {
  if (value === "main_starred" || value === "all" || value === "starred") {
    return "starred";
  }
  if (value === "starred_only" || value === "starred_explore") {
    return "custom";
  }
  return isFeedMode(value) ? value : null;
}

function isImportanceMode(value: unknown): value is ImportanceMode {
  return value === "all" || value === "main" || value === "significant";
}

function isSport(value: unknown): value is Sport {
  return value === "formula" || value === "cs2" || value === "football";
}

function isEventStatus(value: unknown): value is EventStatus {
  return value === "past" || value === "live" || value === "delayed" || value === "upcoming" || value === "tbd";
}

function normalizeStoredStatuses(values: unknown[], shouldMigrateStatusDefaults: boolean): EventStatus[] {
  const statuses = values.filter(isEventStatus);
  const migrated = shouldMigrateStatusDefaults ? statuses.filter((status) => status !== "past") : statuses;
  return migrated.length ? migrated : DEFAULT_VISIBLE_STATUSES;
}

function loadCachedCalendar(key: string): DayGroup[] | null {
  const stored = localStorage.getItem(key);
  if (!stored) {
    return null;
  }
  try {
    const payload = JSON.parse(stored) as { groups?: DayGroup[] };
    return payload.groups || null;
  } catch {
    return null;
  }
}

function readErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Unknown frontend error";
}

async function resetLocalData(): Promise<void> {
  for (const key of Object.keys(localStorage)) {
    if (key.startsWith("matchnest.")) {
      localStorage.removeItem(key);
    }
  }
  sessionStorage.clear();

  if ("serviceWorker" in navigator) {
    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(registrations.map((registration) => registration.unregister()));
  }

  if ("caches" in window) {
    const keys = await caches.keys();
    await Promise.all(keys.map((key) => caches.delete(key)));
  }

  window.location.replace(`${window.location.pathname}?reset=${Date.now()}`);
}

function toggleSet<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}

function groupEntities(entities: EntityItem[]) {
  const grouped = new Map<FollowLevel, EntityItem[]>();
  for (const entity of entities) {
    grouped.set(entity.follow, [...(grouped.get(entity.follow) || []), entity]);
  }
  return grouped;
}

function followLevels(categories: FollowCategory[]): FollowLevel[] {
  return categories.map((category) => category.id);
}

function categoryForLevel(categories: FollowCategory[], level: FollowLevel): FollowCategory {
  return categories.find((category) => category.id === level) || { id: level, name: titleCase(level), color: "#9aa3b2" };
}

function slugifyCategory(name: string): string {
  const slug = name.toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  return slug || "category";
}

function uniqueCategoryId(base: string, categories: FollowCategory[]): string {
  const existing = new Set(categories.map((category) => category.id));
  if (!existing.has(base)) {
    return base;
  }
  let suffix = 2;
  while (existing.has(`${base}_${suffix}`)) {
    suffix += 1;
  }
  return `${base}_${suffix}`;
}

function nextCategoryColor(index: number): string {
  const colors = ["#38bdf8", "#f97316", "#14b8a6", "#ec4899", "#a3e635", "#facc15", "#fb7185"];
  return colors[index % colors.length];
}

function titleCase(value: string): string {
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

function entityKinds(entities: EntityItem[]): string[] {
  return Array.from(new Set(entities.map((entity) => entity.kind))).sort();
}

function kindLabel(kind: string): string {
  if (kind === "team") {
    return "Team";
  }
  if (kind === "competition") {
    return "Tournament";
  }
  if (kind === "player") {
    return "Player";
  }
  if (kind === "session_type") {
    return "Session";
  }
  return kind;
}

function sportLabel(sport: Sport): string {
  return sport === "formula" ? "F1" : sport === "cs2" ? "CS2" : "Football";
}

function selectedNotificationTargetId(
  targetType: NotificationRule["target_type"],
  sportTarget: Sport,
  categoryTarget: FollowLevel,
  entityTarget: string,
  entities: EntityItem[],
): string {
  if (targetType === "sport") {
    return sportTarget;
  }
  if (targetType === "category") {
    return categoryTarget;
  }
  if (targetType === "entity") {
    return entities.some((entity) => entity.id === entityTarget) ? entityTarget : "";
  }
  return "";
}

function notificationTargetLabel(
  targetType: NotificationRule["target_type"],
  targetId: string,
  entities: EntityItem[],
  categories: FollowCategory[],
): string {
  if (targetType === "sport") {
    return sportLabel(targetId as Sport);
  }
  if (targetType === "category") {
    return categoryForLevel(categories, targetId).name;
  }
  return entities.find((entity) => entity.id === targetId)?.name || targetId;
}

function urlBase64ToArrayBuffer(value: string): ArrayBuffer {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = `${value}${padding}`.replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  const output = new Uint8Array(new ArrayBuffer(raw.length));
  for (let index = 0; index < raw.length; index += 1) {
    output[index] = raw.charCodeAt(index);
  }
  return output.buffer;
}

function defaultSportColor(sport: Sport): string {
  return sport === "formula" ? "#F04438" : sport === "cs2" ? "#F4B740" : "#2ECC71";
}

function splitCsv(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function bindingsToText(bindings: EntityItem["bindings"]): string {
  return (bindings || []).map((binding) => `${binding.provider}:${binding.binding_type}=${binding.value}`).join("\n");
}

function parseBindingsText(value: string): Array<{ provider: string; binding_type: string; value: string }> {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [left, bindingValue] = line.split("=", 2);
      const [provider, bindingType] = left.split(":", 2);
      return {
        provider: (provider || "").trim(),
        binding_type: (bindingType || "").trim(),
        value: (bindingValue || "").trim(),
      };
    })
    .filter((binding) => binding.provider && binding.binding_type && binding.value);
}

function notifyLabel(preset: NotifyPreset): string {
  if (preset === "15m") {
    return "15 min before";
  }
  if (preset === "1h") {
    return "1 hour before";
  }
  if (preset === "morning") {
    return "Morning digest";
  }
  return "At start";
}
