"""Admin key management. The plaintext key appears exactly once (creation);
deletion is a soft disable so audit history survives."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from kashi_server.api.deps import get_db, require_key
from kashi_server.api.schemas import KeyCreatedOut, KeyCreateIn, KeyOut
from kashi_server.auth import generate_key, hash_key
from kashi_server.db.models import ApiKey

router = APIRouter(prefix="/v1/admin", dependencies=[Depends(require_key("admin"))])


@router.post("/keys", status_code=201, response_model=KeyCreatedOut)
def create_key(body: KeyCreateIn, db: Session = Depends(get_db)):
    raw = generate_key()
    key = ApiKey(key_hash=hash_key(raw), name=body.name, role=body.role)
    db.add(key)
    db.flush()
    db.refresh(key)  # populate server_default columns (id/disabled/created_at)
    return KeyCreatedOut(
        id=key.id,
        name=key.name,
        role=key.role,
        disabled=key.disabled,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        key=raw,
    )


@router.get("/keys", response_model=list[KeyOut])
def list_keys(db: Session = Depends(get_db)):
    return db.scalars(select(ApiKey).order_by(ApiKey.created_at)).all()


@router.delete("/keys/{key_id}", status_code=204)
def disable_key(key_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    key = db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="not_found")
    key.disabled = True
