"""
Auth for this app's two independent, non-stacking tiers:

- The one admin (Binit) -- a single password, gates Financials only.
- A shared floor PIN -- gates Wall Builder, Packer Scan, and Shipments for
  a Render deployment reachable from the public internet. Never required
  to reach /login or Financials, and the admin password is never required
  to reach the floor screens; these are two separate gates on two
  separate zones, not layers of one gate.

Both store their secret as a salted hash in an env var and use a DB-backed
session so a login survives a server restart. require_admin and
require_floor_access are the reusable pieces: any route adds
`Depends(require_admin)` or `Depends(require_floor_access)` and gets the
same cookie-checked, expiry-checked gate /auth/me and /auth/floor-me use.
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response
from sqlmodel import Session, select

from .database import get_session
from .models import AdminSession, FloorSession, PinAttempt

COOKIE_NAME = "session"
SESSION_LIFETIME = timedelta(days=30)
PBKDF2_ITERATIONS = 200_000

# Same presence-of-env-var signal app/database.py and app/storage.py use to
# switch backends: DATABASE_URL is only ever set for the Render deployment,
# which terminates TLS automatically, never for Binit's LAN deployment
# (SQLite, no TLS in front of it at all). A Secure cookie set from a plain
# HTTP LAN connection would just get silently dropped by the browser, so
# this can't be hardcoded True -- it has to track which deployment is
# actually running.
_COOKIES_SECURE = bool(os.environ.get("DATABASE_URL"))

FLOOR_COOKIE_NAME = "floor_session"
FLOOR_SESSION_LIFETIME = timedelta(hours=18)  # roughly one shift
# Exponential backoff on wrong floor-PIN attempts, capped so it never
# becomes a de facto hard lockout: 2s, 4s, 8s, 16s, 32s, 60s, 60s, ...
PIN_BACKOFF_CAP_SECONDS = 60


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """Returns `<salt_hex>:<hash_hex>` -- the format stored in
    ADMIN_PASSWORD_HASH. Pass no salt to generate a new one (for setting a
    password); pass the stored salt back in to verify one."""
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, _, _expected_hash_hex = stored.partition(":")
    if not salt_hex:
        return False
    candidate = hash_password(password, salt=bytes.fromhex(salt_hex))
    return secrets.compare_digest(candidate, stored)


def create_session(session: Session) -> AdminSession:
    now = datetime.utcnow()
    admin_session = AdminSession(
        token=secrets.token_urlsafe(32),
        created_at=now,
        expires_at=now + SESSION_LIFETIME,
    )
    session.add(admin_session)
    session.commit()
    session.refresh(admin_session)
    return admin_session


def delete_session(session: Session, token: str) -> None:
    admin_session = session.get(AdminSession, token)
    if admin_session is not None:
        session.delete(admin_session)
        session.commit()


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=_COOKIES_SECURE,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def require_admin(
    request: Request, session: Session = Depends(get_session),
) -> AdminSession:
    token = request.cookies.get(COOKIE_NAME)
    if token is None:
        raise HTTPException(status_code=401, detail="Not logged in")

    admin_session = session.get(AdminSession, token)
    if admin_session is None or admin_session.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    return admin_session


def get_admin_password_hash() -> str:
    stored = os.environ.get("ADMIN_PASSWORD_HASH")
    if not stored:
        raise HTTPException(
            status_code=500,
            detail="ADMIN_PASSWORD_HASH is not set -- run scripts/set_admin_password.py",
        )
    return stored


def client_ip(request: Request) -> str:
    """The real client address, even behind Render's edge proxy (which sets
    X-Forwarded-For to the actual visitor, not Render's own internal hop).
    Falls back to the raw connection address for local/LAN use, where
    there's no proxy in front of the backend to set that header at all."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_floor_session(session: Session) -> FloorSession:
    now = datetime.utcnow()
    floor_session = FloorSession(
        token=secrets.token_urlsafe(32),
        created_at=now,
        expires_at=now + FLOOR_SESSION_LIFETIME,
    )
    session.add(floor_session)
    session.commit()
    session.refresh(floor_session)
    return floor_session


def set_floor_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        FLOOR_COOKIE_NAME, token,
        max_age=int(FLOOR_SESSION_LIFETIME.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=_COOKIES_SECURE,
    )


def require_floor_access(
    request: Request, session: Session = Depends(get_session),
) -> FloorSession:
    token = request.cookies.get(FLOOR_COOKIE_NAME)
    if token is None:
        raise HTTPException(status_code=401, detail="Floor PIN not entered")

    floor_session = session.get(FloorSession, token)
    if floor_session is None or floor_session.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Floor session expired or invalid")

    return floor_session


def get_floor_pin_hash() -> str:
    stored = os.environ.get("FLOOR_PIN_HASH")
    if not stored:
        raise HTTPException(
            status_code=500,
            detail="FLOOR_PIN_HASH is not set -- run scripts/set_floor_pin.py",
        )
    return stored


def verify_floor_pin(session: Session, ip_address: str, pin: str) -> bool:
    """True only on a correct PIN submitted outside the current backoff
    window. Every other case -- wrong PIN, or a guess submitted too soon
    after the last one -- returns False identically, so the caller's
    generic "Incorrect PIN" response never reveals which one happened,
    and a throttled guess never even reaches the real password check."""
    now = datetime.utcnow()
    attempt = session.get(PinAttempt, ip_address)

    if attempt is not None:
        delay = timedelta(seconds=min(2 ** attempt.failure_count, PIN_BACKOFF_CAP_SECONDS))
        if now - attempt.last_attempt_at < delay:
            return False

    if verify_password(pin, get_floor_pin_hash()):
        if attempt is not None:
            session.delete(attempt)
            session.commit()
        return True

    if attempt is None:
        attempt = PinAttempt(ip_address=ip_address, failure_count=1, last_attempt_at=now)
    else:
        attempt.failure_count += 1
        attempt.last_attempt_at = now
    session.add(attempt)
    session.commit()
    return False
