"""
Auth for the one admin this app will ever have (Binit). Not a user system --
there's a single password, stored as a salted hash in the ADMIN_PASSWORD_HASH
env var, and a DB-backed session so a login survives a server restart.

require_admin is the reusable piece: any future admin-only route (the
financial view this was built for) adds `Depends(require_admin)` and gets
the exact same cookie-checked, expiry-checked gate as /auth/me does here.
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response
from sqlmodel import Session, select

from .database import get_session
from .models import AdminSession

COOKIE_NAME = "session"
SESSION_LIFETIME = timedelta(days=30)
PBKDF2_ITERATIONS = 200_000


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
        secure=False,  # no TLS anywhere in this app yet -- floor-machine deployment
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
