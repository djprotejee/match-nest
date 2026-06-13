from __future__ import annotations

import os
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .emailer import send_verification_email
from .entity_service import default_entity_color, entity_bindings_from_payload, entity_payload, provider_search_candidates
from .models import EntityKind, EventStatus, F1Session, Follow, FollowLevel, KYIV_TZ, Sport, UserAccount
from .notifications import dispatch_due_notifications, notification_settings_payload, push_config, rule_payload
from .providers.registry import fetch_event_details, fetch_events, provider_results
from .service import (
    filter_events,
    group_by_day,
    month_range,
    parse_enum_set,
    parse_level_set,
    range_for_preset,
    serialize_event,
    update_f1_sessions,
)
from .storage import (
    authenticate_user,
    create_session,
    create_custom_entity,
    create_oauth_state,
    create_user,
    consume_oauth_state,
    delete_or_hide_entity_for_user,
    delete_session,
    get_entity_record,
    get_or_create_oauth_user,
    list_entity_records,
    list_user_ids,
    preferences_for_user,
    search_entity_records,
    set_user_f1_sessions,
    set_user_follow,
    set_user_hide_spoilers,
    set_user_ui_state,
    upsert_notification_rule,
    upsert_push_subscription,
    delete_notification_rule,
    update_custom_entity,
    user_for_session,
    verify_email,
)

app = FastAPI(title="MatchNest API", version="0.1.0")


@app.on_event("startup")
def warm_default_calendar_cache_on_startup() -> None:
    # Render free instances can sleep. Warm the main calendar in the background
    # so the first user navigation does not have to trigger every provider.
    thread = threading.Thread(target=warm_default_calendar_cache, daemon=True)
    thread.start()
    notification_thread = threading.Thread(target=notification_dispatch_loop, daemon=True)
    notification_thread.start()


def warm_default_calendar_cache() -> None:
    try:
        preferences = preferences_for_user(None)
        now = datetime.now(timezone.utc)
        for start, end in warmup_ranges(now, month_count=2):
            provider_results(start, end, preferences)
            time.sleep(2)
    except Exception:
        # Cache warming is best-effort; request handlers still refresh on demand.
        return


def notification_dispatch_loop() -> None:
    while True:
        try:
            dispatch_due_notifications()
        except Exception:
            pass
        time.sleep(60)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_origin_regex=r"http://(192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}):5173",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class FollowUpdate(BaseModel):
    level: str
    notifications_enabled: bool = True
    hide_spoilers: bool = True


class F1SessionsUpdate(BaseModel):
    sessions: list[F1Session]


class AccountSettingsUpdate(BaseModel):
    f1_sessions: list[F1Session] | None = None
    hide_spoilers: bool | None = None
    ui_state: dict | None = None


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class VerifyEmailRequest(BaseModel):
    token: str


class EntityBindingPayload(BaseModel):
    provider: str
    binding_type: str
    value: str


class CustomEntityRequest(BaseModel):
    name: str
    sport: Sport
    kind: EntityKind
    color: str | None = None
    level: str = FollowLevel.STARRED.value
    aliases: list[str] = []
    bindings: list[EntityBindingPayload] = []


class EntityUpdateRequest(BaseModel):
    name: str
    sport: Sport
    kind: EntityKind
    color: str | None = None
    aliases: list[str] = []
    bindings: list[EntityBindingPayload] = []


class PushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionRequest(BaseModel):
    endpoint: str
    keys: PushSubscriptionKeys


class NotificationRuleRequest(BaseModel):
    id: str | None = None
    name: str
    enabled: bool = True
    target_type: str
    target_id: str
    minutes_before: int


def optional_user(authorization: str | None = Header(default=None)) -> UserAccount | None:
    token = bearer_token(authorization)
    return user_for_session(token) if token else None


def require_user(current_user: UserAccount | None = Depends(optional_user)) -> UserAccount:
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return current_user


def require_user_or_dispatch_token(
    authorization: str | None = Header(default=None),
    x_notification_dispatch_token: str | None = Header(default=None),
) -> UserAccount | None:
    expected_token = os.getenv("NOTIFICATION_DISPATCH_TOKEN", "").strip()
    if expected_token and x_notification_dispatch_token == expected_token:
        return None
    current_user = optional_user(authorization)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return current_user


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def user_payload(user: UserAccount) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "email_verified": user.is_email_verified,
        "created_at": user.created_at.isoformat(),
    }


def warmup_ranges(now: datetime, month_count: int = 7) -> list[tuple[datetime, datetime]]:
    ranges: list[tuple[datetime, datetime]] = []
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    ranges.append((today_start, today_start + timedelta(days=1)))
    ranges.append((today_start, today_start + timedelta(days=8)))
    for offset in range(month_count):
        month_index = now.month - 1 + offset
        year = now.year + (month_index // 12)
        month = (month_index % 12) + 1
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        ranges.append((start, end))
    return ranges


def build_verification_url(request: Request, token: str) -> str:
    public_url = os.getenv("APP_PUBLIC_URL", "").strip().rstrip("/")
    if public_url:
        return f"{public_url}/auth/verify-email?token={token}"
    return str(request.url_for("verify_email_get")).split("?")[0] + f"?token={token}"


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "timezone": "Europe/Kyiv"}


@app.get("/sources")
def sources(current_user: UserAccount | None = Depends(optional_user)) -> list[dict]:
    preferences = preferences_for_user(current_user.id if current_user else None)
    results, _ = provider_results(preferences=preferences)
    return [
        {
            "name": result.name,
            "configured": result.configured,
            "count": result.count,
            "error": result.error,
        }
        for result in results
    ]


@app.get("/entities")
def entities(current_user: UserAccount | None = Depends(optional_user)) -> list[dict]:
    preferences = preferences_for_user(current_user.id if current_user else None)
    return [
        entity_payload(record, preferences)
        for record in list_entity_records().values()
    ]


@app.get("/entities/search")
def entity_search(q: str = Query(min_length=1), current_user: UserAccount = Depends(require_user)) -> dict:
    preferences = preferences_for_user(current_user.id)
    local = [entity_payload(record, preferences, include_detail=True) for record in search_entity_records(q)]
    return {"query": q, "local": local, "candidates": provider_search_candidates(q)}


@app.get("/entities/{entity_id}")
def entity_detail(entity_id: str, current_user: UserAccount = Depends(require_user)) -> dict:
    record = get_entity_record(entity_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Entity not found.")
    return entity_payload(record, preferences_for_user(current_user.id), include_detail=True)


@app.post("/entities/custom")
def create_entity(payload: CustomEntityRequest, current_user: UserAccount = Depends(require_user)) -> dict:
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Entity name is required.")
    record = create_custom_entity(
        user_id=current_user.id,
        name=payload.name,
        sport=payload.sport,
        kind=payload.kind,
        color=payload.color or default_entity_color(payload.sport, payload.kind),
        level=payload.level,
        aliases=payload.aliases,
        bindings=entity_bindings_from_payload(payload.bindings),
    )
    return entity_payload(record, preferences_for_user(current_user.id), include_detail=True)


@app.put("/entities/{entity_id}")
def update_entity(entity_id: str, payload: EntityUpdateRequest, current_user: UserAccount = Depends(require_user)) -> dict:
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Entity name is required.")
    try:
        record = update_custom_entity(
            user_id=current_user.id,
            entity_id=entity_id,
            name=payload.name,
            sport=payload.sport,
            kind=payload.kind,
            color=payload.color or default_entity_color(payload.sport, payload.kind),
            aliases=payload.aliases,
            bindings=entity_bindings_from_payload(payload.bindings),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return entity_payload(record, preferences_for_user(current_user.id), include_detail=True)


@app.delete("/entities/{entity_id}")
def delete_entity(entity_id: str, current_user: UserAccount = Depends(require_user)) -> dict:
    try:
        result = delete_or_hide_entity_for_user(current_user.id, entity_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "entity_id": entity_id, "result": result}


@app.get("/events")
def events(
    range: str = Query(default="week", pattern="^(today|week|month)$"),
    level: str | None = None,
    sport: str | None = None,
    status: str | None = None,
    reveal_spoilers: bool = False,
    current_user: UserAccount | None = Depends(optional_user),
) -> list[dict]:
    try:
        start, end = range_for_preset(range)
        preferences = preferences_for_user(current_user.id if current_user else None)
        filtered = filter_events(
            fetch_events(start, end, preferences),
            preferences,
            start=start,
            end=end,
            levels=parse_level_set(level),
            sports=parse_enum_set(sport, Sport),
            statuses=parse_enum_set(status, EventStatus),
            apply_f1_session_filter=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [serialize_event(event, preferences, reveal_spoilers) for event in filtered]


@app.get("/events/{event_id}/details")
def event_details(event_id: str) -> dict:
    details = fetch_event_details(event_id)
    if details is None:
        raise HTTPException(status_code=404, detail="Event details are not available for this event.")
    return details


@app.get("/timeline")
def timeline(
    range: str = Query(default="week", pattern="^(today|week|month)$"),
    level: str | None = "main,starred",
    reveal_spoilers: bool = False,
    current_user: UserAccount | None = Depends(optional_user),
) -> list[dict]:
    start, end = range_for_preset(range)
    preferences = preferences_for_user(current_user.id if current_user else None)
    filtered = filter_events(
        fetch_events(start, end, preferences),
        preferences,
        start=start,
        end=end,
        levels=parse_level_set(level),
        apply_f1_session_filter=False,
    )
    return group_by_day(filtered, preferences, reveal_spoilers)


@app.get("/calendar/{year}/{month}")
def calendar_month(
    year: int,
    month: int,
    level: str | None = None,
    reveal_spoilers: bool = False,
    current_user: UserAccount | None = Depends(optional_user),
) -> list[dict]:
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be between 1 and 12.")
    start, end = month_range(year, month)
    preferences = preferences_for_user(current_user.id if current_user else None)
    filtered = filter_events(
        fetch_events(start, end, preferences),
        preferences,
        start=start,
        end=end,
        levels=parse_level_set(level),
        apply_f1_session_filter=False,
    )
    return group_by_day(filtered, preferences, reveal_spoilers)


@app.get("/widget/next")
def widget_next(current_user: UserAccount | None = Depends(optional_user)) -> dict:
    now = datetime.now(KYIV_TZ)
    preferences = preferences_for_user(current_user.id if current_user else None)
    filtered = filter_events(
        fetch_events(start=now, preferences=preferences),
        preferences,
        start=now,
        levels={FollowLevel.MAIN.value, FollowLevel.STARRED.value},
        statuses={EventStatus.LIVE, EventStatus.DELAYED, EventStatus.UPCOMING},
    )
    if not filtered:
        return {"event": None}
    return {"event": serialize_event(filtered[0], preferences)}


@app.put("/follows/{entity_id}")
def set_follow(entity_id: str, update: FollowUpdate, current_user: UserAccount = Depends(require_user)) -> dict:
    if get_entity_record(entity_id) is None:
        raise HTTPException(status_code=404, detail="Entity not found.")
    set_user_follow(
        current_user.id,
        Follow(
            entity_id=entity_id,
            level=update.level,
            notifications_enabled=update.notifications_enabled,
            hide_spoilers=update.hide_spoilers,
        ),
    )
    return {"ok": True, "entity_id": entity_id, "level": update.level}


@app.put("/settings/f1-sessions")
def set_f1_sessions(update: F1SessionsUpdate, current_user: UserAccount = Depends(require_user)) -> dict:
    preferences = preferences_for_user(current_user.id)
    update_f1_sessions(preferences, [session.value for session in update.sessions])
    set_user_f1_sessions(current_user.id, preferences.f1_sessions)
    return {"ok": True, "sessions": [session.value for session in preferences.f1_sessions]}


@app.get("/settings")
def account_settings(current_user: UserAccount = Depends(require_user)) -> dict:
    preferences = preferences_for_user(current_user.id)
    return {
        "f1_sessions": [session.value for session in preferences.f1_sessions],
        "hide_spoilers": preferences.default_hide_spoilers,
        "ui_state": preferences.ui_state,
    }


@app.put("/settings")
def set_account_settings(update: AccountSettingsUpdate, current_user: UserAccount = Depends(require_user)) -> dict:
    preferences = preferences_for_user(current_user.id)
    if update.f1_sessions is not None:
        update_f1_sessions(preferences, [session.value for session in update.f1_sessions])
        set_user_f1_sessions(current_user.id, preferences.f1_sessions)
    if update.ui_state is not None:
        set_user_ui_state(current_user.id, update.ui_state)
    if update.hide_spoilers is not None:
        set_user_hide_spoilers(current_user.id, update.hide_spoilers)
        ui_state = dict(update.ui_state or preferences.ui_state)
        ui_state["hideSpoilers"] = update.hide_spoilers
        set_user_ui_state(current_user.id, ui_state)
    next_preferences = preferences_for_user(current_user.id)
    return {
        "ok": True,
        "f1_sessions": [session.value for session in next_preferences.f1_sessions],
        "hide_spoilers": next_preferences.default_hide_spoilers,
        "ui_state": next_preferences.ui_state,
    }


@app.get("/notifications")
def notification_settings(current_user: UserAccount = Depends(require_user)) -> dict:
    return notification_settings_payload(current_user.id)


@app.post("/notifications/subscriptions")
def save_push_subscription(
    payload: PushSubscriptionRequest,
    request: Request,
    current_user: UserAccount = Depends(require_user),
) -> dict:
    if not payload.endpoint.strip():
        raise HTTPException(status_code=400, detail="Push endpoint is required.")
    subscription = upsert_push_subscription(
        current_user.id,
        endpoint=payload.endpoint,
        p256dh=payload.keys.p256dh,
        auth=payload.keys.auth,
        user_agent=request.headers.get("user-agent"),
    )
    return {"ok": True, "subscription_id": subscription.id, "push": push_config()}


@app.put("/notifications/rules")
def save_notification_rule(payload: NotificationRuleRequest, current_user: UserAccount = Depends(require_user)) -> dict:
    if payload.target_type not in {"sport", "category", "entity"}:
        raise HTTPException(status_code=400, detail="target_type must be sport, category, or entity.")
    if payload.minutes_before < 0:
        raise HTTPException(status_code=400, detail="minutes_before must be zero or greater.")
    rule = upsert_notification_rule(
        current_user.id,
        rule_id=payload.id,
        name=payload.name,
        enabled=payload.enabled,
        target_type=payload.target_type,
        target_id=payload.target_id,
        minutes_before=payload.minutes_before,
    )
    return {"ok": True, "rule": rule_payload(rule)}


@app.delete("/notifications/rules/{rule_id}")
def remove_notification_rule(rule_id: str, current_user: UserAccount = Depends(require_user)) -> dict:
    delete_notification_rule(current_user.id, rule_id)
    return {"ok": True}


@app.post("/notifications/dispatch")
def run_notifications(_actor: UserAccount | None = Depends(require_user_or_dispatch_token)) -> dict:
    # Manual/cron trigger is useful on free hosting where the service may sleep.
    return dispatch_due_notifications()


@app.post("/background/refresh")
def background_refresh(_actor: UserAccount | None = Depends(require_user_or_dispatch_token)) -> dict:
    now = datetime.now(timezone.utc)
    warmed: list[str] = []
    errors: list[str] = []
    user_ids = [None]
    # Warm all signed-in preference sets so every device for the same account
    # reads persisted events/settings instead of starting a provider refresh.
    try:
        user_ids.extend(list_user_ids())
    except Exception as exc:
        errors.append(f"users: {exc}")

    for user_id in user_ids:
        preferences = preferences_for_user(user_id)
        for start, end in warmup_ranges(now):
            try:
                provider_results(start, end, preferences)
                warmed.append(f"{user_id or 'default'}:{start.date()}:{end.date()}")
            except Exception as exc:
                errors.append(f"{user_id or 'default'}:{start.date()}:{exc}")
    notification_result = dispatch_due_notifications()
    return {"ok": not errors, "warmed": warmed, "errors": errors, "notifications": notification_result}


@app.post("/auth/register")
def register(payload: RegisterRequest, request: Request) -> dict:
    email = payload.email.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    try:
        user, verification_token = create_user(email, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    verification_url = build_verification_url(request, verification_token)
    delivery = send_verification_email(user.email, verification_url)
    response = {"ok": True, "user": user_payload(user), "email_delivery": delivery}
    if delivery["delivery"] == "dev-log":
        response["verification_url"] = verification_url
    return response


@app.post("/auth/verify-email")
def verify_email_post(payload: VerifyEmailRequest) -> dict:
    user = verify_email(payload.token)
    if user is None:
        raise HTTPException(status_code=400, detail="Verification link is invalid or expired.")
    token = create_session(user.id)
    return {"ok": True, "token": token, "user": user_payload(user)}


@app.get("/auth/verify-email")
def verify_email_get(token: str) -> dict:
    user = verify_email(token)
    if user is None:
        raise HTTPException(status_code=400, detail="Verification link is invalid or expired.")
    session_token = create_session(user.id)
    return {"ok": True, "token": session_token, "user": user_payload(user)}


@app.post("/auth/login")
def login(payload: LoginRequest) -> dict:
    user = authenticate_user(payload.email, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    if not user.is_email_verified:
        raise HTTPException(status_code=403, detail="Verify your email before logging in.")
    token = create_session(user.id)
    return {"ok": True, "token": token, "user": user_payload(user)}


@app.get("/auth/me")
def me(current_user: UserAccount = Depends(require_user)) -> dict:
    return {"user": user_payload(current_user)}


@app.post("/auth/logout")
def logout(authorization: str | None = Header(default=None)) -> dict:
    token = bearer_token(authorization)
    if token:
        delete_session(token)
    return {"ok": True}


@app.get("/auth/google/start")
def google_start(request: Request) -> dict:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        raise HTTPException(status_code=503, detail="Google login is not configured.")
    state = create_oauth_state("google")
    redirect_uri = google_redirect_uri(request)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    }
    return {"url": f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"}


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str, state: str) -> RedirectResponse:
    if not consume_oauth_state("google", state):
        raise HTTPException(status_code=400, detail="OAuth state is invalid or expired.")
    token_payload = exchange_google_code(code, google_redirect_uri(request))
    userinfo = fetch_google_userinfo(str(token_payload["access_token"]))
    if not userinfo.get("email") or not userinfo.get("sub"):
        raise HTTPException(status_code=400, detail="Google account did not return email identity.")
    user = get_or_create_oauth_user("google", str(userinfo["sub"]), str(userinfo["email"]))
    session_token = create_session(user.id)
    return RedirectResponse(f"{frontend_public_url(request)}?auth_token={session_token}")


def google_redirect_uri(request: Request) -> str:
    configured = os.getenv("GOOGLE_REDIRECT_URI", "").strip()
    if configured:
        return configured
    return str(request.url_for("google_callback"))


def frontend_public_url(request: Request) -> str:
    return os.getenv("APP_PUBLIC_URL", "").strip().rstrip("/") or str(request.base_url).rstrip("/")


def exchange_google_code(code: str, redirect_uri: str) -> dict:
    payload = urlencode(
        {
            "code": code,
            "client_id": os.getenv("GOOGLE_CLIENT_ID", "").strip(),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    request = UrlRequest(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_google_userinfo(access_token: str) -> dict:
    request = UrlRequest(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")
    app.mount("/icons", StaticFiles(directory=WEB_DIST / "icons"), name="icons")


@app.get("/{full_path:path}", include_in_schema=False)
def serve_pwa(full_path: str) -> FileResponse:
    requested = WEB_DIST / full_path
    if WEB_DIST.exists() and requested.is_file():
        return FileResponse(requested)
    index = WEB_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Web app is not built. Run npm run build in web/.")
