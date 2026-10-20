from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.schemas import PedidoResultados, RespostaResultados
from app.services.resultados import atualizar_agora, listar_resultados

router = APIRouter()


@router.get("/resultados", response_model=RespostaResultados)
async def get_resultados(marca_id: int = 0, session: AsyncSession = Depends(get_session)) -> RespostaResultados:
    return RespostaResultados(**await listar_resultados(session, marca_id))


@router.post("/resultados/atualizar", response_model=RespostaResultados)
async def post_atualizar_resultados(
    pedido: PedidoResultados,
    session: AsyncSession = Depends(get_session),
) -> RespostaResultados:
    aviso = await atualizar_agora(session, pedido.marca_id)
    return RespostaResultados(**await listar_resultados(session, pedido.marca_id), aviso=aviso)
