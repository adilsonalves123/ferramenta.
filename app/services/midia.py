import base64
import asyncio
import subprocess
import uuid
from pathlib import Path

from app.schemas import PedidoCampanha
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
        raise ValueError("Envie o vídeo em MP4.")
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


def _prompt_foto(pedido: PedidoCampanha, tem_referencia: bool) -> str:
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
        raise RuntimeError("A foto não pôde ser preparada.") from erro


async def montar_midia(pedido: PedidoCampanha) -> dict[str, str | None]:
    ident, pasta = nova_pasta()
    foto = pasta / "foto.png"
    aviso: str | None = None
    try:
        referencia, tipo = _referencia(pedido)
        await _foto(_prompt_foto(pedido, referencia is not None), foto, referencia, tipo, "gpt-image-1", "medium")
    except Exception as erro:
        aviso = _aviso("A foto não saiu", erro)
    return {
        "foto_url": f"/midia/{ident}/foto.png" if foto.exists() else None,
        "audio_url": None,
        "video_url": None,
        "aviso_midia": aviso,
    }


async def foto_da_cena(cena: str) -> str:
    pedido = PedidoCampanha(
        nicho="bares, adegas e restaurantes",
        produto=(cena.strip()[:200] or "o movimento do bar"),
        acao="foto",
        brief_foto=cena.strip()[:800],
    )
    midia = await montar_midia(pedido)
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
