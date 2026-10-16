import asyncio
import re
import subprocess
import tempfile
from pathlib import Path

from app.config import get_settings
from app.schemas import ClonagemGerada, ClonagemTexto, PedidoClonador
from app.services.openai_campanha import cliente_openai

SISTEMA = """Você reescreve a fala de um vídeo em português do Brasil correto.
Você recebeu a transcrição real. Siga a ordem das ideias dessa fala.
Corrija erros de português e troque a redação, sem copiar frases inteiras.
Se o campo de produto for um pedido de como refazer o vídeo, obedeça esse pedido.
Se for o nome de um produto, adapte os exemplos a ele, sem inventar preço, telefone, cidade ou promessa.
Entregue quatro falas curtas: gancho, retencao, desenvolvimento e cta.
referencia: repita o link ou o nome do arquivo.
titulo: uma frase curta do assunto.
legenda: o texto do post, curto."""

_YOUTUBE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|embed/|live/))([A-Za-z0-9_-]{11})"
)
_LIMITE_ARQUIVO = 25 * 1024 * 1024


def id_youtube(link: str) -> str | None:
    texto = link.strip()
    achado = _YOUTUBE.search(texto)
    if achado:
        return achado.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", texto):
        return texto
    return None


def _juntar_legenda(obtido) -> str:
    partes: list[str] = []
    for trecho in obtido:
        if isinstance(trecho, dict):
            texto = trecho.get("text", "")
        else:
            texto = getattr(trecho, "text", "")
        if texto:
            partes.append(str(texto).replace("\n", " "))
    return " ".join(partes).strip()


def _legenda_youtube(video_id: str) -> str:
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    try:
        obtido = api.fetch(video_id, languages=["pt-BR", "pt", "en"])
    except TypeError:
        obtido = YouTubeTranscriptApi.get_transcript(video_id, languages=["pt-BR", "pt", "en"])
    fala = _juntar_legenda(obtido)
    if len(fala) < 20:
        raise ValueError("A legenda desse vídeo veio vazia. Envie o arquivo, até 25 MB.")
    return fala[:8000]


def _extrair_audio(dados: bytes, nome: str) -> bytes:
    import imageio_ffmpeg

    sufixo = Path(nome).suffix.lower()
    if sufixo not in {".mp4", ".webm", ".mov", ".mkv", ".m4a", ".mp3", ".wav"}:
        sufixo = ".mp4"
    with tempfile.TemporaryDirectory() as pasta:
        origem = Path(pasta) / f"origem{sufixo}"
        destino = Path(pasta) / "audio.mp3"
        origem.write_bytes(dados)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        resultado = subprocess.run(
            [ffmpeg, "-y", "-i", str(origem), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(destino)],
            capture_output=True,
            text=True,
        )
        if resultado.returncode != 0 or not destino.exists():
            raise ValueError("Não consegui tirar o áudio desse arquivo.")
        return destino.read_bytes()


async def _transcrever_arquivo(dados: bytes, nome: str) -> str:
    if len(dados) > _LIMITE_ARQUIVO:
        raise ValueError("O vídeo precisa ter até 25 MB.")
    if len(dados) < 1000:
        raise ValueError("O arquivo de vídeo está vazio.")
    audio = await asyncio.to_thread(_extrair_audio, dados, nome)
    cliente = cliente_openai().with_options(timeout=120)
    arquivo = ("audio.mp3", audio, "audio/mpeg")
    try:
        resposta = await cliente.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=arquivo,
            language="pt",
        )
    except Exception:
        resposta = await cliente.audio.transcriptions.create(
            model="whisper-1",
            file=arquivo,
            language="pt",
        )
    texto = (getattr(resposta, "text", "") or "").strip()
    if len(texto) < 20:
        raise ValueError("Não consegui ouvir a fala desse arquivo.")
    return texto[:8000]


async def ouvir_video(link: str, arquivo: bytes | None, nome: str) -> str:
    if arquivo:
        return await _transcrever_arquivo(arquivo, nome)
    video_id = id_youtube(link)
    if not video_id:
        raise ValueError("Cole um link do YouTube ou envie o arquivo do vídeo.")
    try:
        return await asyncio.to_thread(_legenda_youtube, video_id)
    except ValueError:
        raise
    except Exception as erro:
        nome_erro = type(erro).__name__
        if nome_erro in {"IpBlocked", "RequestBlocked"}:
            raise ValueError(
                "O YouTube bloqueou a leitura deste servidor. Envie o arquivo do vídeo, até 25 MB."
            ) from erro
        if nome_erro in {"NoTranscriptFound", "TranscriptsDisabled", "VideoUnavailable"}:
            raise ValueError("Esse YouTube não tem legenda. Envie o arquivo do vídeo, até 25 MB.") from erro
        raise ValueError(
            "Não consegui ler a legenda desse YouTube. Envie o arquivo do vídeo, até 25 MB."
        ) from erro


async def clonar_video(pedido: PedidoClonador, transcricao: str) -> ClonagemGerada:
    link = pedido.link_video.strip()
    arquivo = pedido.nome_arquivo.strip()
    referencia = link or arquivo
    resposta = await cliente_openai().with_options(timeout=120).chat.completions.parse(
        model=get_settings().openai_model,
        messages=[
            {"role": "system", "content": SISTEMA},
            {
                "role": "user",
                "content": (
                    f"Nicho: {pedido.nicho.strip()}\n"
                    f"Produto ou pedido: {pedido.produto.strip()}\n"
                    f"Referência: {referencia}\n"
                    f"Transcrição:\n{transcricao}"
                ),
            },
        ],
        response_format=ClonagemTexto,
    )
    mensagem = resposta.choices[0].message
    if mensagem.refusal:
        raise ValueError(mensagem.refusal)
    if mensagem.parsed is None:
        raise ValueError("A IA não devolveu o roteiro no formato esperado.")
    gerada = mensagem.parsed
    if not gerada.referencia.strip():
        gerada.referencia = referencia
    return ClonagemGerada(**gerada.model_dump())
