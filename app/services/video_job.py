import asyncio
import base64
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.schemas import (
    CampanhaCompleta,
    CampanhaGerada,
    GoogleMeuNegocio,
    PedidoCampanha,
    RoteiroVideo,
)
from app.services.midia import (
    _audio,
    _aviso,
    _foto,
    _quadro,
    _video,
    _video_cenas,
    nova_pasta,
)

Gravar = Callable[[], Awaitable[None]]


def _b64(caminho: Path) -> str:
    return base64.b64encode(caminho.read_bytes()).decode("ascii")


def _escrever(conteudo: str, caminho: Path) -> None:
    caminho.write_bytes(base64.b64decode(conteudo))


def _sem_credito(erro: Exception) -> bool:
    texto = str(erro)
    return "insufficient_quota" in texto or "credit_balance_exhausted" in texto


async def executar(pedido: dict, progresso: dict, gravar: Gravar) -> dict:
    cenas = [
        (str(item[0]).strip(), str(item[1]).strip())
        for item in (pedido.get("cenas") or [])
        if isinstance(item, (list, tuple)) and len(item) >= 2 and str(item[0]).strip() and str(item[1]).strip()
    ][:3]
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
    referencia, tipo = None, None
    if foto_base64:
        from app.services.midia import _referencia

        referencia, tipo = _referencia(PedidoCampanha(nicho=nicho, produto=produto[:200], foto_base64=foto_base64))

    ident, pasta = nova_pasta()
    avisos = [str(item) for item in (progresso.get("avisos") or []) if str(item).strip()]
    if not cenas:
        from app.services.midia import montar_midia

        midia = await montar_midia(
            PedidoCampanha(nicho=nicho, produto=produto[:200], acao="video", foto_base64=foto_base64),
            gerada,
            "video",
        )
        return CampanhaCompleta(**gerada.model_dump(), **midia).model_dump(mode="json")

    fotos_salvas = [str(item) for item in (progresso.get("fotos") or [])]
    while len(fotos_salvas) < len(cenas):
        fotos_salvas.append("")
    arquivos: list[Path] = []
    for indice, (_, descricao) in enumerate(cenas):
        destino = pasta / f"cena-{indice}.png"
        guardado = fotos_salvas[indice]
        if guardado and guardado != "falhou":
            _escrever(guardado, destino)
            arquivos.append(destino)
            continue
        if guardado == "falhou":
            continue
        prompt = (
            "Fotografia documental feita com câmera real. Pele com textura, poros, cabelo natural, "
            "roupa de quem trabalha, luz do próprio lugar. "
            "Sem plástico, sem boneco, sem pele lisa de anúncio, sem ilustração, sem 3D, sem desenho. "
            "Sem texto, sem letras, sem logotipo, sem marca d'água. "
            f"Lugar: {nicho}. Cena: {descricao}"
        )
        try:
            await _foto(
                prompt,
                destino,
                referencia if indice == 0 else None,
                tipo if indice == 0 else None,
                "gpt-image-1",
                "low",
            )
            fotos_salvas[indice] = _b64(destino)
            arquivos.append(destino)
        except Exception as erro:
            if _sem_credito(erro):
                raise
            avisos.append(_aviso(f"A cena {indice + 1} não saiu", erro))
            fotos_salvas[indice] = "falhou"
        progresso["fotos"] = fotos_salvas
        progresso["avisos"] = avisos
        await gravar()

    audio = pasta / "audio.mp3"
    guardado_audio = str(progresso.get("audio") or "")
    if guardado_audio and guardado_audio != "falhou":
        _escrever(guardado_audio, audio)
    elif fala and guardado_audio != "falhou":
        await _audio(
            fala,
            audio,
            "Narração de uma história curta em português do Brasil. Voz calma, próxima, como quem conta o que aconteceu. Sem tom de comercial.",
        )
        progresso["audio"] = _b64(audio)
        await gravar()
    if not audio.exists():
        audio_pronto = None
    else:
        audio_pronto = audio

    if not arquivos:
        quadro = pasta / "cena-0.png"
        await asyncio.to_thread(_quadro, quadro)
        arquivos = [quadro]
    video = pasta / "video.mp4"
    if audio_pronto is not None and arquivos:
        if len(arquivos) == 1:
            await asyncio.to_thread(_video, arquivos[0], audio_pronto, video)
        else:
            await asyncio.to_thread(_video_cenas, arquivos, audio_pronto, video)
    else:
        video = None

    def url(arquivo: Path | None, nome: str) -> str | None:
        if arquivo is None or not arquivo.exists():
            return None
        return f"/midia/{ident}/{nome}"

    return CampanhaCompleta(
        **gerada.model_dump(),
        foto_url=url(arquivos[0], arquivos[0].name) if arquivos else None,
        audio_url=url(audio_pronto, "audio.mp3"),
        video_url=url(video, "video.mp4"),
        aviso_midia=" ".join(avisos) or None,
    ).model_dump(mode="json")
