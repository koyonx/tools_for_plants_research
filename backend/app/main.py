from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import analyze, cellpose, health, segformer, training
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
    app.include_router(training.router, tags=["training"])
    return app


app = create_app()
