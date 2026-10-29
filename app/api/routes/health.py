from fastapi import APIRouter, HTTPException

from app import __version__
from app.api.deps import ConnDep, SettingsDep
from app.api.schemas import HealthOut, ReadyOut

router = APIRouter(tags=["ops"])


@router.get("/health", response_model=HealthOut, summary="Liveness: the process is up")
def health() -> HealthOut:
    return HealthOut(status="ok", version=__version__)


@router.get("/ready", response_model=ReadyOut, summary="Readiness: dependencies are reachable")
def ready(conn: ConnDep, settings: SettingsDep) -> ReadyOut:
    try:
        conn.execute("SELECT 1").fetchone()
    except Exception as e:
        raise HTTPException(503, "database unavailable") from e
    return ReadyOut(status="ok", version=__version__, database="ok", llm_enabled=settings.llm_enabled)
