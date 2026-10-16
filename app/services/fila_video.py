import asyncio
import json
import uuid

from sqlmodel import select

from app.database import get_sessionmaker
from app.models import VideoTrabalho

_tarefas: set[asyncio.Task] = set()


def _detalhe(erro: Exception) -> str:
    texto = str(erro)
    if "insufficient_quota" in texto or "credit_balance_exhausted" in texto:
        return "A conta da OpenAI está sem créditos. Adicione créditos no site da OpenAI e tente de novo."
    if texto.startswith("Command [") or "ffmpeg" in texto.lower():
        return "Não foi possível montar o vídeo agora."
    if texto and len(texto) < 180 and "Traceback" not in texto:
        return texto
    return "Não foi possível montar o vídeo agora."


async def abrir(pedido: dict) -> str:
    ident = uuid.uuid4().hex
    async with get_sessionmaker()() as session:
        session.add(VideoTrabalho(id=ident, status="montando", pedido=json.dumps(pedido)))
        await session.commit()
    _agendar(ident)
    return ident


def _agendar(ident: str) -> None:
    tarefa = asyncio.create_task(_correr(ident))
    _tarefas.add(tarefa)
    tarefa.add_done_callback(_tarefas.discard)


async def _correr(ident: str) -> None:
    async with get_sessionmaker()() as session:
        item = await session.get(VideoTrabalho, ident)
        if item is None or item.status in {"pronto", "erro"}:
            return
        if item.tentativas >= 2:
            item.status = "erro"
            item.detalhe = "A montagem foi interrompida. Clique em Pode gerar de novo."
            session.add(item)
            await session.commit()
            return
        item.status = "gerando"
        item.tentativas += 1
        session.add(item)
        await session.commit()
        pedido = json.loads(item.pedido or "{}")

    try:
        from app.services.video_job import executar

        resultado = await executar(pedido)
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
        ids: list[str] = []
        for item in achados.all():
            if item.tentativas >= 2:
                item.status = "erro"
                item.detalhe = "A montagem foi interrompida. Clique em Pode gerar de novo."
                session.add(item)
            else:
                ids.append(item.id)
        await session.commit()
    for ident in ids:
        _agendar(ident)
