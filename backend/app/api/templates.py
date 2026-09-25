"""Template catalogue: the built-in six, plus whatever the workspace adds."""

import re
import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import or_, select

from app import schemas
from app.core.deps import CurrentUser, DbSession
from app.db.models import DocumentTemplate
from app.services import audit

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[schemas.TemplateOut])
async def list_templates(user: CurrentUser, db: DbSession) -> list[DocumentTemplate]:
    result = await db.execute(
        select(DocumentTemplate)
        .where(
            or_(
                DocumentTemplate.is_builtin.is_(True),
                DocumentTemplate.owner_id == user.id,
            )
        )
        .order_by(DocumentTemplate.is_builtin.desc(), DocumentTemplate.name)
    )
    return list(result.scalars().all())


@router.post("", response_model=schemas.TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: schemas.TemplateCreate, user: CurrentUser, db: DbSession
) -> DocumentTemplate:
    slug = re.sub(r"[^a-z0-9]+", "_", payload.name.lower()).strip("_") or "template"
    template = DocumentTemplate(
        owner_id=user.id,
        key=f"{slug}_{uuid.uuid4().hex[:6]}",
        name=payload.name,
        standard=payload.standard,
        description=payload.description,
        sections=[s.model_dump() for s in payload.sections],
        is_builtin=False,
    )
    db.add(template)
    await db.flush()
    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.TEMPLATE_CREATE,
        subject_type="template",
        subject_id=template.id,
        payload={"name": template.name, "key": template.key},
    )
    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_template(template_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    template = (
        await db.execute(select(DocumentTemplate).where(DocumentTemplate.id == template_id))
    ).scalar_one_or_none()
    if template is None or template.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    await audit.record_audit(
        db,
        org_id=user.org_id,
        actor_id=user.id,
        action=audit.TEMPLATE_DELETE,
        subject_type="template",
        subject_id=template.id,
        payload={"name": template.name, "key": template.key},
    )
    await db.delete(template)
    await db.commit()
