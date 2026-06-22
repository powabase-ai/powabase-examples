"""Article-type templates."""

from fastapi import APIRouter, Depends

from ..db import Database
from ..models.template import ContentTemplate
from ..services import templates as svc
from .deps import get_db

router = APIRouter(prefix="/api/templates", tags=["templates"])


@router.get("", response_model=list[ContentTemplate])
def list_templates(db: Database = Depends(get_db)):
    return svc.list_templates(db)
