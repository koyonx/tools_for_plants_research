from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    analyze,
    batches,
    cellpose,
    co2_diffusion,
    co2_morphometrics,
    compare,
    compare_export,
    darcy,
    gas_exchange,
    gm_fit,
    health,
    segformer,
    training,
    validation,
    water_path,
)
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title="plants-research-backend",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, tags=["health"])
    app.include_router(analyze.router, tags=["analyze"])
    app.include_router(cellpose.router, tags=["analyze"])
    app.include_router(segformer.router, tags=["analyze"])
    app.include_router(water_path.router, tags=["analyze"])
    app.include_router(darcy.router, tags=["analyze"])
    app.include_router(co2_morphometrics.router, tags=["analyze"])
    app.include_router(co2_diffusion.router, tags=["analyze"])
    app.include_router(batches.router, tags=["batches"])
    app.include_router(compare.router, tags=["compare"])
    app.include_router(compare_export.router, tags=["compare"])
    app.include_router(gas_exchange.router, tags=["gas-exchange"])
    app.include_router(gm_fit.router, tags=["gas-exchange"])
    app.include_router(validation.router, tags=["validation"])
    app.include_router(training.router, tags=["training"])
    return app


app = create_app()
