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
  fetchAccountSettings,
  fetchEntityDetail,
  fetchCalendar,
  fetchCurrentUser,
  fetchEntities,
  fetchEventDetails,
  fetchGoogleLoginUrl,
  fetchTimeline,
  loginAccount,
  logoutAccount,
  registerAccount,
  saveApiBaseUrl,
  saveAccountSettings,
  saveAuthToken,
  saveF1Sessions,
  saveFollowLevel,
  searchEntities,
  updateEntity,
  verifyEmailToken,
} from "./api";
import {
  CACHE_PREFIX,
  CALENDAR_CACHE_PREFIX,
  type AppState,
  type FeedMode,
  type ImportanceMode,
  type NotifyPreset,
  STORAGE_KEY,
  type WatchStatus,
  loadAppState,
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
import { appendManualPins, filterGroups, findConflicts, flattenGroups, mergeFollowOverrides } from "./eventFilters";
import type { AuthUser, DayGroup, EntityItem, EntitySearchResult, EventDetails, EventStatus, FollowLevel, MatchEvent, RangeFilter, RegisterResponse, Sport } from "./types";

type Tab = "timeline" | "calendar" | "explore" | "settings";

export function App() {
  const [tab, setTab] = useState<Tab>("timeline");
  const [range, setRange] = useState<RangeFilter>("week");
  const [feedMode, setFeedMode] = useState<FeedMode>("main");
  const [importanceMode, setImportanceMode] = useState<ImportanceMode>("all");
  const [timelineStatuses, setTimelineStatuses] = useState<Set<EventStatus>>(new Set(["live", "delayed", "upcoming"]));
  const [calendarStatuses, setCalendarStatuses] = useState<Set<EventStatus>>(new Set(["past", "live", "delayed", "upcoming"]));
  const [sports, setSports] = useState<Set<Sport>>(new Set(SPORTS));
  const [monthCursor, setMonthCursor] = useState(() => new Date());
  const [timeline, setTimeline] = useState<DayGroup[]>([]);
  const [calendarGroups, setCalendarGroups] = useState<DayGroup[]>([]);
  const [entities, setEntities] = useState<EntityItem[]>([]);
  const [state, setState] = useState<AppState>(loadAppState);
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
  const timelineRetriedKeyRef = useRef<string | null>(null);
  const calendarRetriedKeyRef = useRef<string | null>(null);

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
  }, [range, state.hideSpoilers, currentUser, accountSettingsLoaded]);

  useEffect(() => {
    if (currentUser && accountSettingsLoaded) {
      void loadCalendar();
    }
  }, [monthCursor, state.hideSpoilers, currentUser, accountSettingsLoaded]);

  async function loadTimeline() {
    setLoading(true);
    try {
      const [nextTimeline, nextEntities] = await Promise.all([
        fetchTimeline(range, !state.hideSpoilers),
        fetchEntities(),
      ]);
      if (nextTimeline.length || timeline.length === 0) {
        setTimeline(nextTimeline);
      }
      setEntities(nextEntities);
      syncFollowsFromEntities(nextEntities);
      setOffline(false);
      setCacheNote(null);
      setLastError(null);
      if (nextTimeline.length || timeline.length === 0) {
        localStorage.setItem(timelineCacheKey(range, state.hideSpoilers), JSON.stringify({ at: Date.now(), timeline: nextTimeline, entities: nextEntities }));
      }
      if (!nextTimeline.length) {
        scheduleTimelineRetry();
      }
    } catch (error) {
      setLastError(readErrorMessage(error));
      const cached = loadCachedData(timelineCacheKey(range, state.hideSpoilers));
      if (cached) {
        setTimeline(cached.timeline);
        setEntities(cached.entities);
        setCacheNote(`Cached ${formatRelativeTime(cached.at)}`);
      } else {
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
    const cacheKey = calendarCacheKey(year, month);
    const cached = loadCachedCalendar(cacheKey);
    if (cached) {
      setCalendarGroups(cached);
    }
    try {
      const nextGroups = await fetchCalendar(year, month, !state.hideSpoilers);
      if (nextGroups.length || !cached) {
        setCalendarGroups(nextGroups);
      }
      if (nextGroups.length || !cached) {
        localStorage.setItem(cacheKey, JSON.stringify({ at: Date.now(), groups: nextGroups }));
      }
      if (!nextGroups.length) {
        scheduleCalendarRetry();
      }
    } catch {
      if (!cached) {
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
        importanceMode,
        state,
        entityMap,
      }),
    [mergedTimeline, sports, timelineStatuses, feedMode, importanceMode, state, entityMap],
  );
  const filteredCalendar = useMemo(
    () =>
      filterGroups(mergedCalendar, {
        sports,
        statuses: calendarStatuses,
        feedMode,
        importanceMode,
        state,
        entityMap,
      }),
    [mergedCalendar, sports, calendarStatuses, feedMode, importanceMode, state, entityMap],
  );
  const conflicts = useMemo(() => findConflicts(flattenGroups(filteredTimeline)), [filteredTimeline]);

  function updateState(patch: Partial<AppState>) {
    setState((current) => ({ ...current, ...patch }));
  }

  function syncViewSettings(uiState: Record<string, unknown>) {
    if (isRangeFilter(uiState.range)) {
      setRange(uiState.range);
    }
    if (isFeedMode(uiState.feedMode)) {
      setFeedMode(uiState.feedMode);
    }
    if (isImportanceMode(uiState.importanceMode)) {
      setImportanceMode(uiState.importanceMode);
    }
    if (Array.isArray(uiState.sports)) {
      setSports(new Set(uiState.sports.filter(isSport)));
    }
    if (Array.isArray(uiState.timelineStatuses)) {
      setTimelineStatuses(new Set(uiState.timelineStatuses.filter(isEventStatus)));
    }
    if (Array.isArray(uiState.calendarStatuses)) {
      setCalendarStatuses(new Set(uiState.calendarStatuses.filter(isEventStatus)));
    }
  }

  function syncFollowsFromEntities(nextEntities: EntityItem[]) {
    const follows = followsFromEntities(nextEntities);
    setState((current) => ({ ...current, follows }));
  }

  function scheduleTimelineRetry() {
    const retryKey = timelineCacheKey(range, state.hideSpoilers);
    if (timelineRetryRef.current !== null || timelineRetriedKeyRef.current === retryKey) {
      return;
    }
    timelineRetriedKeyRef.current = retryKey;
    timelineRetryRef.current = window.setTimeout(() => {
      timelineRetryRef.current = null;
      void loadTimeline();
    }, 3500);
  }

  function scheduleCalendarRetry() {
    const retryKey = calendarCacheKey(monthCursor.getFullYear(), monthCursor.getMonth() + 1);
    if (calendarRetryRef.current !== null || calendarRetriedKeyRef.current === retryKey) {
      return;
    }
    calendarRetriedKeyRef.current = retryKey;
    calendarRetryRef.current = window.setTimeout(() => {
      calendarRetryRef.current = null;
      void loadCalendar();
    }, 3500);
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

  function toggleReveal(eventId: string) {
    updateState({ revealed: { ...state.revealed, [eventId]: !state.revealed[eventId] } });
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
        <ExploreScreen entities={allEntities} setFollow={setFollow} refreshEntities={loadEntitiesOnly} />
      ) : null}

      {tab === "settings" ? (
        <SettingsScreen
          state={state}
          setState={updateState}
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
            ["all", "All"],
          ]}
          onChange={(value) => props.setFeedMode(value as FeedMode)}
        />
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
            ["all", "All"],
          ]}
          onChange={(value) => props.setFeedMode(value as FeedMode)}
        />
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
  setFollow,
  refreshEntities,
}: {
  entities: EntityItem[];
  setFollow: (entityId: string, level: FollowLevel) => void;
  refreshEntities: () => Promise<void>;
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
  const stats = followLevels().map((level) => ({
    level,
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
              {item.level}
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
            {followLevels().map((level) => (
              <option key={level} value={level}>
                {level}
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
              <EntityCard entity={entity} key={entity.id} setFollow={setFollow} onDetails={openDetails} />
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
          <FollowSelect value={newLevel} onChange={setNewLevel} />
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

      <div className="entity-hub">
        {followLevels().map((level) => {
          const items = groups.get(level) || [];
          return (
            <section className="entity-category" key={level}>
              <div className="entity-category-header">
                <h3>{level}</h3>
                <span>{items.length}</span>
              </div>
              {items.length ? (
                <div className="entity-card-grid">
                  {items.map((entity) => (
                    <EntityCard entity={entity} key={entity.id} setFollow={setFollow} onDetails={openDetails} />
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
  setFollow,
  onDetails,
}: {
  entity: EntityItem;
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
      <FollowSelect value={entity.follow} onChange={(level) => setFollow(entity.id, level)} />
    </article>
  );
}

function EntityDrawer({
  entity,
  onClose,
  setFollow,
  refreshEntities,
  setSelectedEntity,
}: {
  entity: EntityItem;
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
      <FollowSelect value={entity.follow} onChange={(level) => setFollow(entity.id, level)} />
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
          label="Hide past scores by default"
          enabled={props.state.hideSpoilers}
          onChange={(value) => props.setState({ hideSpoilers: value })}
        />
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
        {Object.entries(props.state.notify).map(([preset, enabled]) => (
          <ToggleRow
            key={preset}
            label={notifyLabel(preset as NotifyPreset)}
            enabled={enabled}
            onChange={(value) =>
              props.setState({
                notify: { ...props.state.notify, [preset]: value },
              })
            }
          />
        ))}
      </section>

      <ManualPinForm onAdd={props.addManualPin} />
    </main>
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
  watch: WatchStatus;
  revealed: boolean;
  onWatch: (eventId: string, value: WatchStatus) => void;
  onReveal: (eventId: string) => void;
  onRemoveManual: (eventId: string) => void;
}) {
  const event = props.event;
  const [details, setDetails] = useState<EventDetails | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const shouldHideResult = event.result_hidden && !props.revealed;

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
        <span>{formatEventTime(event.starts_at)}</span>
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
          {event.follow_level === "main" ? "Main" : "Starred"}
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
        <div className="details-table-wrap" key={section.title}>
          <h4>{section.title}</h4>
          <table className="details-table">
            <thead>
              <tr>
                {section.columns.map((column) => (
                  <th className={tableColumnClass(column)} key={column}>
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {section.rows.map((row, rowIndex) => (
                <tr key={`${section.title}-${rowIndex}`}>
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
      ))}
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

function ToggleRow(props: { label: string; enabled: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="toggle-row">
      <span>{props.label}</span>
      <input type="checkbox" checked={props.enabled} onChange={(event) => props.onChange(event.target.checked)} />
    </label>
  );
}

function FollowSelect({ value, onChange }: { value: FollowLevel; onChange: (value: FollowLevel) => void }) {
  return (
    <select className="compact-select" value={value} onChange={(event) => onChange(event.target.value as FollowLevel)}>
      {followLevels().map((level) => (
        <option key={level} value={level}>
          {level}
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

function timelineCacheKey(range: RangeFilter, hideSpoilers: boolean): string {
  return `${CACHE_PREFIX}${range}.${hideSpoilers ? "hidden" : "revealed"}`;
}

function calendarCacheKey(year: number, month: number): string {
  return `${CALENDAR_CACHE_PREFIX}${year}-${String(month).padStart(2, "0")}`;
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
  return value === "main" || value === "starred" || value === "all";
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

function followLevels(): FollowLevel[] {
  return ["main", "starred", "muted", "hidden", "explore"];
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
