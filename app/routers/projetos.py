from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Projeto
from app.schemas import ItemProjeto, PedidoProjeto

router = APIRouter()


def _item(registro: Projeto) -> ItemProjeto:
    return ItemProjeto(
        id=registro.id or 0,
        tipo=registro.tipo,
        titulo=registro.titulo,
        legenda=registro.legenda,
        imagem_url=registro.imagem_url,
        video_url=registro.video_url,
    )


@router.get("/projetos", response_model=list[ItemProjeto])
async def get_projetos(session: AsyncSession = Depends(get_session)) -> list[ItemProjeto]:
    resultado = await session.exec(select(Projeto).order_by(Projeto.id.desc()))
    return [_item(item) for item in resultado.all()]


@router.post("/projetos", response_model=ItemProjeto)
async def post_projeto(
    pedido: PedidoProjeto,
    session: AsyncSession = Depends(get_session),
) -> ItemProjeto:
    registro = Projeto(
        tipo=pedido.tipo.strip()[:20],
        titulo=pedido.titulo.strip()[:200],
        legenda=pedido.legenda.strip(),
        imagem_url=pedido.imagem_url.strip()[:500],
        video_url=pedido.video_url.strip()[:500],
    )
    session.add(registro)
    await session.commit()
    await session.refresh(registro)
    return _item(registro)
