"""Desenho das fotos com o Pillow: JPEG aceito pelo Instagram, cartão do print, Story e marca d'água."""

import io
import unicodedata
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

FONTE_PROPRIA = Path(__file__).resolve().parent.parent / "fontes" / "DejaVuSans-Bold.ttf"
FONTES_SISTEMA = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
)

FEED_LARGURA = 1080
CARTAO = (1080, 1350)
STORY = (1080, 1920)
LADO_MEMORIA = 1600
LADO_MINIATURA = 360


@lru_cache
def _caminho_fonte() -> str:
    for caminho in (FONTE_PROPRIA, *map(Path, FONTES_SISTEMA)):
        if caminho.is_file():
            return str(caminho)
    return ""


@lru_cache(maxsize=64)
def fonte(tamanho: int) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, bool]:
    """Fonte negrito; sem nenhuma, a embutida do Pillow (que não tem acento)."""
    caminho = _caminho_fonte()
    if caminho:
        try:
            return ImageFont.truetype(caminho, tamanho), False
        except OSError:
            pass
    return ImageFont.load_default(size=tamanho), True


def _sem_acento(texto: str) -> str:
    return "".join(letra for letra in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(letra))


def abrir(dados: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(dados)) as aberta:
            aberta.load()
            imagem = ImageOps.exif_transpose(aberta)
            if imagem.mode in ("RGBA", "LA", "P"):
                fundo = Image.new("RGB", imagem.size, (255, 255, 255))
                rgba = imagem.convert("RGBA")
                fundo.paste(rgba, mask=rgba.split()[-1])
                return fundo
            return imagem.convert("RGB")
    except Exception as erro:
        raise ValueError("A foto não pôde ser lida. Use JPG, PNG ou WebP.") from erro


def para_jpeg(imagem: Image.Image, qualidade: int = 85) -> bytes:
    saida = io.BytesIO()
    imagem.convert("RGB").save(saida, "JPEG", quality=qualidade, optimize=True, progressive=True)
    return saida.getvalue()


def reduzir(imagem: Image.Image, lado_max: int) -> Image.Image:
    largura, altura = imagem.size
    maior = max(largura, altura)
    if maior <= lado_max:
        return imagem
    fator = lado_max / maior
    return imagem.resize((max(1, round(largura * fator)), max(1, round(altura * fator))), Image.LANCZOS)


def normalizar_memoria(dados: bytes) -> tuple[bytes, bytes, int, int]:
    """Foto da memória: JPEG de até 1600 px no lado maior e a miniatura para a grade."""
    imagem = reduzir(abrir(dados), LADO_MEMORIA)
    miniatura = reduzir(imagem, LADO_MINIATURA)
    return para_jpeg(imagem, 85), para_jpeg(miniatura, 78), imagem.width, imagem.height


def para_visao(dados: bytes, lado: int = 768) -> bytes:
    return para_jpeg(reduzir(abrir(dados), lado), 80)


def _cobrir(imagem: Image.Image, largura: int, altura: int) -> Image.Image:
    return ImageOps.fit(imagem, (largura, altura), Image.LANCZOS)


def fundo_desfocado(imagem: Image.Image, largura: int, altura: int, brilho: float = 0.5) -> Image.Image:
    pequeno = _cobrir(imagem, max(1, largura // 6), max(1, altura // 6))
    pequeno = pequeno.filter(ImageFilter.GaussianBlur(radius=8))
    fundo = pequeno.resize((largura, altura), Image.BILINEAR).filter(ImageFilter.GaussianBlur(radius=6))
    return ImageEnhance.Brightness(fundo).enhance(brilho)


def instagram(imagem: Image.Image) -> Image.Image:
    """Proporção que o feed aceita (de 4:5 a 1,91:1), com até 1080 px de largura."""
    largura, altura = imagem.size
    proporcao = largura / altura
    alvo = min(max(proporcao, 0.8), 1.91)
    if abs(alvo - proporcao) > 0.001:
        if alvo > proporcao:
            nova_altura = round(largura / alvo)
            perda = 1 - nova_altura / altura
            corte = (largura, nova_altura)
        else:
            nova_largura = round(altura * alvo)
            perda = 1 - nova_largura / largura
            corte = (nova_largura, altura)
        if perda <= 0.2:
            imagem = ImageOps.fit(imagem, corte, Image.LANCZOS)
        else:
            final = (round(altura * alvo), altura) if alvo > proporcao else (largura, round(largura / alvo))
            fundo = fundo_desfocado(imagem, *final, brilho=0.6)
            fundo.paste(imagem, ((final[0] - largura) // 2, (final[1] - altura) // 2))
            imagem = fundo
    largura, altura = imagem.size
    if largura > FEED_LARGURA:
        imagem = imagem.resize((FEED_LARGURA, round(altura * FEED_LARGURA / largura)), Image.LANCZOS)
    elif min(largura, altura) < 320:
        fator = 320 / min(largura, altura)
        imagem = imagem.resize((round(largura * fator), round(altura * fator)), Image.LANCZOS)
    return imagem


def _linhas(desenho: ImageDraw.ImageDraw, texto: str, letra, largura_max: int) -> list[str]:
    linhas: list[str] = []
    atual = ""
    for palavra in texto.split():
        tentativa = f"{atual} {palavra}".strip()
        if not atual or desenho.textlength(tentativa, font=letra) <= largura_max:
            atual = tentativa
        else:
            linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


def _bloco_texto(
    desenho: ImageDraw.ImageDraw,
    texto: str,
    largura_max: int,
    tamanho: int,
    max_linhas: int = 3,
    minimo: int = 34,
) -> tuple[list[str], object, int]:
    """Quebra o texto em até max_linhas, diminuindo a letra até caber."""
    while True:
        letra, embutida = fonte(tamanho)
        corpo = _sem_acento(texto) if embutida else texto
        linhas = _linhas(desenho, corpo, letra, largura_max)
        largo = max((desenho.textlength(linha, font=letra) for linha in linhas), default=0)
        if (len(linhas) <= max_linhas and largo <= largura_max) or tamanho <= minimo:
            return linhas[:max_linhas], letra, tamanho
        tamanho = max(minimo, int(tamanho * 0.9))


def _escrever_centro(
    imagem: Image.Image,
    linhas: list[str],
    letra,
    tamanho: int,
    topo: int,
    cor=(255, 255, 255),
    sombra: bool = True,
) -> int:
    """Escreve as linhas centralizadas a partir de 'topo' e devolve onde o texto acabou."""
    desenho = ImageDraw.Draw(imagem)
    passo = round(tamanho * 1.2)
    y = topo
    for linha in linhas:
        largura = desenho.textlength(linha, font=letra)
        x = (imagem.width - largura) / 2
        if sombra:
            desenho.text((x + 3, y + 4), linha, font=letra, fill=(0, 0, 0))
        desenho.text((x, y), linha, font=letra, fill=cor)
        y += passo
    return y


def _cantos_redondos(imagem: Image.Image, raio: int) -> Image.Image:
    mascara = Image.new("L", imagem.size, 0)
    ImageDraw.Draw(mascara).rounded_rectangle((0, 0, imagem.width - 1, imagem.height - 1), radius=raio, fill=255)
    saida = imagem.convert("RGBA")
    saida.putalpha(mascara)
    return saida


def _colar_com_sombra(fundo: Image.Image, peca: Image.Image, x: int, y: int, raio: int) -> None:
    redonda = _cantos_redondos(peca, raio)
    sombra = Image.new("RGBA", (peca.width + 80, peca.height + 80), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle((40, 48, 40 + peca.width, 48 + peca.height), radius=raio, fill=(0, 0, 0, 150))
    sombra = sombra.filter(ImageFilter.GaussianBlur(radius=18))
    fundo.paste(sombra, (x - 40, y - 40), sombra)
    fundo.paste(redonda, (x, y), redonda)


def _caber(imagem: Image.Image, largura: int, altura: int) -> Image.Image:
    fator = min(largura / imagem.width, altura / imagem.height)
    return imagem.resize((max(1, round(imagem.width * fator)), max(1, round(imagem.height * fator))), Image.LANCZOS)


def _cor_media(imagem: Image.Image) -> tuple[int, int, int]:
    r, g, b = imagem.resize((1, 1), Image.BILINEAR).getpixel((0, 0))[:3]
    return (int(r * 0.35), int(g * 0.35), int(b * 0.35))


def cartao_print(imagem: Image.Image, gancho: str, rodape: str) -> Image.Image:
    """Feed 4:5 do print do app: frase no alto, print no meio sobre fundo desfocado, site embaixo."""
    largura, altura = CARTAO
    fundo = fundo_desfocado(imagem, largura, altura, brilho=0.45)
    tinta = Image.new("RGB", (largura, altura), _cor_media(imagem))
    fundo = Image.blend(fundo, tinta, 0.45)
    desenho = ImageDraw.Draw(fundo)
    margem = 60
    topo = 70
    if gancho.strip():
        linhas, letra, tamanho = _bloco_texto(desenho, gancho.strip(), largura - 2 * margem, 84)
        topo = _escrever_centro(fundo, linhas, letra, tamanho, 70) + 30
    rodape = rodape.strip()
    base = altura - 130
    if rodape:
        letra_rodape, embutida = fonte(38)
        rodape = _sem_acento(rodape) if embutida else rodape
        base = altura - 215
        largura_rodape = desenho.textlength(rodape, font=letra_rodape)
        desenho.text(((largura - largura_rodape) / 2, altura - 190), rodape, font=letra_rodape, fill=(255, 255, 255))
    caixa_altura = max(200, base - topo)
    peca = _caber(imagem, largura - 2 * margem - 60, caixa_altura)
    x = (largura - peca.width) // 2
    y = topo + (caixa_altura - peca.height) // 2
    _colar_com_sombra(fundo, peca, x, y, 26)
    return fundo


def story(imagem: Image.Image, gancho: str = "", cta: str = "", marca_dagua: str = "") -> Image.Image:
    """Story 1080x1920: a foto na largura toda sobre uma cópia desfocada e escura dela mesma."""
    largura, altura = STORY
    fundo = fundo_desfocado(imagem, largura, altura, brilho=0.42)
    desenho = ImageDraw.Draw(fundo)
    topo_seguro, base_segura = 230, altura - 250
    topo = topo_seguro
    if gancho.strip():
        linhas, letra, tamanho = _bloco_texto(desenho, gancho.strip(), largura - 120, 80)
        topo = _escrever_centro(fundo, linhas, letra, tamanho, topo_seguro) + 40
    base = base_segura
    pilula = None
    if cta.strip():
        letra_cta, embutida = fonte(48)
        texto_cta = _sem_acento(cta.strip()) if embutida else cta.strip()
        esquerda, cima, direita, baixo = desenho.textbbox((0, 0), texto_cta, font=letra_cta)
        folga_x, folga_y = 44, 24
        caixa_l = direita - esquerda + 2 * folga_x
        caixa_a = baixo - cima + 2 * folga_y
        base = base_segura - caixa_a - 40
        pilula = (texto_cta, letra_cta, esquerda, cima, caixa_l, caixa_a)
    selo = None
    if marca_dagua.strip():
        letra_selo, embutida = fonte(34)
        texto_selo = _sem_acento(marca_dagua.strip()) if embutida else marca_dagua.strip()
        esquerda_s, cima_s, direita_s, baixo_s = desenho.textbbox((0, 0), texto_selo, font=letra_selo)
        selo = (texto_selo, letra_selo, esquerda_s, cima_s, direita_s - esquerda_s + 32, baixo_s - cima_s + 20)
        base -= selo[5] + 24
    espaco = max(300, base - topo)
    peca = imagem
    if peca.width != largura:
        peca = peca.resize((largura, max(1, round(peca.height * largura / peca.width))), Image.LANCZOS)
    if peca.height > espaco:
        peca = _caber(imagem, largura, espaco)
    x = (largura - peca.width) // 2
    y = topo + (espaco - peca.height) // 2
    fundo.paste(peca, (x, y))
    if selo:
        texto_selo, letra_selo, esquerda_s, cima_s, selo_l, selo_a = selo
        camada = Image.new("RGBA", fundo.size, (0, 0, 0, 0))
        sx, sy = (largura - selo_l) // 2, y + peca.height + 20
        ImageDraw.Draw(camada).rounded_rectangle((sx, sy, sx + selo_l, sy + selo_a), radius=12, fill=(0, 0, 0, 115))
        ImageDraw.Draw(camada).text((sx + 16 - esquerda_s, sy + 10 - cima_s), texto_selo, font=letra_selo, fill=(255, 255, 255, 240))
        fundo = Image.alpha_composite(fundo.convert("RGBA"), camada).convert("RGB")
        desenho = ImageDraw.Draw(fundo)
    if pilula:
        texto_cta, letra_cta, esquerda, cima, caixa_l, caixa_a = pilula
        cx = (largura - caixa_l) // 2
        cy = base_segura - caixa_a
        desenho.rounded_rectangle((cx, cy, cx + caixa_l, cy + caixa_a), radius=caixa_a // 2, fill=(255, 255, 255))
        desenho.text((cx + 44 - esquerda, cy + 24 - cima), texto_cta, font=letra_cta, fill=(17, 24, 39))
    return fundo


def carimbar(imagem: Image.Image, texto: str, margem_baixo: float = 0.03, caixa: tuple[int, int, int, int] | None = None) -> Image.Image:
    """Marca d'água no canto de baixo à direita (da foto toda, ou da 'caixa' onde a foto está)."""
    texto = texto.strip()
    if not texto:
        return imagem
    foto = imagem.convert("RGBA")
    x0, y0, x1, y1 = caixa or (0, 0, foto.width, foto.height)
    largura, altura = x1 - x0, y1 - y0
    camada = Image.new("RGBA", foto.size, (0, 0, 0, 0))
    desenho = ImageDraw.Draw(camada)
    tamanho = max(14, round(altura * 0.035))
    for _ in range(2):
        letra, embutida = fonte(tamanho)
        corpo = _sem_acento(texto) if embutida else texto
        esquerda, topo, direita, baixo = desenho.textbbox((0, 0), corpo, font=letra)
        folga = max(4, round(tamanho * 0.4))
        caixa_largura = direita - esquerda + 2 * folga
        caixa_altura = baixo - topo + 2 * folga
        if caixa_largura <= largura * 0.6:
            break
        tamanho = max(10, int(tamanho * largura * 0.6 / caixa_largura))
    x = x1 - round(largura * 0.03) - caixa_largura
    y = y1 - round(altura * margem_baixo) - caixa_altura
    desenho.rounded_rectangle(
        (x, y, x + caixa_largura, y + caixa_altura),
        radius=max(2, round(tamanho * 0.3)),
        fill=(0, 0, 0, 115),
    )
    desenho.text((x + folga - esquerda, y + folga - topo), corpo, font=letra, fill=(255, 255, 255, 240))
    return Image.alpha_composite(foto, camada).convert("RGB")
