import asyncio

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.schemas import (
    CampanhaGerada,
    ClonagemGerada,
    GoogleMeuNegocio,
    PedidoCampanha,
    PedidoClonador,
    PedidoFotoConfirmada,
    PedidoRadar,
    PedidoSugestao,
    PedidoVideoConfirmado,
    RadarResposta,
    RoteiroVideo,
    SugestaoVideo,
)
from app.services.clonador import clonar_video, ouvir_video
from app.services.fila_video import abrir as abrir_fila
from app.services.fila_video import ler as ler_fila
from app.services.fila_video import previa as previa_fila
from app.services.midia import montar_midia
from app.services.pagina import ler_pagina
from app.services.radar import mapear
from app.services.sugestao import sugerir_video

router = APIRouter()

_LIMITE_ARQUIVO = 25 * 1024 * 1024


@router.post("/clonar-video", response_model=ClonagemGerada)
async def post_clonar_video(
    nicho_negocio: str = Form(min_length=2, max_length=120),
    produto_servico: str = Form(min_length=2, max_length=200),
    link_video: str = Form(default="", max_length=500),
    arquivo_video: UploadFile | None = File(default=None),
) -> ClonagemGerada:
    nome = (arquivo_video.filename or "").strip() if arquivo_video is not None else ""
    dados = b""
    if nome:
        dados = await arquivo_video.read(_LIMITE_ARQUIVO + 1)
        if len(dados) > _LIMITE_ARQUIVO:
            raise HTTPException(status_code=422, detail="O vídeo precisa ter até 25 MB.")
    if not link_video.strip() and not dados:
        raise HTTPException(status_code=422, detail="Cole um link do YouTube ou escolha um vídeo.")
    pedido = PedidoClonador(
        nicho_negocio=nicho_negocio,
        produto_servico=produto_servico,
        link_video=link_video,
        nome_arquivo=nome,
    )
    try:
        transcricao = await ouvir_video(link_video, dados or None, nome)
        gerada = await clonar_video(pedido, transcricao)
        midia = await montar_midia(
            PedidoCampanha(nicho=pedido.nicho, produto=pedido.produto, acao="video"),
            CampanhaGerada(
                titulo_anuncio=gerada.titulo,
                legenda_redes=gerada.legenda,
                roteiro_video=RoteiroVideo(
                    gancho=gerada.gancho,
                    problema=gerada.retencao,
                    solucao=gerada.desenvolvimento,
                    cta=gerada.cta,
                ),
                google_meu_negocio=GoogleMeuNegocio(nome_otimizado="", categoria="", descricao_seo=""),
            ),
            "video",
        )
    except ValueError as erro:
        raise HTTPException(status_code=502, detail=str(erro)) from erro
    except Exception as erro:
        texto = str(erro)
        if "insufficient_quota" in texto or "credit_balance_exhausted" in texto:
            detalhe = "A conta da OpenAI está sem créditos. Adicione créditos no site da OpenAI e tente de novo."
        else:
            detalhe = "Não foi possível refazer o vídeo agora."
        raise HTTPException(status_code=502, detail=detalhe) from erro
    return gerada.model_copy(
        update={
            "foto_url": midia["foto_url"],
            "video_url": midia["video_url"],
            "aviso_midia": midia["aviso_midia"],
        }
    )


@router.post("/ouvir-video")
async def post_ouvir_video(
    link_video: str = Form(default="", max_length=500),
    arquivo_video: UploadFile | None = File(default=None),
) -> dict[str, str]:
    nome = (arquivo_video.filename or "").strip() if arquivo_video is not None else ""
    dados = b""
    if nome:
        dados = await arquivo_video.read(_LIMITE_ARQUIVO + 1)
        if len(dados) > _LIMITE_ARQUIVO:
            raise HTTPException(status_code=422, detail="O vídeo precisa ter até 25 MB.")
    if not link_video.strip() and not dados:
        raise HTTPException(status_code=422, detail="Cole um link do YouTube ou escolha um vídeo.")
    try:
        transcricao = await ouvir_video(link_video, dados or None, nome)
    except ValueError as erro:
        raise HTTPException(status_code=502, detail=str(erro)) from erro
    return {"transcricao": transcricao}


@router.post("/sugerir-video", response_model=SugestaoVideo)
async def post_sugerir_video(pedido: PedidoSugestao) -> SugestaoVideo:
    if pedido.origem == "foto" and pedido.link.strip() and not pedido.transcricao.strip():
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
            detalhe = "Não foi possível sugerir a foto agora." if pedido.origem == "foto" else "Não foi possível sugerir o vídeo agora."
        raise HTTPException(status_code=502, detail=detalhe) from erro


@router.post("/confirmar-video")
async def post_confirmar_video(pedido: PedidoVideoConfirmado) -> dict:
    cenas = [(cena.narracao.strip(), cena.imagem.strip()) for cena in pedido.cenas if cena.narracao.strip() and cena.imagem.strip()]
    fala = pedido.roteiro.strip() or " ".join(narracao for narracao, _ in cenas) or " ".join(
        parte for parte in (pedido.gancho, pedido.problema, pedido.solucao, pedido.cta) if parte.strip()
    )
    partes = [narracao for narracao, _ in cenas] or [pedido.gancho, pedido.problema, pedido.solucao, pedido.cta]
    while len(partes) < 4:
        partes.append(partes[-1])
    ident = await abrir_fila(
        {
            "nicho": pedido.nicho.strip(),
            "produto": f"{pedido.cena.strip()}. {pedido.produto.strip()}"[:200],
            "titulo": pedido.titulo.strip(),
            "fala": fala.strip(),
            "partes": [parte.strip() for parte in partes],
            "cenas": [[narracao, imagem] for narracao, imagem in cenas[:3]],
            "foto_base64": pedido.foto_base64,
        }
    )
    return {"id": ident, "status": "montando", "detail": ""}


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
        midia = await montar_midia(campanha, None, "foto")
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


@router.get("/video-trabalho/{ident}/previa")
async def get_video_previa(ident: str) -> Response:
    dados = await previa_fila(ident)
    if not dados:
        raise HTTPException(status_code=404, detail="A cena ainda não saiu.")
    return Response(content=dados, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/video-trabalho/{ident}")
async def get_video_trabalho(ident: str) -> dict:
    item = await ler_fila(ident)
    if item is None:
        raise HTTPException(status_code=404, detail="Esse vídeo não está mais na fila.")
    return item


@router.post("/radar-clientes", response_model=RadarResposta)
async def post_radar_clientes(pedido: PedidoRadar) -> RadarResposta:
    if pedido.tipo_busca == "determinada" and len(pedido.tags.strip()) < 2:
        raise HTTPException(status_code=422, detail="Digite as tags da busca determinada.")
    if pedido.tipo_busca == "geral" and len(pedido.categoria.strip()) < 2:
        raise HTTPException(status_code=422, detail="Digite o que buscar.")
    try:
        return await mapear(pedido)
    except RuntimeError as erro:
        raise HTTPException(status_code=502, detail=str(erro)) from erro
