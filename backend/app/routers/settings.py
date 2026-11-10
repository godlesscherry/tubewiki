"""Runtime settings endpoints: LLM configuration and output directory."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import settings
from ..schemas import SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings() -> dict:
    return settings.public()


@router.put("")
def update_settings(payload: SettingsUpdate) -> dict:
    try:
        return settings.update(payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/test-llm")
async def test_llm() -> dict:
    return await settings.test_llm()
