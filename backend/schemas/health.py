from pydantic import BaseModel


class Check(BaseModel):
    ok: bool
    detail: str


class Health(BaseModel):
    """What /api/health answers. Both fields below are left out for a caller who is not
    entitled to them (api/health.py), so an empty checks map means "not told", not "no
    checks ran" — the UI already treats a missing check as unknown."""

    ok: bool
    env: str = ""
    checks: dict[str, Check] = {}
