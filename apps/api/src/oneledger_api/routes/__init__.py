"""Route registration."""

from __future__ import annotations

from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    # Importing these modules registers their durable job handlers.
    from ..services import ai as _ai
    from ..services import analytics_jobs, scheduler
    from ..services import imports as _imports
    from . import (
        accounts,
        ai,
        analytics,
        auth,
        categories,
        entry,
        health,
        imports,
        insights,
        internal,
        invites,
        notifications,
        people,
        planning,
        products,
        review,
        tokens,
        transactions,
    )

    for module in (health, internal):
        app.include_router(module.router)
    for module in (
        auth,
        accounts,
        categories,
        transactions,
        review,
        imports,
        analytics,
        products,
        planning,
        insights,
        tokens,
        ai,
        invites,
        entry,
        people,
        notifications,
    ):
        app.include_router(module.router, prefix="/api/v1")
