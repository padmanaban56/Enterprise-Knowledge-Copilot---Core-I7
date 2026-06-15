"""
api/bundles_help_admin.py — Bundles, Help Center & Admin endpoints (LLD §6.3/6.4)

Implements:
  6.3 Bundle Endpoints
    GET    /bundles                  — list (pinned first, then newest)
    GET    /bundles/search           — search by name (case-insensitive substring)
    GET    /bundles/{bundle_id}      — fetch single bundle
    PATCH  /bundles/{bundle_id}      — update name and/or document set
    PATCH  /bundles/{bundle_id}/pin  — pin/unpin
    POST   /bundles/{bundle_id}/apply — apply to a chat (replaces active_documents)
    DELETE /bundles/{bundle_id}      — delete

  6.4 Help Center & Admin Endpoints
    POST /issues/draft   — save/update draft (replaces attachments atomically)
    GET  /issues/draft   — retrieve latest draft for user+chat
    POST /issues/submit  — finalize issue (status='submitted', timestamped)
    POST /faq/feedback   — thumbs up/down on FAQ entry (upsert)
    POST /create_user    — admin: provision new user
    POST /grant_access   — admin: grant collection/tenant access

  6.5 Error Codes
    400 — missing/invalid params, empty name, bad payload shape
    404 — resource not found
    409 — conflict (duplicate bundle name on PATCH)
"""
from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from api.auth import CurrentUser, get_current_user, require_roles, get_auth_service
from configs.settings import get_settings

settings = get_settings()
router = APIRouter()


class BundleService:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)

    # ── GET /bundles ─────────────────────────────────────────────────────────
    def list_bundles(self, user_id: str) -> List[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT bundle_id, name, document_ids, pinned, created_at, updated_at "
                    "FROM bundles WHERE user_id = :uid "
                    "ORDER BY pinned DESC, created_at DESC"
                ),
                {"uid": user_id},
            ).fetchall()
            return [dict(r._mapping) for r in rows]

    # ── GET /bundles/search ──────────────────────────────────────────────────
    def search_bundles(self, user_id: str, q: str) -> List[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT bundle_id, name, document_ids, pinned, created_at, updated_at "
                    "FROM bundles WHERE user_id = :uid AND name ILIKE :q "
                    "ORDER BY pinned DESC, created_at DESC"
                ),
                {"uid": user_id, "q": f"%{q}%"},
            ).fetchall()
            return [dict(r._mapping) for r in rows]

    # ── GET /bundles/{id} ────────────────────────────────────────────────────
    def get_bundle(self, user_id: str, bundle_id: str) -> Optional[dict]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT bundle_id, name, document_ids, pinned, created_at, updated_at "
                    "FROM bundles WHERE bundle_id = :bid AND user_id = :uid"
                ),
                {"bid": bundle_id, "uid": user_id},
            ).fetchone()
            return dict(row._mapping) if row else None

    # ── Create (used by tests/seed; not in the LLD endpoint list but needed
    #    so PATCH/apply have something to operate on) ──────────────────────
    def create_bundle(self, user_id: str, name: str, document_ids: List[str]) -> dict:
        if not name or not name.strip():
            raise HTTPException(status_code=400, detail="Bundle name cannot be empty")
        try:
            with self.engine.begin() as conn:
                row = conn.execute(
                    text(
                        "INSERT INTO bundles (user_id, name, document_ids) "
                        "VALUES (:uid, :name, :docs) "
                        "RETURNING bundle_id, name, document_ids, pinned, created_at, updated_at"
                    ),
                    {"uid": user_id, "name": name.strip(), "docs": document_ids},
                ).fetchone()
                return dict(row._mapping)
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(status_code=409, detail="A bundle with this name already exists")
            raise

    # ── PATCH /bundles/{id} ──────────────────────────────────────────────────
    def update_bundle(
        self, user_id: str, bundle_id: str,
        name: Optional[str] = None, document_ids: Optional[List[str]] = None,
    ) -> dict:
        existing = self.get_bundle(user_id, bundle_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Bundle not found")

        if name is not None and not name.strip():
            raise HTTPException(status_code=400, detail="Bundle name cannot be empty")

        new_name = name.strip() if name is not None else existing["name"]
        new_docs = document_ids if document_ids is not None else existing["document_ids"]

        try:
            with self.engine.begin() as conn:
                row = conn.execute(
                    text(
                        "UPDATE bundles SET name = :name, document_ids = :docs, updated_at = NOW() "
                        "WHERE bundle_id = :bid AND user_id = :uid "
                        "RETURNING bundle_id, name, document_ids, pinned, created_at, updated_at"
                    ),
                    {"name": new_name, "docs": new_docs, "bid": bundle_id, "uid": user_id},
                ).fetchone()
                return dict(row._mapping)
        except HTTPException:
            raise
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(status_code=409, detail="A bundle with this name already exists")
            raise

    # ── PATCH /bundles/{id}/pin ──────────────────────────────────────────────
    def set_pin(self, user_id: str, bundle_id: str, pinned: bool) -> dict:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "UPDATE bundles SET pinned = :pinned, updated_at = NOW() "
                    "WHERE bundle_id = :bid AND user_id = :uid "
                    "RETURNING bundle_id, name, document_ids, pinned, created_at, updated_at"
                ),
                {"pinned": pinned, "bid": bundle_id, "uid": user_id},
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Bundle not found")
            return dict(row._mapping)

    # ── POST /bundles/{id}/apply ─────────────────────────────────────────────
    def apply_bundle(self, user_id: str, bundle_id: str, chat_id: str) -> dict:
        bundle = self.get_bundle(user_id, bundle_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail="Bundle not found")

        with self.engine.begin() as conn:
            # Upsert: a brand-new chat (no messages sent yet) won't have a
            # chat_sessions row yet — create one so the bundle scope can be
            # applied before the first message.
            conn.execute(
                text(
                    "INSERT INTO chat_sessions (session_id, user_id, title, active_documents) "
                    "VALUES (:cid, :uid, 'New Chat', :docs) "
                    "ON CONFLICT (session_id) DO UPDATE SET "
                    "active_documents = :docs, updated_at = NOW()"
                ),
                {"cid": chat_id, "uid": user_id, "docs": bundle["document_ids"]},
            )
        return {"chat_id": chat_id, "active_documents": bundle["document_ids"]}

    # ── DELETE /bundles/{id} ──────────────────────────────────────────────────
    def delete_bundle(self, user_id: str, bundle_id: str) -> None:
        with self.engine.begin() as conn:
            result = conn.execute(
                text("DELETE FROM bundles WHERE bundle_id = :bid AND user_id = :uid"),
                {"bid": bundle_id, "uid": user_id},
            )
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Bundle not found")


class HelpCenterService:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)

    # ── POST /issues/draft ───────────────────────────────────────────────────
    def save_draft(
        self, user_id: str, chat_id: str, title: Optional[str], description: Optional[str],
        category: Optional[str], priority: str, attachments: List[dict],
    ) -> dict:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "INSERT INTO issues (user_id, chat_id, title, description, category, "
                    "priority, attachments, status) "
                    "VALUES (:uid, :cid, :title, :desc, :cat, :prio, :attachments, 'draft') "
                    "ON CONFLICT (user_id, chat_id, status) DO UPDATE SET "
                    "title = EXCLUDED.title, description = EXCLUDED.description, "
                    "category = EXCLUDED.category, priority = EXCLUDED.priority, "
                    "attachments = EXCLUDED.attachments, updated_at = NOW() "
                    "RETURNING issue_id, user_id, chat_id, title, description, category, "
                    "priority, attachments, status, created_at, updated_at"
                ),
                {
                    "uid": user_id, "cid": chat_id, "title": title, "desc": description,
                    "cat": category, "prio": priority, "attachments": json.dumps(attachments),
                },
            ).fetchone()
            return dict(row._mapping)

    # ── GET /issues/draft ─────────────────────────────────────────────────────
    def get_draft(self, user_id: str, chat_id: str) -> Optional[dict]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT issue_id, user_id, chat_id, title, description, category, "
                    "priority, attachments, status, created_at, updated_at "
                    "FROM issues WHERE user_id = :uid AND chat_id = :cid AND status = 'draft' "
                    "ORDER BY updated_at DESC LIMIT 1"
                ),
                {"uid": user_id, "cid": chat_id},
            ).fetchone()
            return dict(row._mapping) if row else None

    # ── POST /issues/submit ───────────────────────────────────────────────────
    def submit_issue(self, user_id: str, chat_id: str) -> dict:
        draft = self.get_draft(user_id, chat_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="No draft issue found for this chat")

        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "UPDATE issues SET status = 'submitted', submitted_at = NOW(), updated_at = NOW() "
                    "WHERE issue_id = :iid "
                    "RETURNING issue_id, user_id, chat_id, title, description, category, "
                    "priority, attachments, status, submitted_at, created_at, updated_at"
                ),
                {"iid": draft["issue_id"]},
            ).fetchone()
            return dict(row._mapping)

    # ── POST /faq/feedback ────────────────────────────────────────────────────
    def faq_feedback(self, user_id: str, faq_id: str, vote: int) -> dict:
        if vote not in (1, -1):
            raise HTTPException(status_code=400, detail="vote must be 1 (thumbs_up) or -1 (thumbs_down)")
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "INSERT INTO faq_feedback (user_id, faq_id, vote) "
                    "VALUES (:uid, :fid, :vote) "
                    "ON CONFLICT (user_id, faq_id) DO UPDATE SET vote = EXCLUDED.vote, updated_at = NOW() "
                    "RETURNING faq_feedback_id, user_id, faq_id, vote, created_at, updated_at"
                ),
                {"uid": user_id, "fid": faq_id, "vote": vote},
            ).fetchone()
            return dict(row._mapping)


_bundle_service: Optional[BundleService] = None
_help_service: Optional[HelpCenterService] = None


def get_bundle_service() -> BundleService:
    global _bundle_service
    if _bundle_service is None:
        _bundle_service = BundleService(settings.postgres_url)
    return _bundle_service


def get_help_service() -> HelpCenterService:
    global _help_service
    if _help_service is None:
        _help_service = HelpCenterService(settings.postgres_url)
    return _help_service


# ════════════════════════════════════════════════════════════════════════════
# 6.3 BUNDLE ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════
class BundleUpdateRequest(BaseModel):
    name: Optional[str] = None
    document_ids: Optional[List[str]] = None


class BundlePinRequest(BaseModel):
    pinned: bool


class BundleApplyRequest(BaseModel):
    chat_id: str


class BundleCreateRequest(BaseModel):
    name: str
    document_ids: List[str] = Field(default_factory=list)


@router.get("/bundles")
async def list_bundles(user: CurrentUser = Depends(get_current_user)):
    return get_bundle_service().list_bundles(user.user_id)


@router.get("/bundles/search")
async def search_bundles(q: str, user: CurrentUser = Depends(get_current_user)):
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")
    return get_bundle_service().search_bundles(user.user_id, q.strip())


@router.post("/bundles")
async def create_bundle(req: BundleCreateRequest, user: CurrentUser = Depends(get_current_user)):
    return get_bundle_service().create_bundle(user.user_id, req.name, req.document_ids)


@router.get("/bundles/{bundle_id}")
async def get_bundle(bundle_id: str, user: CurrentUser = Depends(get_current_user)):
    bundle = get_bundle_service().get_bundle(user.user_id, bundle_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Bundle not found")
    return bundle


@router.patch("/bundles/{bundle_id}")
async def update_bundle(
    bundle_id: str, req: BundleUpdateRequest, user: CurrentUser = Depends(get_current_user),
):
    if req.name is None and req.document_ids is None:
        raise HTTPException(status_code=400, detail="At least one of 'name' or 'document_ids' is required")
    return get_bundle_service().update_bundle(user.user_id, bundle_id, req.name, req.document_ids)


@router.patch("/bundles/{bundle_id}/pin")
async def pin_bundle(
    bundle_id: str, req: BundlePinRequest, user: CurrentUser = Depends(get_current_user),
):
    return get_bundle_service().set_pin(user.user_id, bundle_id, req.pinned)


@router.post("/bundles/{bundle_id}/apply")
async def apply_bundle(
    bundle_id: str, req: BundleApplyRequest, user: CurrentUser = Depends(get_current_user),
):
    if not req.chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    return get_bundle_service().apply_bundle(user.user_id, bundle_id, req.chat_id)


@router.delete("/bundles/{bundle_id}")
async def delete_bundle(bundle_id: str, user: CurrentUser = Depends(get_current_user)):
    get_bundle_service().delete_bundle(user.user_id, bundle_id)
    return {"status": "deleted", "bundle_id": bundle_id}


# ════════════════════════════════════════════════════════════════════════════
# 6.4 HELP CENTER ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════
class IssueDraftRequest(BaseModel):
    chat_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    priority: str = "medium"
    attachments: List[dict] = Field(default_factory=list)


class IssueSubmitRequest(BaseModel):
    chat_id: str


class FAQFeedbackRequest(BaseModel):
    faq_id: str
    vote: int  # 1 = thumbs_up, -1 = thumbs_down


@router.post("/issues/draft")
async def save_issue_draft(req: IssueDraftRequest, user: CurrentUser = Depends(get_current_user)):
    if not req.chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    return get_help_service().save_draft(
        user.user_id, req.chat_id, req.title, req.description,
        req.category, req.priority, req.attachments,
    )


@router.get("/issues/draft")
async def get_issue_draft(chat_id: str, user: CurrentUser = Depends(get_current_user)):
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    draft = get_help_service().get_draft(user.user_id, chat_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="No draft found for this chat")
    return draft


@router.post("/issues/submit")
async def submit_issue(req: IssueSubmitRequest, user: CurrentUser = Depends(get_current_user)):
    if not req.chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    return get_help_service().submit_issue(user.user_id, req.chat_id)


@router.post("/faq/feedback")
async def faq_feedback(req: FAQFeedbackRequest, user: CurrentUser = Depends(get_current_user)):
    if not req.faq_id:
        raise HTTPException(status_code=400, detail="faq_id is required")
    return get_help_service().faq_feedback(user.user_id, req.faq_id, req.vote)


# ════════════════════════════════════════════════════════════════════════════
# 6.4 ADMIN ENDPOINTS — IT_ADMIN / admin role required
# ════════════════════════════════════════════════════════════════════════════
class CreateUserRequest(BaseModel):
    email: str
    username: str
    department: Optional[str] = None
    role: str = "employee"
    access_roles: List[str] = Field(default_factory=lambda: ["EMPLOYEE"])
    password: Optional[str] = None


class GrantAccessRequest(BaseModel):
    user_id: str
    resource_type: str  # 'collection' | 'tenant' | 'repository'
    resource_name: str
    role: str


@router.post("/create_user", dependencies=[Depends(require_roles("IT_ADMIN", "admin", "EXECUTIVE"))])
async def create_user(req: CreateUserRequest, user: CurrentUser = Depends(get_current_user)):
    if not req.email or not req.username:
        raise HTTPException(status_code=400, detail="email and username are required")
    return get_auth_service().create_user(
        email=req.email, username=req.username, department=req.department,
        role=req.role, access_roles=req.access_roles, password=req.password,
    )


@router.post("/grant_access", dependencies=[Depends(require_roles("IT_ADMIN", "admin", "EXECUTIVE"))])
async def grant_access(req: GrantAccessRequest, user: CurrentUser = Depends(get_current_user)):
    if not req.user_id or not req.resource_type or not req.resource_name or not req.role:
        raise HTTPException(status_code=400, detail="user_id, resource_type, resource_name and role are required")
    return get_auth_service().grant_access(
        user_id=req.user_id, resource_type=req.resource_type,
        resource_name=req.resource_name, role=req.role, granted_by=user.user_id,
    )