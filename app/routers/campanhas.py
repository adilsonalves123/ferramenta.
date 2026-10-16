from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Campanha, Roteiro
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
        midia = await montar_midia(pedido, None, "foto")
        return CampanhaCompleta(
            titulo_anuncio="",
            legenda_redes="",
            roteiro_video={"gancho": "", "problema": "", "solucao": "", "cta": ""},
            google_meu_negocio={"nome_otimizado": "", "categoria": "", "descricao_seo": ""},
            **midia,
        )

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

    vazio = {"foto_url": None, "audio_url": None, "video_url": None, "aviso_midia": None}
    midia = await montar_midia(pedido, gerada, pedido.acao) if pedido.acao in ("foto", "video") else vazio

    campanha = Campanha(
        nicho=pedido.nicho.strip(),
        produto=pedido.produto.strip(),
        titulo_anuncio=gerada.titulo_anuncio,
        legenda_redes=gerada.legenda_redes,
        gmn_nome=gerada.google_meu_negocio.nome_otimizado,
        gmn_categoria=gerada.google_meu_negocio.categoria,
        gmn_descricao=gerada.google_meu_negocio.descricao_seo,
    )
    session.add(campanha)
    try:
        await session.flush()
        if campanha.id is None:
            raise SQLAlchemyError("A campanha ficou sem id.")
        session.add(
            Roteiro(
                campanha_id=campanha.id,
                gancho=gerada.roteiro_video.gancho,
                problema=gerada.roteiro_video.problema,
                solucao=gerada.roteiro_video.solucao,
                cta=gerada.roteiro_video.cta,
            )
        )
        await session.commit()
    except SQLAlchemyError as erro:
        await session.rollback()
        if not midia.get("video_url") and not midia.get("foto_url"):
            raise HTTPException(status_code=503, detail="A campanha foi gerada, mas não entrou no banco.") from erro
        aviso = midia.get("aviso_midia") or ""
        midia["aviso_midia"] = (aviso + " Saiu, mas não entrou no banco.").strip()
    return CampanhaCompleta(**gerada.model_dump(), **midia)
