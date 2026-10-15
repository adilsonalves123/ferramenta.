import base64
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


def _prompt_foto(pedido: PedidoCampanha) -> str:
    return (
        "Fotografia realista, vertical de estúdio. Um apresentador brasileiro simpático, "
        "do peito para cima, olhando para a câmera, como personagem do anúncio. "
        f"Ambiente de {pedido.nicho.strip()}. Produto em destaque: {pedido.produto.strip()}. "
        "Sem texto, sem letras, sem logotipo, sem marca d'água."
    )


def _fala(gerada: CampanhaGerada) -> str:
    roteiro = gerada.roteiro_video
    texto = " ".join(
        parte.strip()
        for parte in (roteiro.gancho, roteiro.problema, roteiro.solucao, roteiro.cta)
        if parte.strip()
    )
    return texto[:4000]


async def _foto(prompt: str, destino: Path) -> None:
    imagem = await cliente_openai().images.generate(
        model="gpt-image-1-mini",
        prompt=prompt,
        size="1024x1024",
        quality="low",
        output_format="png",
    )
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


def _video(foto: Path, audio: Path, destino: Path) -> None:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    comando = [
        ffmpeg,
        "-y",
        "-loop",
        "1",
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
        str(destino),
    ]
    resultado = subprocess.run(comando, capture_output=True, text=True)
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
    audio: Path | None = None
    video: Path | None = None

    try:
        await _foto(_prompt_foto(pedido), foto)
    except Exception as erro:
        avisos.append(_aviso("A foto não saiu", erro))
        foto = None

    if acao == "video" and foto and gerada:
        audio = pasta / "audio.mp3"
        video = pasta / "video.mp4"
        try:
            await _audio(_fala(gerada), audio)
        except Exception as erro:
            avisos.append(_aviso("O áudio não saiu", erro))
            audio = None
        if audio:
            try:
                _video(foto, audio, video)
            except Exception as erro:
                avisos.append(_aviso("O vídeo não foi montado", erro))
                video = None
        else:
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
