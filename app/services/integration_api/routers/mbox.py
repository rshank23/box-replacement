from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.libs.common import mbox
from app.libs.common.config import Settings, get_settings
from app.libs.common.security import Principal, current_principal

from ..schemas import MBoxEntryOut

router = APIRouter()


@router.get("/browse", response_model=list[MBoxEntryOut], summary="Browse the MBox archive tree")
async def browse(
    path: str | None = Query(
        default=None,
        max_length=1000,
        description="Relative folder path; takes precedence over study/country/site",
    ),
    root: str = Query(default="source", pattern="^(source|destination|unclassified)$"),
    study: str | None = Query(default=None, max_length=200),
    country: str | None = Query(default=None, max_length=200),
    site: str | None = Query(default=None, max_length=200),
    settings: Settings = Depends(get_settings),
    _: Principal = Depends(current_principal),
) -> list[MBoxEntryOut]:
    try:
        if path is not None or root != "source":
            entries = mbox.browse_path(path or "", settings, root)
        else:
            entries = mbox.browse(study, country, site, settings)
    except mbox.MBoxPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return [MBoxEntryOut(**e.__dict__) for e in entries]
