from __future__ import annotations

import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path


DEV_EMAIL_LOG = Path(__file__).resolve().parents[1] / ".data" / "dev-emails.log"


def send_verification_email(email: str, verification_url: str) -> dict:
    """Send a verification email, or write a local dev email when SMTP is not configured."""
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    if not smtp_host:
        write_dev_email(email, verification_url)
        return {"delivery": "dev-log", "path": str(DEV_EMAIL_LOG)}

    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", smtp_username or "matchnest@localhost").strip()

    message = EmailMessage()
    message["Subject"] = "Verify your MatchNest account"
    message["From"] = smtp_from
    message["To"] = email
    message.set_content(
        "Welcome to MatchNest.\n\n"
        "Open this link to verify your email address:\n"
        f"{verification_url}\n\n"
        "If you did not create this account, ignore this email."
    )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
        smtp.starttls()
        if smtp_username:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)

    return {"delivery": "smtp", "path": None}


def write_dev_email(email: str, verification_url: str) -> None:
    DEV_EMAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
    DEV_EMAIL_LOG.write_text(
        (
            DEV_EMAIL_LOG.read_text(encoding="utf-8") if DEV_EMAIL_LOG.exists() else ""
        )
        + f"\n[{datetime.now(timezone.utc).isoformat()}] Verify {email}: {verification_url}\n",
        encoding="utf-8",
    )
