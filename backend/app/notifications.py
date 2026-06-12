from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import Event, EventStatus, NotificationRule, Sport
from .service import effective_event_status, visible_follow_level
from .storage import (
    disable_push_subscription,
    list_all_notification_rules,
    list_events,
    list_notification_rules,
    list_push_subscriptions,
    mark_notification_sent,
    preferences_for_user,
    sent_notification_exists,
)

try:
    from pywebpush import WebPushException, webpush
except ImportError:  # pragma: no cover - production dependency, optional locally.
    WebPushException = Exception
    webpush = None


VAPID_PUBLIC_KEY_ENV = "VAPID_PUBLIC_KEY"
VAPID_PRIVATE_KEY_ENV = "VAPID_PRIVATE_KEY"
VAPID_SUBJECT_ENV = "VAPID_SUBJECT"


def push_config() -> dict[str, Any]:
    public_key = os.getenv(VAPID_PUBLIC_KEY_ENV, "").strip()
    private_key = os.getenv(VAPID_PRIVATE_KEY_ENV, "").strip()
    return {
        "configured": bool(public_key and private_key and webpush is not None),
        "public_key": public_key,
        "missing": [
            name
            for name, value in [
                (VAPID_PUBLIC_KEY_ENV, public_key),
                (VAPID_PRIVATE_KEY_ENV, private_key),
                ("pywebpush", "installed" if webpush is not None else ""),
            ]
            if not value
        ],
    }


def rule_payload(rule: NotificationRule) -> dict[str, Any]:
    payload = asdict(rule)
    payload["created_at"] = rule.created_at.isoformat()
    payload["updated_at"] = rule.updated_at.isoformat()
    return payload


def subscription_payloads(user_id: int) -> list[dict[str, Any]]:
    return [
        {
            "id": subscription.id,
            "endpoint": subscription.endpoint,
            "enabled": subscription.enabled,
            "user_agent": subscription.user_agent,
            "updated_at": subscription.updated_at.isoformat(),
        }
        for subscription in list_push_subscriptions(user_id=user_id, enabled_only=False)
    ]


def notification_settings_payload(user_id: int) -> dict[str, Any]:
    return {
        "push": push_config(),
        "subscriptions": subscription_payloads(user_id),
        "rules": [rule_payload(rule) for rule in list_notification_rules(user_id)],
    }


def dispatch_due_notifications(now: datetime | None = None) -> dict[str, int]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rules = list_all_notification_rules(enabled_only=True)
    if not rules:
        return {"matched": 0, "sent": 0, "skipped": 0, "failed": 0}

    max_offset = max(rule.minutes_before for rule in rules)
    events = list_events(current - timedelta(minutes=5), current + timedelta(minutes=max_offset + 5))
    matched = sent = skipped = failed = 0

    for rule in rules:
        preferences = preferences_for_user(rule.user_id)
        subscriptions = list_push_subscriptions(user_id=rule.user_id, enabled_only=True)
        if not subscriptions:
            skipped += 1
            continue
        for event in events:
            if not notification_rule_matches(rule, event, preferences):
                continue
            if event.starts_at is None:
                continue
            scheduled_for = event.starts_at.astimezone(timezone.utc) - timedelta(minutes=rule.minutes_before)
            if scheduled_for > current or current - scheduled_for > timedelta(minutes=10):
                continue
            matched += 1
            if sent_notification_exists(rule.user_id, event.id, rule.id, scheduled_for):
                skipped += 1
                continue
            payload = notification_payload(rule, event)
            if send_to_user(rule.user_id, payload):
                mark_notification_sent(rule.user_id, event.id, rule.id, scheduled_for)
                sent += 1
            else:
                failed += 1
    return {"matched": matched, "sent": sent, "skipped": skipped, "failed": failed}


def notification_rule_matches(rule: NotificationRule, event: Event, preferences) -> bool:
    if effective_event_status(event) not in {EventStatus.UPCOMING, EventStatus.DELAYED}:
        return False
    if visible_follow_level(event, preferences) == "hidden":
        return False
    if rule.target_type == "sport":
        return event.sport.value == rule.target_id
    if rule.target_type == "category":
        return visible_follow_level(event, preferences) == rule.target_id
    if rule.target_type == "entity":
        return rule.target_id in event.entity_ids
    return False


def notification_payload(rule: NotificationRule, event: Event) -> dict[str, Any]:
    starts_at = event.starts_at.astimezone(timezone.utc).isoformat() if event.starts_at else None
    return {
        "title": f"{event.title}",
        "body": notification_body(rule, event),
        "url": f"/?event={event.id}",
        "event_id": event.id,
        "starts_at": starts_at,
        "tag": f"matchnest:{event.id}:{rule.id}",
    }


def notification_body(rule: NotificationRule, event: Event) -> str:
    parts = []
    if rule.minutes_before:
        parts.append(f"in {rule.minutes_before} min")
    if event.competition:
        parts.append(event.competition)
    parts.append(sport_label(event.sport))
    return " - ".join(parts)


def send_to_user(user_id: int, payload: dict[str, Any]) -> bool:
    if not push_config()["configured"]:
        return False
    subscriptions = list_push_subscriptions(user_id=user_id, enabled_only=True)
    ok = False
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=json.dumps(payload),
                vapid_private_key=os.getenv(VAPID_PRIVATE_KEY_ENV, "").strip(),
                vapid_claims={"sub": os.getenv(VAPID_SUBJECT_ENV, "mailto:matchnest@example.com").strip()},
            )
            ok = True
        except WebPushException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {404, 410}:
                disable_push_subscription(subscription.endpoint)
    return ok


def sport_label(sport: Sport) -> str:
    if sport == Sport.FORMULA:
        return "F1"
    if sport == Sport.CS2:
        return "CS2"
    return "Football"
