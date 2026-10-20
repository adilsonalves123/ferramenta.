import asyncio

from fastapi import APIRouter, HTTPException

from app.schemas import (
    PedidoCampanha,
    PedidoFotoConfirmada,
    PedidoSugestao,
    SugestaoVideo,
)
from app.services.midia import montar_midia
from app.services.pagina import ler_pagina
from app.services.sugestao import sugerir_video

router = APIRouter()


@router.post("/sugerir-video", response_model=SugestaoVideo)
async def post_sugerir_video(pedido: PedidoSugestao) -> SugestaoVideo:
    if pedido.link.strip() and not pedido.transcricao.strip():
        try:
            lido = await asyncio.to_thread(ler_pagina, pedido.link.strip())
        except ValueError as erro:
            raise HTTPException(status_code=422, detail=str(erro)) from erro
        pedido = pedido.model_copy(update={"transcricao": lido})
    try:
        return await sugerir_video(pedido)
    except ValueError as erro:
        raise HTTPException(status_code=502, detail=str(erro)) from erro
    except Exception as erro:
        texto = str(erro)
        if "insufficient_quota" in texto or "credit_balance_exhausted" in texto:
            detalhe = "A conta da OpenAI está sem créditos. Adicione créditos no site da OpenAI e tente de novo."
        else:
            detalhe = "Não foi possível sugerir a foto agora."
        raise HTTPException(status_code=502, detail=detalhe) from erro


@router.post("/confirmar-foto")
async def post_confirmar_foto(pedido: PedidoFotoConfirmada) -> dict:
    campanha = PedidoCampanha(
        nicho=pedido.nicho.strip(),
        produto=pedido.produto.strip()[:200],
        acao="foto",
        foto_base64=pedido.foto_base64,
        brief_foto=pedido.cena.strip()[:800],
    )
    try:
        midia = await montar_midia(campanha)
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    except Exception as erro:
        texto = str(erro)
        if "insufficient_quota" in texto or "credit_balance_exhausted" in texto:
            detalhe = "A conta da OpenAI está sem créditos. Adicione créditos no site da OpenAI e tente de novo."
        else:
            detalhe = "Não foi possível criar a foto agora."
        raise HTTPException(status_code=502, detail=detalhe) from erro
    if not midia.get("foto_url"):
        raise HTTPException(status_code=502, detail=midia.get("aviso_midia") or "A foto não saiu.")
    return {
        "titulo_anuncio": pedido.titulo.strip(),
        "legenda_redes": pedido.legenda.strip(),
        "roteiro_video": {"gancho": "", "problema": "", "solucao": "", "cta": ""},
        "google_meu_negocio": {"nome_otimizado": "", "categoria": "", "descricao_seo": ""},
        **midia,
    }
