from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.schemas import ItemSugestao, RespostaSugestoes
from app.services.calendario import garantir_sugestoes, listar_sugestoes, mudar_status
from app.services.semana import marca_por_id

router = APIRouter()


async def _resposta(session: AsyncSession, marca_id: int, forcar: bool) -> RespostaSugestoes:
    marca = await marca_por_id(session, marca_id)
    if marca is None:
        raise HTTPException(status_code=404, detail="Crie uma marca em Minha marca antes.")
    try:
        await garantir_sugestoes(session, marca, forcar)
    except Exception:
        await session.rollback()
        marca = await marca_por_id(session, marca_id)
        if marca is None:
            raise HTTPException(status_code=404, detail="Essa marca não existe mais.") from None
    itens = await listar_sugestoes(session, marca.id or 0)
    return RespostaSugestoes(
        marca_id=marca.id or 0,
        cidade=marca.cidade or "",
        uf=marca.uf or "",
        clima_status=marca.clima_status or "",
        itens=[
            ItemSugestao(
                id=item.id or 0,
                dia_sugerido=item.dia_sugerido,
                tipo=item.tipo,
                titulo=item.titulo,
                detalhe=item.detalhe,
                data_evento=item.data_evento,
            )
            for item in itens
        ],
    )


@router.get("/sugestoes", response_model=RespostaSugestoes)
async def get_sugestoes(marca_id: int = 0, session: AsyncSession = Depends(get_session)) -> RespostaSugestoes:
    return await _resposta(session, marca_id, False)


@router.post("/sugestoes/atualizar", response_model=RespostaSugestoes)
async def post_atualizar_sugestoes(
    marca_id: int = 0, session: AsyncSession = Depends(get_session)
) -> RespostaSugestoes:
    return await _resposta(session, marca_id, True)


@router.post("/sugestoes/{sugestao_id}/dispensar")
async def post_dispensar(
    sugestao_id: int, marca_id: int = 0, session: AsyncSession = Depends(get_session)
) -> dict[str, bool]:
    if not await mudar_status(session, sugestao_id, marca_id, "dispensada"):
        raise HTTPException(status_code=404, detail="Essa sugestão não existe mais.")
    return {"ok": True}
