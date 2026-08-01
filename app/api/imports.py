"""Ручки импорта выгрузки и догрузки курсов."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_db
from app.models import Transaction, User
from app.schemas import BackfillOut, ImportReportOut
from app.services.fx import FxService
from app.services.importer import import_csv
from app.services.zen_csv import ZenCsvError

router = APIRouter(prefix="/api", tags=["import"])

#: защита от заливки чего-то постороннего
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@router.post("/import/csv", response_model=ImportReportOut)
async def upload_csv(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ImportReportOut:
    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файл пустой")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Файл слишком большой")

    try:
        report = import_csv(db, user, file.filename or "import.csv", content)
    except ZenCsvError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return ImportReportOut(**vars(report))


@router.post("/fx/backfill", response_model=BackfillOut)
def backfill_rates(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> BackfillOut:
    filled = FxService(db).backfill(user_id=user.id)
    pending_left = (
        db.scalar(
            select(func.count(Transaction.id)).where(
                Transaction.user_id == user.id,
                Transaction.amount_base.is_(None),
            )
        )
        or 0
    )
    return BackfillOut(filled=filled, pending_left=pending_left)
