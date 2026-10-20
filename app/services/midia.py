import base64
import asyncio
import subprocess
import uuid
from pathlib import Path

from app.schemas import CampanhaGerada, PedidoCampanha
from app.services.openai_campanha import cliente_openai

PASTA_MIDIA = Path(__file__).resolve().parent.parent.parent / "midia"


def _par(valor: int) -> int:
    return valor if valor % 2 == 0 else valor + 1


def _jpeg_instagram(origem: Path, destino: Path) -> None:
    import re

    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    consulta = subprocess.run([ffmpeg, "-i", str(origem)], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20)
    achado = None
    for linha in (consulta.stderr or "").splitlines():
        if "Video:" in linha:
            achado = re.search(r"(\d+)x(\d+)", linha)
            if achado:
                break
    if achado is None:
        raise ValueError("A foto não pôde ser lida.")
    largura, altura = int(achado.group(1)), int(achado.group(2))
    if min(largura, altura) < 320:
        fator = 320 / min(largura, altura)
        largura = int(largura * fator)
        altura = int(altura * fator)
    proporcao = largura / altura
    if proporcao < 0.8:
        largura_final, altura_final = _par(int(altura * 0.8)), _par(altura)
    elif proporcao > 1.91:
        largura_final, altura_final = _par(largura), _par(int(largura / 1.91))
    else:
        largura_final, altura_final = _par(largura), _par(altura)
    filtro = f"scale={largura}:{altura}:flags=lanczos,pad={largura_final}:{altura_final}:(ow-iw)/2:(oh-ih)/2:white"
    comando = [ffmpeg, "-y", "-i", str(origem), "-vf", filtro, "-q:v", "3", str(destino)]
    resultado = _executar_ffmpeg(comando)
    if resultado.returncode != 0 or not destino.exists() or destino.stat().st_size < 100:
        raise ValueError("A foto não pôde ser preparada para o Instagram.")


def _jpeg_story(origem: Path, destino: Path) -> None:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    filtro = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
    comando = [ffmpeg, "-y", "-i", str(origem), "-vf", filtro, "-q:v", "3", str(destino)]
    resultado = _executar_ffmpeg(comando)
    if resultado.returncode != 0 or not destino.exists() or destino.stat().st_size < 100:
        raise ValueError("A foto do Story não pôde ser preparada.")


def arquivo_story(caminho: str) -> str:
    caminho = caminho.strip().split("?", 1)[0]
    if not caminho.startswith("/midia/"):
        return caminho
    partes = caminho.strip("/").split("/")
    if len(partes) != 3 or not partes[1] or not partes[2] or ".." in partes:
        raise RuntimeError("A foto do Story não foi achada. Anexe de novo.")
    origem = PASTA_MIDIA / partes[1] / partes[2]
    destino = PASTA_MIDIA / partes[1] / "story.jpg"
    if not origem.is_file():
        raise RuntimeError("A foto do Story não está mais no servidor. Anexe de novo.")
    _jpeg_story(origem, destino)
    return f"/midia/{partes[1]}/story.jpg"


FONTES_MARCA = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
)


def _arquivos_fonte() -> list[Path]:
    achadas = [Path(caminho) for caminho in FONTES_MARCA if Path(caminho).is_file()]
    pasta = Path("/usr/share/fonts")
    if pasta.is_dir():
        todas = sorted(pasta.rglob("*.ttf"))
        achadas += [fonte for fonte in todas if "bold" in fonte.name.lower()]
        achadas += todas
    return achadas


def _fonte_marca(tamanho: int):
    """Fonte do sistema; sem nenhuma, a fonte embutida do Pillow (que não tem acento)."""
    from PIL import ImageFont

    for caminho in _arquivos_fonte():
        try:
            return ImageFont.truetype(str(caminho), tamanho), False
        except OSError:
            continue
    return ImageFont.load_default(size=tamanho), True


def _sem_acento(texto: str) -> str:
    import unicodedata

    return "".join(letra for letra in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(letra))


def _carimbar(origem: Path, destino: Path, texto: str, margem_baixo: float = 0.03) -> None:
    from PIL import Image, ImageDraw

    with Image.open(origem) as aberta:
        foto = aberta.convert("RGBA")
    largura, altura = foto.size
    camada = Image.new("RGBA", foto.size, (0, 0, 0, 0))
    desenho = ImageDraw.Draw(camada)
    tamanho = max(14, round(altura * 0.035))
    for _ in range(2):
        fonte, embutida = _fonte_marca(tamanho)
        if embutida:
            texto = _sem_acento(texto)
        esquerda, topo, direita, baixo = desenho.textbbox((0, 0), texto, font=fonte)
        folga = max(4, round(tamanho * 0.4))
        caixa_largura = direita - esquerda + 2 * folga
        caixa_altura = baixo - topo + 2 * folga
        if caixa_largura <= largura * 0.6:
            break
        tamanho = max(10, int(tamanho * largura * 0.6 / caixa_largura))
    x = largura - round(largura * 0.03) - caixa_largura
    y = altura - round(altura * margem_baixo) - caixa_altura
    desenho.rounded_rectangle(
        (x, y, x + caixa_largura, y + caixa_altura),
        radius=max(2, round(tamanho * 0.3)),
        fill=(0, 0, 0, 115),
    )
    desenho.text((x + folga - esquerda, y + folga - topo), texto, font=fonte, fill=(255, 255, 255, 240))
    Image.alpha_composite(foto, camada).convert("RGB").save(destino, "JPEG", quality=92)
    if not destino.exists() or destino.stat().st_size < 100:
        raise RuntimeError("A marca d'água não entrou na foto.")


def _foto_local(caminho: str) -> str:
    from app.config import get_settings

    caminho = caminho.strip().split("?", 1)[0]
    base = get_settings().app_url.rstrip("/")
    if base and caminho.startswith(base + "/midia/"):
        caminho = caminho[len(base) :]
    if caminho.startswith("http://") or caminho.startswith("https://"):
        import urllib.request

        with urllib.request.urlopen(caminho, timeout=30) as resposta:
            dados = resposta.read(8_000_001)
        caminho, tipo = salvar_anexo(dados)
        if tipo != "foto":
            raise RuntimeError("A foto do post não é uma foto.")
    return caminho


def foto_com_marca(caminho: str, texto: str, story: bool = False) -> str:
    """Devolve a foto pronta para a Meta (feed, ou Story 1080x1920) com o texto da marca no canto."""
    texto = texto.strip()
    if not texto:
        return arquivo_story(caminho) if story else caminho
    try:
        local = _foto_local(caminho)
        if story:
            local = arquivo_story(local)
        partes = local.strip("/").split("/")
        if len(partes) != 3 or partes[0] != "midia" or not partes[1] or not partes[2] or ".." in partes:
            raise RuntimeError("A foto do post não foi achada. Anexe de novo.")
        origem = PASTA_MIDIA / partes[1] / partes[2]
        if not origem.is_file():
            raise RuntimeError("A foto do post não está mais no servidor. Anexe de novo.")
        nome = "story_marca.jpg" if story else f"{origem.stem}_marca.jpg"
        _carimbar(origem, origem.with_name(nome), texto, 0.12 if story else 0.03)
    except RuntimeError:
        raise
    except Exception as erro:
        raise RuntimeError("A marca d'água não pôde ser colocada na foto.") from erro
    return f"/midia/{partes[1]}/{nome}"


def salvar_anexo(dados: bytes) -> tuple[str, str]:
    if dados.startswith(b"\xff\xd8\xff"):
        ext, tipo = ".jpg", "foto"
    elif dados.startswith(b"\x89PNG\r\n\x1a\n"):
        ext, tipo = ".png", "foto"
    elif len(dados) > 12 and dados.startswith(b"RIFF") and dados[8:12] == b"WEBP":
        ext, tipo = ".webp", "foto"
    elif len(dados) > 12 and dados[4:8] == b"ftyp":
        ext, tipo = ".mp4", "video"
    elif dados.startswith(b"\x1a\x45\xdf\xa3"):
        ext, tipo = ".webm", "video"
    else:
        raise ValueError("Envie uma foto JPG, PNG ou WebP, ou um vídeo MP4.")
    limite = 25_000_000 if tipo == "video" else 8_000_000
    if len(dados) > limite:
        raise ValueError("A foto precisa ter até 8 MB. O vídeo, até 25 MB.")
    ident, pasta = nova_pasta()
    if tipo == "video":
        nome = f"anexo{ext}"
        (pasta / nome).write_bytes(dados)
        return f"/midia/{ident}/{nome}", tipo
    origem = pasta / f"origem{ext}"
    origem.write_bytes(dados)
    try:
        _jpeg_instagram(origem, pasta / "anexo.jpg")
    finally:
        origem.unlink(missing_ok=True)
    return f"/midia/{ident}/anexo.jpg", "foto"


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
    brief = pedido.brief_foto.strip()
    if brief:
        cena = (
            "Fotografia realista para post de rede social, luz natural do lugar. "
            f"{brief} "
            "Sem texto legível, sem letras, sem números na imagem, sem logotipo, sem marca d'água, sem menu de aplicativo com palavras."
        )
        if tem_referencia:
            return (
                "Use a foto enviada como o lugar real. Conserve o balcão, as cores e o ambiente. "
                + cena
            )
        return cena
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
    cliente = cliente_openai().with_options(timeout=180)
    pedido = {
        "model": modelo,
        "prompt": prompt,
        "size": "1024x1024",
        "quality": qualidade,
        "output_format": "png",
    }
    if referencia:
        nome = "referencia.jpg" if tipo == "image/jpeg" else "referencia.webp" if tipo == "image/webp" else "referencia.png"
        extra = {} if modelo.endswith("mini") else {"input_fidelity": "high"}
        imagem = await cliente.images.edit(
            image=(nome, referencia, tipo or "image/png"),
            **extra,
            **pedido,
        )
    else:
        imagem = await cliente.images.generate(**pedido)
    conteudo = imagem.data[0].b64_json if imagem.data else None
    if not conteudo:
        raise ValueError("A foto não veio.")
    destino.write_bytes(base64.b64decode(conteudo))


async def _audio(fala: str, destino: Path, instrucao: str = "") -> None:
    pedido = {
        "model": "gpt-4o-mini-tts",
        "voice": "coral",
        "input": fala,
        "response_format": "mp3",
    }
    if instrucao:
        pedido["instructions"] = instrucao
    try:
        resposta = await cliente_openai().audio.speech.create(**pedido)
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
    if resultado.returncode != 0 or not destino.exists() or destino.stat().st_size < 1000:
        raise RuntimeError("Não consegui criar a imagem do vídeo.")


def _executar_ffmpeg(comando: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            comando,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
    except subprocess.TimeoutExpired as erro:
        raise RuntimeError("O vídeo não foi montado.") from erro


def _duracao_audio(ffmpeg: str, audio: Path) -> str:
    import re

    consulta = subprocess.run([ffmpeg, "-i", str(audio)], capture_output=True, text=True, timeout=20)
    achado = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", consulta.stderr or "")
    if not achado:
        return "20"
    horas, minutos, segundos = achado.groups()
    total = int(horas) * 3600 + int(minutos) * 60 + float(segundos)
    if total <= 0:
        return "20"
    return f"{total + 0.2:.2f}"


def _video(foto: Path, audio: Path, destino: Path) -> None:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    duracao = _duracao_audio(ffmpeg, audio)
    comando = [
        ffmpeg,
        "-nostdin",
        "-y",
        "-loop",
        "1",
        "-framerate",
        "25",
        "-t",
        duracao,
        "-i",
        str(foto),
        "-i",
        str(audio),
        "-t",
        duracao,
        "-c:v",
        "libx264",
        "-threads",
        "1",
        "-preset",
        "ultrafast",
        "-tune",
        "stillimage",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(destino),
    ]
    resultado = _executar_ffmpeg(comando)
    if resultado.returncode != 0 or not destino.exists() or destino.stat().st_size < 1000:
        raise RuntimeError("O vídeo não foi montado.")


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
                await asyncio.to_thread(_quadro, foto)
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
            await asyncio.to_thread(_video, foto, audio, video)
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


async def foto_da_cena(cena: str) -> str:
    pedido = PedidoCampanha(
        nicho="bares, adegas e restaurantes",
        produto=(cena.strip()[:200] or "o movimento do bar"),
        acao="foto",
        brief_foto=cena.strip()[:800],
    )
    midia = await montar_midia(pedido, None, "foto")
    url = midia.get("foto_url") or ""
    if not url:
        raise RuntimeError(midia.get("aviso_midia") or "A foto não saiu.")
    partes = url.strip("/").split("/")
    if len(partes) < 2:
        raise RuntimeError("A foto não saiu.")
    ident = partes[1]
    destino = PASTA_MIDIA / ident / "anexo.jpg"
    await asyncio.to_thread(_jpeg_instagram, PASTA_MIDIA / ident / "foto.png", destino)
    return f"/midia/{ident}/anexo.jpg"


def _video_cenas(fotos: list[Path], audio: Path, destino: Path) -> None:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    duracao = float(_duracao_audio(ffmpeg, audio))
    cada = max(duracao / len(fotos), 0.8)
    total = f"{cada * len(fotos):.2f}"
    comando = [ffmpeg, "-nostdin", "-y"]
    for foto in fotos:
        comando += ["-loop", "1", "-framerate", "25", "-t", f"{cada:.2f}", "-i", str(foto)]
    comando += ["-i", str(audio)]
    filtros = []
    for indice in range(len(fotos)):
        filtros.append(f"[{indice}:v]scale=1024:1024,format=yuv420p,setsar=1[v{indice}]")
    entradas = "".join(f"[v{indice}]" for indice in range(len(fotos)))
    filtro = ";".join(filtros) + f";{entradas}concat=n={len(fotos)}:v=1:a=0[v]"
    comando += [
        "-filter_complex",
        filtro,
        "-map",
        "[v]",
        "-map",
        f"{len(fotos)}:a",
        "-c:v",
        "libx264",
        "-threads",
        "1",
        "-preset",
        "ultrafast",
        "-tune",
        "stillimage",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-t",
        total,
        "-movflags",
        "+faststart",
        str(destino),
    ]
    resultado = _executar_ffmpeg(comando)
    if resultado.returncode != 0 or not destino.exists() or destino.stat().st_size < 1000:
        raise RuntimeError("O vídeo não foi montado.")


async def montar_historia(
    nicho: str,
    cenas: list[tuple[str, str]],
    fala: str,
    referencia: bytes | None = None,
    tipo: str | None = None,
) -> dict[str, str | None]:
    ident, pasta = nova_pasta()
    avisos: list[str] = []
    fotos: list[Path] = []

    async def uma_cena(indice: int, descricao: str) -> Path | None:
        destino = pasta / f"cena-{indice}.png"
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
            return destino
        except Exception as erro:
            avisos.append(_aviso(f"A cena {indice + 1} não saiu", erro))
            return None

    audio = pasta / "audio.mp3"
    video = pasta / "video.mp4"

    async def narrar() -> None:
        await _audio(
            fala,
            audio,
            "Narração de uma história curta em português do Brasil. Voz calma, próxima, como quem conta o que aconteceu. Sem tom de comercial.",
        )

    prontas, audio_resultado = await asyncio.gather(
        asyncio.gather(*(uma_cena(indice, descricao) for indice, (_, descricao) in enumerate(cenas))),
        narrar(),
        return_exceptions=True,
    )
    if isinstance(prontas, Exception):
        avisos.append(_aviso("As cenas não saíram", prontas))
        prontas = []
    fotos = [foto for foto in prontas if foto is not None and foto.exists()]
    if not fotos:
        quadro = pasta / "cena-0.png"
        try:
            await asyncio.to_thread(_quadro, quadro)
            fotos = [quadro]
        except Exception as erro:
            avisos.append(_aviso("A imagem do vídeo não saiu", erro))
    if isinstance(audio_resultado, Exception):
        avisos.append(_aviso("O áudio não saiu", audio_resultado))
        audio = None
    elif not audio.exists():
        audio = None

    if audio is not None and fotos:
        try:
            if len(fotos) == 1:
                await asyncio.to_thread(_video, fotos[0], audio, video)
            else:
                await asyncio.to_thread(_video_cenas, fotos, audio, video)
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
        "foto_url": url(fotos[0], fotos[0].name) if fotos else None,
        "audio_url": url(audio, "audio.mp3"),
        "video_url": url(video, "video.mp4"),
        "aviso_midia": " ".join(avisos) or None,
    }
