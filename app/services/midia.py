import base64
import asyncio
import subprocess
import uuid
from pathlib import Path

from app.schemas import CampanhaGerada, PedidoCampanha
from app.services.openai_campanha import cliente_openai

PASTA_MIDIA = Path(__file__).resolve().parent.parent.parent / "midia"


def nova_pasta() -> tuple[str, Path]:
    PASTA_MIDIA.mkdir(parents=True, exist_ok=True)
    ident = uuid.uuid4().hex
    pasta = PASTA_MIDIA / ident
    pasta.mkdir()
    return ident, pasta


def _aviso(rotulo: str, erro: Exception) -> str:
    detalhe = str(erro).replace("\n", " ")
    if "insufficient_quota" in detalhe or "credit_balance_exhausted" in detalhe:
        return f"{rotulo}: a conta da OpenAI está sem créditos."
    return f"{rotulo}: {detalhe[:160]}"


def _prompt_foto(pedido: PedidoCampanha, acao: str, tem_referencia: bool) -> str:
    nicho = pedido.nicho.strip()
    produto = pedido.produto.strip()
    if tem_referencia:
        return (
            "Use a foto enviada como a cena real. Conserve o produto, o balcão, as cores e o lugar. "
            "Ajuste só a luz e o enquadramento para um post quadrado. "
            "Não troque o produto, não invente pessoa, não adicione texto, letra, logotipo nem marca d'água. "
            f"Negócio: {nicho}. O que deve continuar em destaque: {produto}."
        )
    if acao == "video":
        return (
            "Fotografia realista, feita dentro do estabelecimento, luz natural de fim de tarde. "
            f"Ambiente de {nicho}. Produto em destaque, de perto: {produto}. "
            "Uma pessoa do lugar pode aparecer ao fundo, de lado, sem olhar para a câmera e sem pose de apresentador. "
            "Sem texto, sem letras, sem logotipo, sem marca d'água."
        )
    return (
        "Fotografia realista de comida e bebida, luz natural, câmera perto do produto. "
        f"Ambiente de {nicho}, com o balcão ao fundo levemente desfocado. "
        f"Assunto único: {produto}. "
        "Sem pessoas, sem mãos, sem texto, sem letras, sem logotipo, sem marca d'água."
    )


def _referencia(pedido: PedidoCampanha) -> tuple[bytes | None, str | None]:
    bruto = pedido.foto_base64.strip()
    if not bruto:
        return None, None
    if bruto.startswith("data:") and "," in bruto:
        bruto = bruto.split(",", 1)[1]
    try:
        dados = base64.b64decode(bruto, validate=True)
    except Exception as erro:
        raise ValueError("A foto enviada não pôde ser lida. Use JPG, PNG ou WebP.") from erro
    if not dados or len(dados) > 4_000_000:
        raise ValueError("A foto precisa ter até 4 MB.")
    if dados.startswith(b"\xff\xd8\xff"):
        return dados, "image/jpeg"
    if dados.startswith(b"\x89PNG\r\n\x1a\n"):
        return dados, "image/png"
    if len(dados) > 12 and dados.startswith(b"RIFF") and dados[8:12] == b"WEBP":
        return dados, "image/webp"
    raise ValueError("Envie a foto em JPG, PNG ou WebP.")


def _fala(gerada: CampanhaGerada) -> str:
    roteiro = gerada.roteiro_video
    texto = " ".join(
        parte.strip()
        for parte in (roteiro.gancho, roteiro.problema, roteiro.solucao, roteiro.cta)
        if parte.strip()
    )
    return texto[:4000]


async def _foto(
    prompt: str,
    destino: Path,
    referencia: bytes | None,
    tipo: str | None,
    modelo: str,
    qualidade: str,
) -> None:
    cliente = cliente_openai().with_options(timeout=45)
    pedido = {
        "model": modelo,
        "prompt": prompt,
        "size": "1024x1024",
        "quality": qualidade,
        "output_format": "png",
    }
    if referencia:
        nome = "referencia.jpg" if tipo == "image/jpeg" else "referencia.webp" if tipo == "image/webp" else "referencia.png"
        imagem = await cliente.images.edit(
            image=(nome, referencia, tipo or "image/png"),
            input_fidelity="high",
            **pedido,
        )
    else:
        imagem = await cliente.images.generate(**pedido)
    conteudo = imagem.data[0].b64_json if imagem.data else None
    if not conteudo:
        raise ValueError("A foto não veio.")
    destino.write_bytes(base64.b64decode(conteudo))


async def _audio(fala: str, destino: Path) -> None:
    try:
        resposta = await cliente_openai().audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="coral",
            input=fala,
            response_format="mp3",
        )
    except Exception:
        resposta = await cliente_openai().audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=fala,
            response_format="mp3",
        )
    destino.write_bytes(resposta.content)


def _quadro(destino: Path) -> None:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    resultado = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x1e1b4b:s=1024x1024:d=1",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(destino),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if resultado.returncode != 0 or not destino.exists():
        raise RuntimeError("Não consegui criar a imagem do vídeo.")


def _video(foto: Path, audio: Path, destino: Path) -> None:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    comando = [
        ffmpeg,
        "-y",
        "-loop",
        "1",
        "-framerate",
        "25",
        "-i",
        str(foto),
        "-i",
        str(audio),
        "-c:v",
        "libx264",
        "-tune",
        "stillimage",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-pix_fmt",
        "yuv420p",
        "-shortest",
        "-movflags",
        "+faststart",
        str(destino),
    ]
    resultado = subprocess.run(comando, capture_output=True, text=True, timeout=60)
    if resultado.returncode != 0 or not destino.exists():
        raise RuntimeError(resultado.stderr[-400:] or "O vídeo não foi montado.")


async def montar_midia(
    pedido: PedidoCampanha,
    gerada: CampanhaGerada | None,
    acao: str,
) -> dict[str, str | None]:
    ident, pasta = nova_pasta()
    avisos: list[str] = []
    foto: Path | None = pasta / "foto.png"
    audio: Path | None = pasta / "audio.mp3" if acao == "video" and gerada else None
    video: Path | None = pasta / "video.mp4" if acao == "video" and gerada else None
    modelo = "gpt-image-1-mini" if acao == "video" else "gpt-image-1"
    qualidade = "low" if acao == "video" else "medium"
    fala = _fala(gerada) if gerada else ""
    if video is not None and not fala.strip():
        avisos.append("O vídeo não saiu: a fala veio vazia.")
        audio = None
        video = None

    async def gerar_foto() -> None:
        referencia, tipo = _referencia(pedido)
        await _foto(
            _prompt_foto(pedido, acao, referencia is not None),
            foto,  # type: ignore[arg-type]
            referencia,
            tipo,
            modelo,
            qualidade,
        )

    foto_erro: Exception | None = None
    audio_erro: Exception | None = None
    if audio is not None:
        resultado_foto, resultado_audio = await asyncio.gather(gerar_foto(), _audio(fala, audio), return_exceptions=True)
        if isinstance(resultado_foto, Exception):
            foto_erro = resultado_foto
        if isinstance(resultado_audio, Exception):
            audio_erro = resultado_audio
    else:
        try:
            await gerar_foto()
        except Exception as erro:
            foto_erro = erro

    if foto_erro is not None:
        avisos.append(_aviso("A foto não saiu", foto_erro))
        if video is not None and foto is not None:
            try:
                _quadro(foto)
            except Exception as falha_quadro:
                avisos.append(_aviso("A imagem do vídeo não saiu", falha_quadro))
                foto = None
        else:
            foto = None
    if audio_erro is not None:
        avisos.append(_aviso("O áudio não saiu", audio_erro))
        audio = None
        video = None

    if video is not None and audio is not None and foto is not None and audio.exists() and foto.exists():
        try:
            _video(foto, audio, video)
        except Exception as erro:
            avisos.append(_aviso("O vídeo não foi montado", erro))
            video = None
    elif video is not None:
        video = None

    def url(arquivo: Path | None, nome: str) -> str | None:
        if arquivo is None or not arquivo.exists():
            return None
        return f"/midia/{ident}/{nome}"

    return {
        "foto_url": url(foto, "foto.png"),
        "audio_url": url(audio, "audio.mp3"),
        "video_url": url(video, "video.mp4"),
        "aviso_midia": " ".join(avisos) or None,
    }
