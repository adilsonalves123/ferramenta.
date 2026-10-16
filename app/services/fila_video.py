import asyncio
import uuid
from collections.abc import Awaitable, Callable

trabalhos: dict[str, dict] = {}
_tarefas: set[asyncio.Task] = set()


def abrir(trabalho: Callable[[], Awaitable[dict]]) -> str:
    ident = uuid.uuid4().hex
    trabalhos[ident] = {"id": ident, "status": "montando", "detail": ""}

    async def correr() -> None:
        try:
            resultado = await trabalho()
            trabalhos[ident] = {"id": ident, "status": "pronto", "detail": "", **resultado}
        except Exception as erro:
            texto = str(erro)
            if "insufficient_quota" in texto or "credit_balance_exhausted" in texto:
                detalhe = "A conta da OpenAI está sem créditos. Adicione créditos no site da OpenAI e tente de novo."
            else:
                detalhe = "Não foi possível montar o vídeo agora."
            trabalhos[ident] = {"id": ident, "status": "erro", "detail": detalhe}

    tarefa = asyncio.create_task(correr())
    _tarefas.add(tarefa)
    tarefa.add_done_callback(_tarefas.discard)
    return ident


def ler(ident: str) -> dict | None:
    return trabalhos.get(ident)
