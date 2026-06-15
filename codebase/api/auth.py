"""
api/auth.py — Authentication & RBAC (LLD §7)

Implements LLD §7.1 RBAC Architecture:
  Authentication Layer — User login via SSO/OAuth2/SAML/Azure AD/Okta/LDAP
  This module provides:
    - Local username/password login issuing JWTs (HS256)
    - A pluggable SSO callback endpoint stub for Azure AD / Okta / SAML /
      LDAP — providers exchange an external token for a Copilot JWT via the
      same `issue_token()` path, so the rest of the app is provider-agnostic.
    - `get_current_user` / `require_roles` FastAPI dependencies enforcing
      RBAC on protected endpoints.

Roles model:
  - `role`: single primary role (e.g. 'employee', 'admin') — coarse-grained.
  - `access_roles`: list of fine-grained RBAC roles used by the retrieval
    layer for document/collection access filtering (EMPLOYEE, MANAGER, HR,
    FINANCE, IT_ADMIN, EXECUTIVE, ...). These are embedded in the JWT and
    forwarded to `hybrid_engine.retrieve(rbac_roles=...)`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import bcrypt as _bcrypt
from pydantic import BaseModel
from sqlalchemy import create_engine, text

from configs.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


# ── Models ───────────────────────────────────────────────────────────────────
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    must_change_password: bool = False
    user: "UserOut"


class UserOut(BaseModel):
    user_id: str
    email: str
    username: str
    department: Optional[str] = None
    role: str = "employee"
    access_roles: List[str] = []


class LoginRequest(BaseModel):
    email: str
    password: str


class SSOCallbackRequest(BaseModel):
    """Generic SSO callback payload. Real integrations (Azure AD / Okta /
    SAML / LDAP) validate the external token/assertion upstream of this
    call and pass the verified identity here."""
    provider: str            # 'azure_ad' | 'okta' | 'saml' | 'ldap' | 'google'
    sso_subject: str         # external IdP subject / object id
    email: str
    username: Optional[str] = None
    department: Optional[str] = None


# ── Password hashing ────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── JWT helpers ──────────────────────────────────────────────────────────────
def issue_token(user_row: dict) -> TokenResponse:
    expiry = timedelta(hours=settings.jwt_expiry_hours)
    expire_at = datetime.now(timezone.utc) + expiry

    access_roles = user_row.get("access_roles") or ["EMPLOYEE"]
    payload = {
        "sub": str(user_row["user_id"]),
        "email": user_row["email"],
        "username": user_row["username"],
        "department": user_row.get("department"),
        "role": user_row.get("role", "employee"),
        "access_roles": access_roles,
        "exp": expire_at,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)

    return TokenResponse(
        access_token=token,
        expires_in=int(expiry.total_seconds()),
        must_change_password=bool(user_row.get("must_change_password", False)),
        user=UserOut(
            user_id=str(user_row["user_id"]),
            email=user_row["email"],
            username=user_row["username"],
            department=user_row.get("department"),
            role=user_row.get("role", "employee"),
            access_roles=access_roles,
        ),
    )


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


# ── Auth service (DB-backed) ────────────────────────────────────────────────
class AuthService:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)

    def get_user_by_email(self, email: str) -> Optional[dict]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT user_id, email, username, department, role, "
                    "password_hash, access_roles, is_active "
                    "FROM users WHERE email = :email"
                ),
                {"email": email},
            ).fetchone()
            return dict(row._mapping) if row else None

    def get_user_by_sso(self, provider: str, sso_subject: str) -> Optional[dict]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT user_id, email, username, department, role, "
                    "access_roles, is_active "
                    "FROM users WHERE sso_provider = :p AND sso_subject = :s"
                ),
                {"p": provider, "s": sso_subject},
            ).fetchone()
            return dict(row._mapping) if row else None

    def create_sso_user(
        self, provider: str, sso_subject: str, email: str,
        username: Optional[str] = None, department: Optional[str] = None,
    ) -> dict:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "INSERT INTO users (email, username, department, role, "
                    "access_roles, sso_provider, sso_subject) "
                    "VALUES (:email, :username, :department, 'employee', "
                    "ARRAY['EMPLOYEE'], :provider, :subject) "
                    "ON CONFLICT (email) DO UPDATE SET "
                    "sso_provider = :provider, sso_subject = :subject "
                    "RETURNING user_id, email, username, department, role, access_roles, is_active"
                ),
                {
                    "email": email,
                    "username": username or email.split("@")[0],
                    "department": department,
                    "provider": provider,
                    "subject": sso_subject,
                },
            ).fetchone()
            return dict(row._mapping)

    def touch_last_login(self, user_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE users SET last_login_at = NOW() WHERE user_id = :uid"),
                {"uid": user_id},
            )

    def create_user(
        self, email: str, username: str, department: Optional[str] = None,
        role: str = "employee", access_roles: Optional[List[str]] = None,
        password: Optional[str] = None,
    ) -> dict:
        access_roles = access_roles or ["EMPLOYEE"]
        pwd_hash = hash_password(password) if password else None
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "INSERT INTO users (email, username, department, role, "
                    "access_roles, password_hash, sso_provider) "
                    "VALUES (:email, :username, :department, :role, :access_roles, "
                    ":password_hash, 'local') "
                    "ON CONFLICT (email) DO NOTHING "
                    "RETURNING user_id, email, username, department, role, access_roles"
                ),
                {
                    "email": email, "username": username, "department": department,
                    "role": role, "access_roles": access_roles, "password_hash": pwd_hash,
                },
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=409, detail="User with this email already exists")
            return dict(row._mapping)

    def grant_access(
        self, user_id: str, resource_type: str, resource_name: str,
        role: str, granted_by: str,
    ) -> dict:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "INSERT INTO access_grants (user_id, resource_type, resource_name, role, granted_by) "
                    "VALUES (:uid, :rtype, :rname, :role, :by) "
                    "ON CONFLICT (user_id, resource_type, resource_name, role) DO NOTHING "
                    "RETURNING grant_id, user_id, resource_type, resource_name, role, created_at"
                ),
                {"uid": user_id, "rtype": resource_type, "rname": resource_name,
                 "role": role, "by": granted_by},
            ).fetchone()

            # Also append to the user's access_roles array for retrieval-layer filtering
            conn.execute(
                text(
                    "UPDATE users SET access_roles = "
                    "(SELECT ARRAY(SELECT DISTINCT unnest(access_roles || ARRAY[:role]))) "
                    "WHERE user_id = :uid"
                ),
                {"uid": user_id, "role": role},
            )

            if row is None:
                return {"status": "already_granted"}
            return dict(row._mapping)

    # ── Access Requests (employee-initiated, admin-approved) ────────────────────
    def create_access_request(
        self, user_id: str, resource_name: str, justification: str,
        resource_type: str = "access_role",
    ) -> dict:
        with self.engine.begin() as conn:
            # Already have it?
            has_it = conn.execute(
                text("SELECT access_roles FROM users WHERE user_id = :uid"),
                {"uid": user_id},
            ).fetchone()
            if has_it and resource_name in (has_it.access_roles or []):
                raise HTTPException(status_code=400, detail=f"You already have {resource_name} access")

            # Pending duplicate?
            pending = conn.execute(
                text(
                    "SELECT request_id FROM access_requests "
                    "WHERE user_id = :uid AND resource_name = :rname AND status = 'pending'"
                ),
                {"uid": user_id, "rname": resource_name},
            ).fetchone()
            if pending:
                raise HTTPException(status_code=400, detail="You already have a pending request for this access")

            row = conn.execute(
                text(
                    "INSERT INTO access_requests (user_id, resource_type, resource_name, justification) "
                    "VALUES (:uid, :rtype, :rname, :just) "
                    "RETURNING request_id, user_id, resource_type, resource_name, justification, "
                    "status, requested_at"
                ),
                {"uid": user_id, "rtype": resource_type, "rname": resource_name, "just": justification},
            ).fetchone()
            return dict(row._mapping)

    def list_access_requests(self, user_id: Optional[str] = None) -> List[dict]:
        """List access requests. If user_id given, scope to that user (employee
        view); otherwise return all (admin view)."""
        query = (
            "SELECT ar.request_id, ar.user_id, u.email as user_email, u.username as user_name, "
            "ar.resource_type, ar.resource_name, ar.justification, ar.status, "
            "ar.rejection_reason, ar.requested_at, ar.resolved_at, "
            "r.username as resolved_by_name "
            "FROM access_requests ar "
            "JOIN users u ON u.user_id = ar.user_id "
            "LEFT JOIN users r ON r.user_id = ar.resolved_by "
        )
        params = {}
        if user_id:
            query += "WHERE ar.user_id = :uid "
            params["uid"] = user_id
        query += "ORDER BY ar.requested_at DESC"

        with self.engine.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()
            return [dict(r._mapping) for r in rows]

    def resolve_access_request(
        self, request_id: str, approve: bool, resolved_by: str, rejection_reason: Optional[str] = None,
    ) -> dict:
        with self.engine.begin() as conn:
            req = conn.execute(
                text("SELECT * FROM access_requests WHERE request_id = :rid"),
                {"rid": request_id},
            ).fetchone()
            if req is None:
                raise HTTPException(status_code=404, detail="Access request not found")
            if req.status != "pending":
                raise HTTPException(status_code=400, detail="Request already resolved")

            new_status = "approved" if approve else "rejected"
            conn.execute(
                text(
                    "UPDATE access_requests SET status = :status, resolved_by = :by, "
                    "resolved_at = NOW(), rejection_reason = :reason WHERE request_id = :rid"
                ),
                {"status": new_status, "by": resolved_by, "reason": rejection_reason, "rid": request_id},
            )

            if approve:
                # Append to access_roles array
                conn.execute(
                    text(
                        "UPDATE users SET access_roles = "
                        "(SELECT ARRAY(SELECT DISTINCT unnest(access_roles || ARRAY[:role]))) "
                        "WHERE user_id = :uid"
                    ),
                    {"uid": req.user_id, "role": req.resource_name},
                )
                # Record the grant for audit
                conn.execute(
                    text(
                        "INSERT INTO access_grants (user_id, resource_type, resource_name, role, granted_by) "
                        "VALUES (:uid, :rtype, :rname, :role, :by) "
                        "ON CONFLICT (user_id, resource_type, resource_name, role) DO NOTHING"
                    ),
                    {"uid": req.user_id, "rtype": req.resource_type, "rname": req.resource_name,
                     "role": req.resource_name, "by": resolved_by},
                )

        return {"status": new_status}

    # ── Audit Log ─────────────────────────────────────────────────────────────
    def log_audit(
        self, user_id: Optional[str], action: str, query_text: Optional[str] = None,
        confidence: Optional[float] = None, chunks_used: Optional[int] = None,
        latency_ms: Optional[int] = None, session_id: Optional[str] = None,
    ) -> None:
        """Write an audit_logs row. Failures are swallowed (non-critical)."""
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(user_id, session_id, action, query_text, confidence, chunks_used, latency_ms) "
                        "VALUES (:uid, :sid, :action, :q, :conf, :chunks, :latency)"
                    ),
                    {"uid": user_id, "sid": session_id, "action": action, "q": query_text,
                     "conf": confidence, "chunks": chunks_used, "latency": latency_ms},
                )
        except Exception:
            pass

    def list_audit_logs(
        self, limit: int = 50, skip: int = 0, action: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> List[dict]:
        query = (
            "SELECT al.log_id, al.user_id, u.email as user_email, u.username as user_name, "
            "al.session_id, al.action, al.query_text, al.confidence, al.chunks_used, "
            "al.latency_ms, al.created_at "
            "FROM audit_logs al "
            "LEFT JOIN users u ON u.user_id = al.user_id "
            "WHERE 1=1 "
        )
        params: dict = {"limit": limit, "skip": skip}
        if action and action != "all":
            query += "AND al.action = :action "
            params["action"] = action
        if user_email and user_email != "all":
            query += "AND u.email ILIKE :email "
            params["email"] = f"%{user_email}%"
        query += "ORDER BY al.created_at DESC LIMIT :limit OFFSET :skip"

        with self.engine.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()
            return [dict(r._mapping) for r in rows]

    def audit_metrics(self, days: int = 7) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT "
                "COUNT(*) FILTER (WHERE action = 'query') as total_queries, "
                "COUNT(*) FILTER (WHERE action = 'login') as logins, "
                "COUNT(*) FILTER (WHERE action = 'access_request') as access_requests, "
                "COUNT(*) FILTER (WHERE action = 'low_confidence') as low_confidence_events "
                "FROM audit_logs WHERE created_at > NOW() - (:days || ' days')::interval"
            ), {"days": days}).fetchone()
            return dict(row._mapping)

    def list_users(self) -> List[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT user_id, email, username, department, role, "
                    "access_roles, is_active, must_change_password, last_login_at, created_at "
                    "FROM users ORDER BY created_at DESC"
                )
            ).fetchall()
            return [dict(r._mapping) for r in rows]

    def update_user_roles(
        self, user_id: str, role: str, access_roles: List[str],
    ) -> dict:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "UPDATE users SET role = :role, access_roles = :access_roles "
                    "WHERE user_id = :uid "
                    "RETURNING user_id, email, username, role, access_roles, is_active"
                ),
                {"role": role, "access_roles": access_roles, "uid": user_id},
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="User not found")
            return dict(row._mapping)

    def change_password(
        self, user_id: str, current_password: str, new_password: str,
    ) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT password_hash FROM users WHERE user_id = :uid"),
                {"uid": user_id},
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="User not found")
            if not verify_password(current_password, row.password_hash or ""):
                raise HTTPException(status_code=400, detail="Current password is incorrect")

        new_hash = hash_password(new_password)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE users SET password_hash = :hash, must_change_password = FALSE "
                    "WHERE user_id = :uid"
                ),
                {"hash": new_hash, "uid": user_id},
            )
        return {"status": "password_changed"}

    def admin_reset_password(self, user_id: str, new_password: str) -> dict:
        """Admin sets a new temp password and forces change on next login."""
        new_hash = hash_password(new_password)
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "UPDATE users SET password_hash = :hash, must_change_password = TRUE "
                    "WHERE user_id = :uid "
                    "RETURNING email, username"
                ),
                {"hash": new_hash, "uid": user_id},
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="User not found")
        return {"status": "password_reset", "temp_password": new_password,
                "email": row.email, "username": row.username}


_auth_service: Optional[AuthService] = None


def get_auth_service() -> AuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService(settings.postgres_url)
    return _auth_service


# ── FastAPI dependencies ─────────────────────────────────────────────────────
class CurrentUser(BaseModel):
    user_id: str
    email: str
    username: str
    department: Optional[str] = None
    role: str = "employee"
    access_roles: List[str] = []


async def get_current_user(token: Optional[str] = Depends(_oauth2_scheme)) -> CurrentUser:
    """
    Decode the bearer JWT and return the authenticated user.

    If no Authorization header is provided, falls back to an anonymous
    EMPLOYEE identity — this keeps existing endpoints functional during
    incremental rollout, while protected endpoints use `require_roles(...)`
    to enforce stricter access.
    """
    if not token:
        return CurrentUser(
            user_id="anonymous", email="anonymous@local", username="anonymous",
            role="employee", access_roles=["EMPLOYEE"],
        )

    payload = decode_token(token)
    return CurrentUser(
        user_id=payload["sub"],
        email=payload["email"],
        username=payload["username"],
        department=payload.get("department"),
        role=payload.get("role", "employee"),
        access_roles=payload.get("access_roles", ["EMPLOYEE"]),
    )


def require_roles(*allowed_roles: str):
    """
    FastAPI dependency factory: 403s unless the current user's `role` or any
    of their `access_roles` intersects `allowed_roles`.

    Usage:
        @app.post("/create_user", dependencies=[Depends(require_roles("IT_ADMIN", "admin"))])
    """
    allowed = {r.upper() for r in allowed_roles}

    async def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        user_roles = {user.role.upper()} | {r.upper() for r in user.access_roles}
        if not (user_roles & allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {sorted(allowed)}",
            )
        return user

    return _check