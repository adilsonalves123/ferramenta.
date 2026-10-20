import asyncio
import os
import time
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
from app.services.calendario import sugestoes_de_todas
from app.services.resultados import medir_rodada
from app.routers.agenda import router as router_agenda
from app.routers.arte import router as router_arte
from app.routers.campanhas import router
from app.routers.legal import router as router_legal
from app.routers.memoria import router as router_memoria
from app.routers.novas import router as router_novas
from app.routers.projetos import router as router_projetos
from app.routers.resultados import router as router_resultados
from app.routers.resumo import router as router_resumo
from app.routers.sugestoes import router as router_sugestoes
from app.services.imagens import limpar_antigas
from app.services.midia import PASTA_MIDIA

PAGINA = Path(__file__).with_name("pagina.html")
PASTA_QUADROS = Path(__file__).resolve().parent.parent / "quadros"

trava_robo = asyncio.Lock()
LIMPEZA_A_CADA = 6 * 3600
ultima_limpeza: float | None = None


def robo_desligado() -> bool:
    return os.environ.get("DESLIGAR_ROBO", "").strip() == "1"


async def rodada() -> None:
    global ultima_limpeza
    async with trava_robo:
        async with get_sessionmaker()() as session:
            await preparar_fotos(session)
            await publicar_vencidos(session)
            if ultima_limpeza is None or time.monotonic() - ultima_limpeza > LIMPEZA_A_CADA:
                ultima_limpeza = time.monotonic()
                try:
                    await limpar_antigas(session)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await session.rollback()
            try:
                await medir_rodada(session)
            except asyncio.CancelledError:
                raise
            except Exception:
                await session.rollback()
            try:
                await sugestoes_de_todas(session)
            except asyncio.CancelledError:
                raise
            except Exception:
                await session.rollback()


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
    app.include_router(router_arte)
    app.include_router(router_legal)
    app.include_router(router_memoria)
    app.include_router(router_resultados)
    app.include_router(router_resumo)
    app.include_router(router_sugestoes)
    PASTA_MIDIA.mkdir(parents=True, exist_ok=True)
    PASTA_QUADROS.mkdir(parents=True, exist_ok=True)
    app.mount("/midia", StaticFiles(directory=PASTA_MIDIA), name="midia")
    app.mount("/quadros", StaticFiles(directory=PASTA_QUADROS), name="quadros")

    @app.exception_handler(RequestValidationError)
    async def pedido_invalido(request: Request, exc: RequestValidationError) -> JSONResponse:
        erros = exc.errors()
        primeiro = erros[0] if erros else {}
        loc = primeiro.get("loc") or ()
        campo = str(loc[-1]) if loc else ""
        tipo = str(primeiro.get("type") or "")
        if request.url.path.startswith("/marcas"):
            detalhe = {
                "nome": "Escreva o nome da marca com até 120 letras.",
                "publico": "O público pode ter até 200 letras.",
                "site": "O site pode ter até 200 letras.",
                "marca_dagua": "A marca d'água pode ter até 60 letras.",
                "abrangencia": "Escolha o alcance da marca: Minha cidade, Meu estado ou Brasil todo.",
                "cidade": "A cidade pode ter até 120 letras.",
                "uf": "Escreva a sigla do estado com 2 letras, por exemplo SP.",
                "texto": "As outras instruções precisam ter entre 20 e 8000 letras.",
                "oferta": "O que você vende ou faz pode ter até 2000 letras.",
                "itens": "A lista de produtos, pratos ou funcionalidades pode ter até 4000 letras.",
                "horarios": "Os horários podem ter até 1000 letras.",
                "diferenciais": "Os diferenciais podem ter até 2000 letras.",
                "promocoes": "As promoções fixas podem ter até 2000 letras.",
                "tom": "O tom de voz pode ter até 1000 letras.",
                "nunca": "O Nunca dizer pode ter até 2000 letras.",
                "cor_principal": "Escolha a cor principal na paleta.",
                "cor_destaque": "Escolha a cor de destaque na paleta.",
            }.get(campo, "Não consegui salvar a marca. Confira os campos.")
            return JSONResponse(status_code=422, content={"detail": detalhe})
        if request.url.path.startswith("/memoria"):
            detalhe = {
                "descricao": "A descrição pode ter até 1500 letras.",
                "tags": "As palavras-chave podem ter até 500 letras.",
            }.get(campo, "Não consegui salvar a foto. Confira os campos.")
            return JSONResponse(status_code=422, content={"detail": detalhe})
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
