from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Campanha
from app.schemas import CampanhaCompleta, PedidoCampanha
from app.services.midia import montar_midia
from app.services.openai_campanha import gerar_campanha

router = APIRouter()


@router.post("/gerar-campanha", response_model=CampanhaCompleta)
async def post_gerar_campanha(
    pedido: PedidoCampanha,
    session: AsyncSession = Depends(get_session),
) -> CampanhaCompleta:
    if pedido.acao == "foto":
        midia = await montar_midia(pedido)
        return CampanhaCompleta(titulo_anuncio="", legenda_redes="", **midia)

    try:
        gerada = await gerar_campanha(pedido)
    except ValueError as erro:
        raise HTTPException(status_code=502, detail=str(erro)) from erro
    except Exception as erro:
        texto = str(erro)
        if "insufficient_quota" in texto or "credit_balance_exhausted" in texto:
            detalhe = "A conta da OpenAI está sem créditos. Adicione créditos no site da OpenAI e tente de novo."
        else:
            detalhe = "Não foi possível gerar a campanha agora."
        raise HTTPException(status_code=502, detail=detalhe) from erro

    session.add(
        Campanha(
            nicho=pedido.nicho.strip(),
            produto=pedido.produto.strip(),
            titulo_anuncio=gerada.titulo_anuncio,
            legenda_redes=gerada.legenda_redes,
            gmn_nome="",
            gmn_categoria="",
            gmn_descricao="",
        )
    )
    try:
        await session.commit()
    except SQLAlchemyError as erro:
        await session.rollback()
        raise HTTPException(status_code=503, detail="A campanha foi gerada, mas não entrou no banco.") from erro
    return CampanhaCompleta(**gerada.model_dump())
