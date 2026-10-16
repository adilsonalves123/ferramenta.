from app.schemas import (
    CampanhaCompleta,
    CampanhaGerada,
    GoogleMeuNegocio,
    PedidoCampanha,
    RoteiroVideo,
)
from app.services.midia import _referencia, montar_historia, montar_midia


async def executar(pedido: dict) -> dict:
    cenas = [
        (item[0], item[1])
        for item in (pedido.get("cenas") or [])
        if isinstance(item, (list, tuple)) and len(item) >= 2 and str(item[0]).strip() and str(item[1]).strip()
    ]
    fala = str(pedido.get("fala") or "").strip()
    partes = [str(parte) for parte in (pedido.get("partes") or []) if str(parte).strip()]
    while len(partes) < 4:
        partes.append(partes[-1] if partes else fala or "Cena")
    gerada = CampanhaGerada(
        titulo_anuncio=str(pedido.get("titulo") or "").strip(),
        legenda_redes=fala,
        roteiro_video=RoteiroVideo(
            gancho=partes[0].strip(),
            problema=partes[1].strip(),
            solucao=partes[2].strip(),
            cta=partes[3].strip(),
        ),
        google_meu_negocio=GoogleMeuNegocio(nome_otimizado="", categoria="", descricao_seo=""),
    )
    nicho = str(pedido.get("nicho") or "").strip()
    produto = str(pedido.get("produto") or "").strip()
    foto_base64 = str(pedido.get("foto_base64") or "")
    referencia, tipo = _referencia(PedidoCampanha(nicho=nicho, produto=produto[:200], foto_base64=foto_base64))
    if cenas:
        midia = await montar_historia(nicho, cenas[:3], fala, referencia, tipo)
    else:
        midia = await montar_midia(
            PedidoCampanha(nicho=nicho, produto=produto[:200], acao="video", foto_base64=foto_base64),
            gerada,
            "video",
        )
    return CampanhaCompleta(**gerada.model_dump(), **midia).model_dump(mode="json")
