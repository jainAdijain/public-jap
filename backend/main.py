"""Starts FastAPI and registers the Inngest endpoint at /api/inngest (internal only, PRD S8).

The same image runs twice (PRD H2). `JAP_ROLE=api` is the process the browser talks to: it
does not mount /api/inngest at all, so there is nothing there to reach even before the guard
in auth/guards.py refuses a proxied call. `JAP_ROLE=worker` runs the functions and the
Playwright browser and is published nowhere. `all` does both, which is what the tests and a
bare `uvicorn main:app` want.
"""

import inngest.fast_api
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import (
    account,
    admin,
    answers,
    applications,
    auth,
    billing,
    boards,
    duplicates,
    errors,
    health,
    inbound,
    insights,
    listings,
    ops,
    postings,
    profile,
    resumes,
    runs,
    webhooks,
)
from auth import guards
from auth.deps import admin_user, agreed_user, current_user
from config import get_settings
from functions import ALL_FUNCTIONS, inngest_client
from observability.http import RequestLog
from observability.logs import setup_logging
from observability.sentry import setup_sentry

# The roles that run JAP's background functions; an api process answers requests only.
RUNS_FUNCTIONS = ("all", "worker")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="JAP", version="0.25.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestLog)  # outermost of ours: times CORS and the error handlers too
    errors.install(app)
    guards.install(app)
    # Open without a session: /api/health (service status only) and the sign-in routes (S6).
    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    # A mail service cannot sign in, so the inbound route carries a shared secret of its
    # own instead of a session, and is off entirely without one (IDEA-16, api/inbound.py).
    app.include_router(inbound.router, prefix="/api")
    # A payment provider cannot sign in either: a signature over the raw body stands in
    # for the session, and anything unsigned is Not Found (IDEA-21, api/webhooks.py).
    app.include_router(webhooks.router, prefix="/api")
    # Everything else needs a signed-in session that has agreed to the notice as it stands,
    # and works on that user's own data (PRD S6, S7, S14).
    for module in (
        profile, resumes, boards, answers, runs, postings, listings, applications, insights,
        billing,
    ):  # fmt: skip
        app.include_router(module.router, prefix="/api", dependencies=[Depends(agreed_user)])
    # Getting your data back and deleting your account are the exception: a signed-in session
    # is enough, agreed or not, or refusing the notice would leave a person with no way out
    # (S15).
    app.include_router(account.router, prefix="/api", dependencies=[Depends(current_user)])
    # The service as a whole is the admin's: health, backups, form tests, duplicates, accounts
    # (PRD S12). To anyone else these routes are Not Found.
    for module in (ops, duplicates, admin):
        app.include_router(module.router, prefix="/api", dependencies=[Depends(admin_user)])
    app.include_router(account.admin_router, prefix="/api", dependencies=[Depends(admin_user)])
    if settings.jap_role in RUNS_FUNCTIONS:
        inngest.fast_api.serve(app, inngest_client, ALL_FUNCTIONS, serve_path="/api/inngest")
    return app


setup_logging()
setup_sentry()
app = create_app()
