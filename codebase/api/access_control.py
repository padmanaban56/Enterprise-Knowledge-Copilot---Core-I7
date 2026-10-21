"""Shared document visibility rules for listings and dashboard aggregates."""
from fastapi import HTTPException
from uuid import UUID

ADMIN_ROLES = {'ADMIN', 'IT_ADMIN', 'EXECUTIVE'}
REPOSITORY_ROLE_MAP = {'HR': ['HR'], 'IT': ['IT'], 'Engineering': ['ENGINEERING'],
                       'Projects': ['PROJECTS'], 'Finance': ['FINANCE'], 'External': ['EXTERNAL']}


def repository_roles(repository):
    return list(REPOSITORY_ROLE_MAP.get(repository, ['ADMIN']))


def allowed_repositories(roles):
    normalized = {str(role).upper() for role in (roles or [])}
    if ADMIN_ROLES.intersection(normalized):
        return list(REPOSITORY_ROLE_MAP)
    return [name for name, required in REPOSITORY_ROLE_MAP.items() if normalized.intersection(required)]


def payload_visible(payload, roles):
    normalized = {str(role).upper() for role in (roles or [])}
    if normalized.intersection(ADMIN_ROLES):
        return True
    if payload.get('doc_type') == 'Ticket':
        return bool(payload.get('category')) and ticket_visible(payload, roles)
    if str(payload.get('doc_id') or '').lower() in granted_document_ids(roles):
        return True
    repository = payload.get('repository') or payload.get('department')
    required = {str(role).upper() for role in (payload.get('access_roles') or [])}
    return repository in allowed_repositories(roles) and (not required or bool(required.intersection(normalized)))


def granted_document_ids(roles):
    ids = []
    for role in roles or []:
        if str(role).upper().startswith('FILE:'):
            try:
                ids.append(str(UUID(str(role)[5:])))
            except ValueError:
                pass
    return ids


def ticket_visible(ticket, roles):
    return ticket_repository(ticket.get('category')) in allowed_repositories(roles)


def ticket_repository(category):
    category = str(category or '').strip().lower()
    repository = {'hr':'HR','payroll':'HR','leave':'HR','human resources':'HR',
                  'finance':'Finance','billing':'Finance','engineering':'Engineering',
                  'projects':'Projects','project':'Projects'}.get(category, 'IT')
    return repository


# Operations/security and legacy technical categories belong to IT.
TICKET_REPOSITORY_SQL = """(CASE LOWER(TRIM(category))
    WHEN 'hr' THEN 'HR' WHEN 'payroll' THEN 'HR' WHEN 'leave' THEN 'HR'
    WHEN 'human resources' THEN 'HR' WHEN 'finance' THEN 'Finance'
    WHEN 'billing' THEN 'Finance' WHEN 'engineering' THEN 'Engineering'
    WHEN 'projects' THEN 'Projects' WHEN 'project' THEN 'Projects'
    ELSE 'IT' END)"""

TICKET_VISIBLE = f"(:is_admin OR {TICKET_REPOSITORY_SQL} = ANY(CAST(:repositories AS text[])))"


def signed_in_roles(user):
    if not user.user_id or user.user_id == 'anonymous':
        raise HTTPException(401, 'Authentication required')
    return sorted({str(role).upper() for role in (user.access_roles or ['EMPLOYEE'])}
                  | {str(user.role or '').upper()})


def access_parameters(roles):
    normalized = sorted({str(role).upper() for role in (roles or ['EMPLOYEE'])})
    return {'roles': normalized, 'is_admin': bool(ADMIN_ROLES.intersection(normalized)),
            'repositories': allowed_repositories(normalized),
            'granted_ids': granted_document_ids(normalized)}


DOCUMENT_VISIBLE = """(:is_admin OR d.doc_id::text = ANY(CAST(:granted_ids AS text[])) OR ((COALESCE(cardinality(d.access_roles), 0) = 0
    OR EXISTS (SELECT 1 FROM unnest(d.access_roles) AS allowed(role)
               WHERE UPPER(allowed.role) = ANY(CAST(:roles AS text[]))))
    AND EXISTS (SELECT 1 FROM repositories repo_acl
        WHERE repo_acl.repository_id=d.repository_id
          AND repo_acl.name = ANY(CAST(:repositories AS text[])))))"""

REPOSITORY_VISIBLE = """(:is_admin OR r.name = ANY(CAST(:repositories AS text[]))
    OR EXISTS (SELECT 1 FROM documents granted_doc WHERE granted_doc.repository_id=r.repository_id
        AND granted_doc.doc_id::text = ANY(CAST(:granted_ids AS text[]))))"""
