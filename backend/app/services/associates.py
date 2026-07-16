from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Associate


def get_or_create_associate(
    session: Session,
    *,
    email: str,
    name: str,
) -> Associate:
    normalized = email.strip().lower()
    associate = session.scalar(select(Associate).where(Associate.email == normalized))
    if associate is not None:
        if name and associate.name != name:
            associate.name = name
            session.commit()
            session.refresh(associate)
        return associate

    associate = Associate(name=name.strip() or normalized, email=normalized)
    session.add(associate)
    session.commit()
    session.refresh(associate)
    return associate


def get_associate_by_email(session: Session, email: str) -> Associate | None:
    return session.scalar(
        select(Associate).where(Associate.email == email.strip().lower())
    )
