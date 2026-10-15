from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import criar_tabelas, fechar_engine
from app.routers.campanhas import router
from app.services.midia import PASTA_MIDIA

PAGINA = Path(__file__).with_name("pagina.html")
PASTA_QUADROS = Path(__file__).resolve().parent.parent / "quadros"


@asynccontextmanager
async def vida(app: FastAPI):
    await criar_tabelas()
    yield
    await fechar_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Ferramenta de campanha", version="0.1.0", lifespan=vida)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origens_cors(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    PASTA_MIDIA.mkdir(parents=True, exist_ok=True)
    PASTA_QUADROS.mkdir(parents=True, exist_ok=True)
    app.mount("/midia", StaticFiles(directory=PASTA_MIDIA), name="midia")
    app.mount("/quadros", StaticFiles(directory=PASTA_QUADROS), name="quadros")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def inicio() -> str:
        return PAGINA.read_text(encoding="utf-8")

    @app.get("/saude")
    async def saude() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
