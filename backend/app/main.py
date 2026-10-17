import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import engine
from app.routers import briefings, health, sites, weather
from app.services.briefings import scheduler_loop

# Show the app's own INFO logs (e.g. briefings sent) alongside uvicorn's.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s:     %(name)s - %(message)s")
logging.getLogger("sitesentinel").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        app.state.http_client = http_client
        scheduler = None
        if get_settings().briefings_enabled:
            scheduler = asyncio.create_task(scheduler_loop(http_client))
        yield
        if scheduler:
            scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler
    await engine.dispose()


app = FastAPI(title="SiteSentinel API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(sites.router)
app.include_router(weather.router)
app.include_router(briefings.router)
