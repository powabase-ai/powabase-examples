"""Shared FastAPI dependencies (DB pool + Powabase client from app.state)."""

from fastapi import HTTPException, Request, status

from ..db import Database
from ..powabase import PowabaseClient


def get_db(request: Request) -> Database:
    db = request.app.state.db
    if db is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "database not configured"
        )
    return db


def get_powabase(request: Request) -> PowabaseClient:
    pb = request.app.state.powabase
    if pb is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "powabase client not configured"
        )
    return pb
