from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.schemas import (
    CampanhaGerada,
    ClonagemGerada,
    GoogleMeuNegocio,
    PedidoCampanha,
    PedidoClonador,
    PedidoRadar,
    RadarResposta,
    RoteiroVideo,
)
from app.services.clonador import clonar_video, ouvir_video
from app.services.midia import montar_midia
from app.services.radar import mapear

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
