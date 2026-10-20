import asyncio
import os
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import criar_tabelas, fechar_engine, get_sessionmaker
from app.services.publicar import preparar_fotos, publicar_vencidos, soltar_presos
from app.routers.agenda import router as router_agenda
from app.routers.campanhas import router
from app.routers.legal import router as router_legal
from app.routers.novas import router as router_novas
from app.routers.projetos import router as router_projetos
from app.services.midia import PASTA_MIDIA

PAGINA = Path(__file__).with_name("pagina.html")
PASTA_QUADROS = Path(__file__).resolve().parent.parent / "quadros"

trava_robo = asyncio.Lock()


def robo_desligado() -> bool:
    return os.environ.get("DESLIGAR_ROBO", "").strip() == "1"


async def rodada() -> None:
    async with trava_robo:
        async with get_sessionmaker()() as session:
            await preparar_fotos(session)
            await publicar_vencidos(session)


async def vigia() -> None:
    await asyncio.sleep(45)
    while True:
        try:
            await rodada()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(90)


@asynccontextmanager
async def vida(app: FastAPI):
    await criar_tabelas()
    tarefa = None
    if not robo_desligado():
        async with get_sessionmaker()() as session:
            await soltar_presos(session)
        tarefa = asyncio.create_task(vigia())
    yield
    if tarefa is not None:
        tarefa.cancel()
        try:
            await tarefa
        except asyncio.CancelledError:
            pass
    await fechar_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.produto_nome, version="0.1.0", lifespan=vida)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origens_cors(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(router_novas)
    app.include_router(router_projetos)
    app.include_router(router_agenda)
    app.include_router(router_legal)
    PASTA_MIDIA.mkdir(parents=True, exist_ok=True)
    PASTA_QUADROS.mkdir(parents=True, exist_ok=True)
    app.mount("/midia", StaticFiles(directory=PASTA_MIDIA), name="midia")
    app.mount("/quadros", StaticFiles(directory=PASTA_QUADROS), name="quadros")

    @app.exception_handler(RequestValidationError)
    async def pedido_invalido(_request: Request, exc: RequestValidationError) -> JSONResponse:
        erros = exc.errors()
        primeiro = erros[0] if erros else {}
        loc = primeiro.get("loc") or ()
        campo = str(loc[-1]) if loc else ""
        tipo = str(primeiro.get("type") or "")
        if tipo == "string_too_long" and campo in {"produto", "produto_servico"}:
            detalhe = "O texto ficou longo demais. Encurte um pouco e tente de novo."
        elif tipo == "string_too_short" and campo in {"nicho", "nicho_negocio", "produto", "produto_servico"}:
            detalhe = "Escreva o título e o que você quer, com pelo menos algumas palavras."
        else:
            detalhe = "O pedido não passou. Confira o título e o que você quer."
        return JSONResponse(status_code=422, content={"detail": detalhe})

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def inicio() -> str:
        return PAGINA.read_text(encoding="utf-8").replace("__PRODUTO__", escape(settings.produto_nome))

    @app.get("/saude")
    async def saude() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
