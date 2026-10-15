from fastapi import APIRouter, HTTPException

from app.schemas import ClonagemGerada, PedidoClonador, PedidoRadar, RadarResposta
from app.services.clonador import clonar_video
from app.services.radar import mapear

router = APIRouter()


@router.post("/clonar-video", response_model=ClonagemGerada)
async def post_clonar_video(pedido: PedidoClonador) -> ClonagemGerada:
    if not pedido.link_video.strip() and not pedido.nome_arquivo.strip():
        raise HTTPException(status_code=422, detail="Cole um link ou escolha um vídeo.")
    try:
        return await clonar_video(pedido)
    except ValueError as erro:
        raise HTTPException(status_code=502, detail=str(erro)) from erro
    except Exception as erro:
        texto = str(erro)
        if "insufficient_quota" in texto or "credit_balance_exhausted" in texto:
            detalhe = "A conta da OpenAI está sem créditos. Adicione créditos no site da OpenAI e tente de novo."
        else:
            detalhe = "Não foi possível reescrever o roteiro agora."
        raise HTTPException(status_code=502, detail=detalhe) from erro


@router.post("/radar-clientes", response_model=RadarResposta)
async def post_radar_clientes(pedido: PedidoRadar) -> RadarResposta:
    if pedido.tipo_busca == "determinada" and len(pedido.tags.strip()) < 2:
        raise HTTPException(status_code=422, detail="Digite as tags da busca determinada.")
    return mapear(pedido)
