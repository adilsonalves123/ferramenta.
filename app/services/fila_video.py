import asyncio
import json
import uuid

from sqlmodel import select

from app.database import get_sessionmaker
from app.models import VideoTrabalho

_tarefas: set[asyncio.Task] = set()
_em_curso: set[str] = set()
_LIMITE_RETOMADAS = 8


def _detalhe(erro: Exception) -> str:
    texto = str(erro)
    if "insufficient_quota" in texto or "credit_balance_exhausted" in texto:
        return "A conta da OpenAI está sem créditos. Adicione créditos no site da OpenAI e tente de novo."
    if texto.startswith("Command [") or "ffmpeg" in texto.lower():
        return "Não foi possível montar o vídeo agora."
    if texto and len(texto) < 180 and "Traceback" not in texto:
        return texto
    return "Não foi possível montar o vídeo agora."


def _progresso(bruto: str) -> dict:
    if not bruto.strip():
        return {"retomadas": 0, "fotos": [], "audio": "", "avisos": []}
    try:
        carregado = json.loads(bruto)
    except json.JSONDecodeError:
        carregado = {}
    if not isinstance(carregado, dict) or "titulo_anuncio" in carregado:
        return {"retomadas": 0, "fotos": [], "audio": "", "avisos": []}
    carregado.setdefault("retomadas", 0)
    carregado.setdefault("fotos", [])
    carregado.setdefault("audio", "")
    carregado.setdefault("avisos", [])
    return carregado


async def abrir(pedido: dict) -> str:
    ident = uuid.uuid4().hex
    async with get_sessionmaker()() as session:
        session.add(VideoTrabalho(id=ident, status="montando", pedido=json.dumps(pedido)))
        await session.commit()
    _agendar(ident)
    return ident


def _agendar(ident: str) -> None:
    if ident in _em_curso:
        return
    _em_curso.add(ident)
    tarefa = asyncio.create_task(_correr(ident))
    _tarefas.add(tarefa)

    def _fim(feita: asyncio.Task) -> None:
        _em_curso.discard(ident)
        _tarefas.discard(feita)

    tarefa.add_done_callback(_fim)


async def _correr(ident: str) -> None:
    async with get_sessionmaker()() as session:
        item = await session.get(VideoTrabalho, ident)
        if item is None or item.status in {"pronto", "erro"}:
            return
        progresso = _progresso(item.resultado or "")
        retomadas = int(progresso.get("retomadas") or 0) + 1
        if retomadas > _LIMITE_RETOMADAS:
            item.status = "erro"
            item.detalhe = "O servidor reiniciou várias vezes e a montagem não terminou. Clique em Pode gerar de novo."
            session.add(item)
            await session.commit()
            return
        progresso["retomadas"] = retomadas
        item.status = "gerando"
        item.tentativas = retomadas
        item.resultado = json.dumps(progresso)
        item.detalhe = ""
        session.add(item)
        await session.commit()
        pedido = json.loads(item.pedido or "{}")

    async def gravar() -> None:
        try:
            async with get_sessionmaker()() as session:
                atual = await session.get(VideoTrabalho, ident)
                if atual is None or atual.status in {"pronto", "erro"}:
                    return
                atual.resultado = json.dumps(progresso)
                atual.status = "gerando"
                session.add(atual)
                await session.commit()
        except Exception:
            return

    try:
        from app.services.video_job import executar

        resultado = await executar(pedido, progresso, gravar)
    except Exception as erro:
        async with get_sessionmaker()() as session:
            item = await session.get(VideoTrabalho, ident)
            if item is not None and item.status != "pronto":
                item.status = "erro"
                item.detalhe = _detalhe(erro)
                session.add(item)
                await session.commit()
        return

    async with get_sessionmaker()() as session:
        item = await session.get(VideoTrabalho, ident)
        if item is None:
            return
        item.status = "pronto"
        item.detalhe = ""
        item.resultado = json.dumps(resultado)
        session.add(item)
        await session.commit()


async def ler(ident: str) -> dict | None:
    async with get_sessionmaker()() as session:
        item = await session.get(VideoTrabalho, ident)
        if item is None:
            return None
        if item.status == "pronto" and item.resultado:
            dados = json.loads(item.resultado)
            return {"id": ident, "detail": "", **dados, "status": "pronto"}
        if item.status == "erro":
            return {"id": ident, "status": "erro", "detail": item.detalhe or "Não foi possível montar o vídeo agora."}
        return {"id": ident, "status": "montando", "detail": ""}


async def retomar() -> None:
    async with get_sessionmaker()() as session:
        achados = await session.exec(select(VideoTrabalho).where(VideoTrabalho.status.in_(["montando", "gerando"])))
        ids = [item.id for item in achados.all()]
    for ident in ids:
        _agendar(ident)
