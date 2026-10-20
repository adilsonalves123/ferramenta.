"""Moldes de arte: a foto vira a peça do post (feed 1080x1350 e Story 1080x1920), com o texto sempre cabendo."""

import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.services import arte

PASTA_FONTES = Path(__file__).resolve().parent.parent / "fontes"
ARQUIVOS_FONTE = {
    "forte": "Montserrat-ExtraBold.ttf",
    "negrito": "Montserrat-Bold.ttf",
    "medio": "Montserrat-SemiBold.ttf",
}
VERSAO = "2"
FEED = (1080, 1350)
STORY = (1080, 1920)
SEGURO_STORY = (250, 300)
MODELOS = ("faixa", "selo", "destaque", "antes_depois", "tela_app", "story_chamada")
NOMES_MODELOS = {
    "faixa": "Faixa",
    "selo": "Selo",
    "destaque": "Destaque",
    "antes_depois": "Antes e depois",
    "tela_app": "Imagem em destaque",
    "story_chamada": "Chamada",
}
COR_PRINCIPAL = "#1B2A41"
COR_DESTAQUE = "#E63946"
BRANCO = (255, 255, 255)
GRAFITE = (17, 24, 39)
MARGEM = 64


@dataclass
class Identidade:
    principal: tuple[int, int, int] = (27, 42, 65)
    destaque: tuple[int, int, int] = (230, 57, 70)
    logo: Image.Image | None = None
    assinatura: str = ""


@dataclass
class Textos:
    titulo: str = ""
    subtitulo: str = ""
    selo: str = ""
    chamada: str = ""
    antes: str = ""
    cor: str = "destaque"


@dataclass
class Bloco:
    linhas: list[str]
    letra: object
    tamanho: int
    passo: int
    largura: int
    topo: int
    fundo: int

    @property
    def altura(self) -> int:
        return self.topo + self.passo * (len(self.linhas) - 1) + self.fundo


def cor_hex(valor: str, padrao: str) -> tuple[int, int, int]:
    texto = (valor or "").strip().lstrip("#")
    if len(texto) != 6 or any(c not in "0123456789abcdefABCDEF" for c in texto):
        texto = padrao.lstrip("#")
    return tuple(int(texto[i : i + 2], 16) for i in (0, 2, 4))


def hex_valido(valor: str, padrao: str) -> str:
    r, g, b = cor_hex(valor, padrao)
    return f"#{r:02X}{g:02X}{b:02X}"


def _luz(cor: tuple[int, int, int]) -> float:
    def canal(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = cor[:3]
    return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)


def tinta(fundo: tuple[int, int, int]) -> tuple[int, int, int]:
    """Branco sobre cor forte (o texto da arte é grande e negrito); grafite só em fundo claro."""
    return BRANCO if 1.05 / (_luz(fundo) + 0.05) >= 2.6 else GRAFITE


def _misturar(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a[:3], b[:3]))


def _escuro(cor: tuple[int, int, int], quanto: float = 0.35) -> tuple[int, int, int]:
    return _misturar(cor, (0, 0, 0), quanto)


@lru_cache(maxsize=256)
def _letra(peso: str, tamanho: int):
    caminho = PASTA_FONTES / ARQUIVOS_FONTE.get(peso, ARQUIVOS_FONTE["forte"])
    try:
        return ImageFont.truetype(str(caminho), tamanho), False
    except OSError:
        return arte.fonte(tamanho)


def _comprimento(letra, texto: str) -> float:
    return letra.getlength(texto)


def _guloso(palavras: list[str], letra, largura: float, partir: bool = False) -> list[str]:
    linhas: list[str] = []
    atual = ""
    for palavra in palavras:
        while partir and _comprimento(letra, palavra) > largura and len(palavra) > 1:
            corte = len(palavra) - 1
            while corte > 1 and _comprimento(letra, palavra[:corte] + "-") > largura:
                corte -= 1
            if atual:
                linhas.append(atual)
                atual = ""
            linhas.append(palavra[:corte] + "-")
            palavra = palavra[corte:]
        tentativa = f"{atual} {palavra}".strip()
        if not atual or _comprimento(letra, tentativa) <= largura:
            atual = tentativa
        else:
            linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


def _quebrar(texto: str, letra, largura: float, partir: bool = False) -> list[str]:
    """Quebra em linhas e equilibra o tamanho delas (título bonito não deixa uma palavra sozinha).
    Palavra maior que a linha só é partida com hífen quando 'partir' (na letra mínima)."""
    palavras = texto.split()
    linhas = _guloso(palavras, letra, largura, partir)
    if len(linhas) <= 1:
        return linhas
    melhor = linhas
    alvo = largura
    while alvo > largura * 0.45:
        alvo *= 0.95
        tentativa = _guloso(palavras, letra, alvo, partir)
        if len(tentativa) != len(linhas):
            break
        melhor = tentativa
    return melhor


def _montar_bloco(linhas: list[str], letra, tamanho: int, entrelinha: float) -> Bloco:
    if not linhas:
        return Bloco([], letra, tamanho, 0, 0, 0, 0)
    topo = -min(letra.getbbox(linhas[0], anchor="ls")[1], letra.getbbox("H", anchor="ls")[1])
    fundo = max(0, letra.getbbox(linhas[-1], anchor="ls")[3])
    largura = round(max(_comprimento(letra, linha) for linha in linhas))
    return Bloco(linhas, letra, tamanho, round(tamanho * entrelinha), largura, topo, fundo)


def ajustar(
    texto: str,
    peso: str,
    largura: int,
    altura: int,
    maximo: int,
    minimo: int,
    max_linhas: int,
    entrelinha: float = 1.06,
    caixa_alta: bool = False,
) -> Bloco:
    """Diminui a letra até o texto caber na caixa; no mínimo, corta com reticências."""
    texto = " ".join((texto or "").split())
    if caixa_alta:
        texto = texto.upper()
    tamanho = maximo
    while True:
        letra, sem_acento = _letra(peso, tamanho)
        corpo = arte._sem_acento(texto) if sem_acento else texto
        linhas = _quebrar(corpo, letra, largura, partir=tamanho <= minimo)
        bloco = _montar_bloco(linhas, letra, tamanho, entrelinha)
        if len(linhas) <= max_linhas and bloco.largura <= largura and bloco.altura <= altura:
            return bloco
        if tamanho <= minimo:
            break
        tamanho = max(minimo, int(tamanho * 0.93))
    passo = round(tamanho * entrelinha)
    cabem = max(1, min(max_linhas, 1 + max(0, (altura - tamanho)) // max(1, passo)))
    if len(linhas) > cabem:
        linhas = linhas[:cabem]
        ultima = linhas[-1]
        while ultima and _comprimento(letra, ultima + "…") > largura:
            ultima = ultima[:-1].rstrip()
        linhas[-1] = (ultima + "…") if ultima else "…"
    return _montar_bloco(linhas, letra, tamanho, entrelinha)


def escrever(
    camada: Image.Image,
    bloco: Bloco,
    x: float,
    y: float,
    cor,
    alinhar: str = "esquerda",
    largura: float = 0,
) -> None:
    """Escreve o bloco com o topo visual em y. Em 'centro', x é o centro; em 'direita', x é a borda direita."""
    desenho = ImageDraw.Draw(camada)
    base = y + bloco.topo
    for linha in bloco.linhas:
        comprimento = _comprimento(bloco.letra, linha)
        if alinhar == "centro":
            px = x - comprimento / 2
        elif alinhar == "direita":
            px = x - comprimento
        else:
            px = x
        desenho.text((px, base), linha, font=bloco.letra, fill=cor, anchor="ls")
        base += bloco.passo


def _sombra_de(camada: Image.Image, raio: int = 10, opacidade: float = 0.55, desloc: tuple[int, int] = (0, 6)) -> Image.Image:
    alfa = camada.getchannel("A").point(lambda v: int(v * opacidade))
    alfa = alfa.filter(ImageFilter.GaussianBlur(raio))
    sombra = Image.new("RGBA", camada.size, (0, 0, 0, 255))
    sombra.putalpha(alfa)
    saida = Image.new("RGBA", camada.size, (0, 0, 0, 0))
    saida.alpha_composite(sombra, desloc)
    return saida


def texto_com_sombra(
    tela: Image.Image,
    bloco: Bloco,
    x: float,
    y: float,
    cor,
    alinhar: str = "esquerda",
    raio: int = 10,
    opacidade: float = 0.6,
) -> None:
    camada = Image.new("RGBA", tela.size, (0, 0, 0, 0))
    escrever(camada, bloco, x, y, cor, alinhar)
    tela.alpha_composite(_sombra_de(camada, raio, opacidade, (0, 4)))
    tela.alpha_composite(camada)


def _mascara_redonda(tamanho: tuple[int, int], raio: int, escala: int = 3) -> Image.Image:
    largura, altura = tamanho
    grande = Image.new("L", (largura * escala, altura * escala), 0)
    ImageDraw.Draw(grande).rounded_rectangle(
        (0, 0, largura * escala - 1, altura * escala - 1), radius=raio * escala, fill=255
    )
    return grande.resize(tamanho, Image.LANCZOS)


def arredondar(imagem: Image.Image, raio: int) -> Image.Image:
    saida = imagem.convert("RGBA")
    alfa = _mascara_redonda(saida.size, raio)
    if imagem.mode == "RGBA":
        from PIL import ImageChops

        alfa = ImageChops.multiply(alfa, imagem.getchannel("A"))
    saida.putalpha(alfa)
    return saida


def caixa_redonda(tamanho: tuple[int, int], raio: int, cor) -> Image.Image:
    cor = tuple(cor)
    peca = Image.new("RGBA", tamanho, cor[:3] + ((cor[3],) if len(cor) == 4 else (255,)))
    alfa = _mascara_redonda(tamanho, raio)
    if len(cor) == 4:
        alfa = alfa.point(lambda v: v * cor[3] // 255)
    peca.putalpha(alfa)
    return peca


def colar_com_sombra(
    tela: Image.Image,
    peca: Image.Image,
    x: int,
    y: int,
    raio: int = 22,
    opacidade: float = 0.45,
    desloc: tuple[int, int] = (0, 14),
) -> None:
    folga = raio * 3
    camada = Image.new("RGBA", (peca.width + 2 * folga, peca.height + 2 * folga), (0, 0, 0, 0))
    camada.alpha_composite(peca, (folga, folga))
    sombra = _sombra_de(camada, raio, opacidade, desloc)
    tela.alpha_composite(sombra, (x - folga, y - folga))
    tela.alpha_composite(peca, (x, y))


def cobrir(foto: Image.Image, largura: int, altura: int, foco: tuple[float, float] = (0.5, 0.5)) -> Image.Image:
    fator = max(largura / foto.width, altura / foto.height)
    saida = ImageOps.fit(foto.convert("RGB"), (largura, altura), Image.LANCZOS, centering=foco)
    if fator > 1.25:
        saida = saida.filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=2))
    return saida.convert("RGBA")


def caber(foto: Image.Image, largura: int, altura: int) -> Image.Image:
    fator = min(largura / foto.width, altura / foto.height)
    return foto.resize((max(1, round(foto.width * fator)), max(1, round(foto.height * fator))), Image.LANCZOS)


def degrade(
    tela: Image.Image,
    cor: tuple[int, int, int],
    y0: int,
    y1: int,
    alfa0: int,
    alfa1: int,
    curva: float = 1.6,
) -> None:
    """Faixa em degradê de y0 a y1 (alfa0 em y0, alfa1 em y1)."""
    y0, y1 = max(0, y0), min(tela.height, y1)
    altura = y1 - y0
    if altura <= 1:
        return
    valores = []
    for i in range(altura):
        t = i / (altura - 1)
        if alfa1 >= alfa0:
            valores.append(int(alfa0 + (alfa1 - alfa0) * (t**curva)))
        else:
            valores.append(int(alfa1 + (alfa0 - alfa1) * ((1 - t) ** curva)))
    coluna = Image.new("L", (1, altura))
    coluna.putdata(valores)
    camada = Image.new("RGBA", (tela.width, altura), tuple(cor[:3]) + (255,))
    camada.putalpha(coluna.resize((tela.width, altura)))
    tela.alpha_composite(camada, (0, y0))


def pilula(
    tela: Image.Image,
    texto: str,
    x: float,
    y: float,
    fundo,
    cor_texto=None,
    tamanho: int = 40,
    largura_max: int = 700,
    alinhar: str = "esquerda",
    caixa_alta: bool = False,
    peso: str = "negrito",
    sombra: bool = True,
) -> tuple[int, int, int, int]:
    """Chamada em pílula. y é o topo. Devolve a caixa ocupada."""
    texto = (texto or "").strip()
    if not texto:
        return (int(x), int(y), int(x), int(y))
    folga_x = round(tamanho * 0.95)
    folga_y = round(tamanho * 0.62)
    bloco = ajustar(texto, peso, largura_max - 2 * folga_x, tamanho * 2, tamanho, 22, 1, caixa_alta=caixa_alta)
    letra_h = _montar_bloco(["H"], bloco.letra, bloco.tamanho, 1.0)
    altura_texto = letra_h.topo
    largura = bloco.largura + 2 * folga_x
    altura = altura_texto + 2 * folga_y
    if alinhar == "centro":
        x0 = round(x - largura / 2)
    elif alinhar == "direita":
        x0 = round(x - largura)
    else:
        x0 = round(x)
    y0 = round(y)
    peca = caixa_redonda((largura, altura), altura // 2, fundo)
    escrita = Image.new("RGBA", peca.size, (0, 0, 0, 0))
    ImageDraw.Draw(escrita).text(
        (folga_x, folga_y + altura_texto),
        bloco.linhas[0] if bloco.linhas else "",
        font=bloco.letra,
        fill=cor_texto or tinta(tuple(fundo[:3])),
        anchor="ls",
    )
    peca.alpha_composite(escrita)
    if sombra:
        colar_com_sombra(tela, peca, x0, y0, raio=12, opacidade=0.35, desloc=(0, 6))
    else:
        tela.alpha_composite(peca, (x0, y0))
    return (x0, y0, x0 + largura, y0 + altura)


def assinar(
    tela: Image.Image,
    ident: Identidade,
    x: float,
    y: float,
    alinhar: str = "esquerda",
    vertical: str = "topo",
    altura_max: int = 76,
    largura_max: int = 250,
) -> tuple[int, int, int, int]:
    """Logo da marca num chip branco; sem logo, o texto da marca d'água numa pílula escura."""
    if ident.logo is not None:
        logo = caber(ident.logo.convert("RGBA"), largura_max, altura_max)
        folga = 14
        chip = caixa_redonda((logo.width + 2 * folga, logo.height + 2 * folga), 18, (255, 255, 255, 238))
        chip.alpha_composite(logo, (folga, folga))
    elif ident.assinatura.strip():
        bloco = ajustar(ident.assinatura, "negrito", largura_max + 120, 60, 30, 20, 1)
        folga_x, folga_y = 22, 14
        largura = bloco.largura + 2 * folga_x
        altura = bloco.altura + 2 * folga_y
        chip = caixa_redonda((largura, altura), altura // 2, (0, 0, 0, 120))
        escrever(chip, bloco, folga_x, folga_y, (255, 255, 255, 245))
    else:
        return (int(x), int(y), int(x), int(y))
    x0 = round(x - chip.width) if alinhar == "direita" else round(x - chip.width / 2) if alinhar == "centro" else round(x)
    y0 = round(y - chip.height) if vertical == "base" else round(y)
    colar_com_sombra(tela, chip, x0, y0, raio=10, opacidade=0.25, desloc=(0, 4))
    return (x0, y0, x0 + chip.width, y0 + chip.height)


def _faixa_inclinada(
    tela: Image.Image,
    bloco: Bloco,
    cor: tuple[int, int, int],
    centro_y: int,
    angulo: float = 4,
    folga_y: int = 34,
    largura: int | None = None,
) -> int:
    """Faixa colorida inclinada atravessando a foto. Devolve a altura da faixa já girada."""
    largura = largura or round(tela.width * 1.3)
    altura = bloco.altura + 2 * folga_y
    faixa = Image.new("RGBA", (largura, altura), tuple(cor[:3]) + (255,))
    escrever(faixa, bloco, largura / 2, folga_y, tinta(cor), "centro")
    girada = faixa.rotate(angulo, resample=Image.BICUBIC, expand=True)
    x = round(tela.width / 2 - girada.width / 2)
    y = round(centro_y - girada.height / 2)
    colar_com_sombra(tela, girada, x, y, raio=16, opacidade=0.4, desloc=(0, 10))
    return girada.height


def _area(formato: str) -> tuple[int, int, int, int]:
    """Largura, altura, topo seguro e base segura do formato."""
    if formato == "story":
        largura, altura = STORY
        return largura, altura, SEGURO_STORY[0], altura - SEGURO_STORY[1]
    largura, altura = FEED
    return largura, altura, 0, altura


def _cores(ident: Identidade, textos: Textos) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """(ênfase, fundo): a cor que chama atenção e a cor de apoio para barras e fundos."""
    if textos.cor == "principal":
        return ident.principal, ident.destaque
    return ident.destaque, ident.principal


def _fundo_escuro(cor: tuple[int, int, int]) -> tuple[int, int, int]:
    return _escuro(cor, 0.55) if _luz(cor) > 0.25 else cor


def _rodape(tela: Image.Image, ident: Identidade, textos: Textos, enfase, base: int, topo_rodape: int | None = None) -> int:
    """Chamada à esquerda e logo à direita na linha de baixo. Devolve o topo da linha."""
    largura = tela.width
    altura_linha = 92
    y = (topo_rodape if topo_rodape is not None else base - MARGEM - altura_linha)
    caixa_logo = assinar(tela, ident, largura - MARGEM, y + altura_linha / 2 - 52, "direita", altura_max=76)
    espaco = (caixa_logo[0] - MARGEM - 30) if caixa_logo[2] > caixa_logo[0] else largura - 2 * MARGEM
    if textos.chamada.strip():
        pilula(tela, textos.chamada, MARGEM, y + 8, enfase, tamanho=40, largura_max=max(320, espaco))
    return y


def _foto_cheia(foto: Image.Image, largura: int, altura: int) -> Image.Image:
    return cobrir(foto, largura, altura)


def _foto_inteira(foto: Image.Image, largura: int, altura: int, y: int, altura_max: int | None = None) -> Image.Image:
    """A foto inteira na largura toda, a partir de y, sobre uma cópia desfocada dela mesma.
    As bordas de cima e de baixo somem aos poucos no fundo (o produto não é cortado pelo texto)."""
    tela = arte.fundo_desfocado(foto, largura, altura, brilho=0.5).convert("RGBA")
    altura_foto = round(foto.height * largura / foto.width)
    limite = min(altura_max or altura, altura - max(0, y))
    peca = cobrir(foto, largura, min(altura_foto, limite))
    mascara = Image.new("L", peca.size, 255)
    desenho = ImageDraw.Draw(mascara)
    sumir = 150
    for i in range(sumir):
        valor = round(255 * (i / sumir) ** 1.3)
        if y > 0:
            desenho.line((0, i, peca.width, i), fill=valor)
        if y + peca.height < altura:
            desenho.line((0, peca.height - 1 - i, peca.width, peca.height - 1 - i), fill=valor)
    peca.putalpha(mascara)
    tela.alpha_composite(peca, (0, max(0, y)))
    return tela


def _molde_faixa(foto, ident, textos, formato):
    largura, altura, topo, base = _area(formato)
    titulo = ajustar(textos.titulo, "forte", largura - 2 * MARGEM - 40, 270, 128, 56, 2, 1.0, caixa_alta=True)
    altura_faixa = titulo.altura + 2 * 34
    inclinacao = round(largura * 1.3 * 0.07 / 2)
    centro = topo + (70 if formato == "feed" else 60) + inclinacao + altura_faixa // 2
    y_foto = centro + altura_faixa // 4 if formato == "feed" else centro + altura_faixa // 2 - 60
    tela = _foto_inteira(foto, largura, altura, min(y_foto, altura - largura), altura - min(y_foto, altura - largura))
    enfase, fundo = _cores(ident, textos)
    degrade(tela, (0, 0, 0), round(altura * 0.62), altura, 0, 170)
    girada = _faixa_inclinada(tela, titulo, enfase, centro)
    if textos.subtitulo.strip():
        sub = ajustar(textos.subtitulo, "negrito", largura - 2 * MARGEM - 120, 130, 50, 30, 2, 1.1)
        largura_tag = sub.largura + 2 * 34
        tag = Image.new("RGBA", (largura_tag, sub.altura + 2 * 22), (255, 255, 255, 255))
        escrever(tag, sub, largura_tag / 2, 22, fundo, "centro")
        tag = tag.rotate(4, resample=Image.BICUBIC, expand=True)
        y_tag = centro + girada // 2 - 10
        colar_com_sombra(tela, tag, round(largura / 2 - tag.width / 2), y_tag, raio=12, opacidade=0.35)
    _rodape(tela, ident, textos, enfase, base)
    return tela


def _selo_redondo(tela: Image.Image, texto: str, cx: int, cy: int, diametro: int, cor) -> None:
    escala = 3
    grande = Image.new("RGBA", (diametro * escala, diametro * escala), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(grande)
    lado = diametro * escala
    desenho.ellipse((0, 0, lado - 1, lado - 1), fill=tuple(cor[:3]) + (255,))
    anel = round(lado * 0.055)
    largura_anel = max(3, round(lado * 0.012))
    desenho.ellipse((anel, anel, lado - anel, lado - anel), outline=tinta(cor) + (210,), width=largura_anel)
    selo = grande.resize((diametro, diametro), Image.LANCZOS)
    bloco = ajustar(texto, "forte", round(diametro * 0.66), round(diametro * 0.5), round(diametro * 0.4), 26, 2, 0.98, caixa_alta=True)
    escrever(selo, bloco, diametro / 2, diametro / 2 - bloco.altura / 2, tinta(cor), "centro")
    selo = selo.rotate(-8, resample=Image.BICUBIC, expand=True)
    colar_com_sombra(tela, selo, round(cx - selo.width / 2), round(cy - selo.height / 2), raio=18, opacidade=0.45)


def _molde_selo(foto, ident, textos, formato):
    largura, altura, topo, base = _area(formato)
    tela = _foto_inteira(foto, largura, altura, 0 if formato == "feed" else topo - 70)
    enfase, fundo = _cores(ident, textos)
    barra = _fundo_escuro(fundo)
    cor_texto = tinta(barra)
    folga = 52
    recuo = 0 if formato == "feed" else 48
    largura_texto = largura - 2 * MARGEM - 2 * recuo
    titulo = ajustar(textos.titulo, "forte", largura_texto, 230, 96, 46, 2, 1.02)
    sub = ajustar(textos.subtitulo, "medio", largura_texto, 110, 42, 28, 2, 1.15) if textos.subtitulo.strip() else None
    conteudo = titulo.altura + ((22 + sub.altura) if sub else 0)
    linha_rodape = 92 + 34
    if formato == "feed":
        y_barra = base - folga - conteudo - linha_rodape - folga
        degrade(tela, (0, 0, 0), y_barra - 200, y_barra, 0, 110)
        tela.alpha_composite(Image.new("RGBA", (largura, altura - y_barra), tuple(barra) + (250,)), (0, y_barra))
        x_texto = MARGEM
    else:
        altura_cartao = folga + conteudo + linha_rodape + folga - 20
        y_barra = base - altura_cartao
        degrade(tela, (0, 0, 0), y_barra - 300, altura, 0, 200, 1.2)
        cartao = caixa_redonda((largura - 2 * recuo, altura_cartao), 36, tuple(barra) + (246,))
        colar_com_sombra(tela, cartao, recuo, y_barra, raio=20, opacidade=0.4)
        x_texto = MARGEM + recuo
    y = y_barra + folga
    escrever(tela, titulo, x_texto, y, cor_texto)
    y += titulo.altura
    if sub:
        y += 22
        escrever(tela, sub, x_texto, y, _misturar(cor_texto, barra, 0.22))
        y += sub.altura
    y += 34
    if textos.chamada.strip():
        pilula(tela, textos.chamada, x_texto, y, enfase, tamanho=40, largura_max=largura_texto - 300)
    assinar(tela, ident, largura - MARGEM - recuo, y + 46 - 52, "direita")
    degrade(tela, (0, 0, 0), 0, round(altura * 0.22), 90, 0)
    diametro = 330 if formato == "feed" else 380
    cx = largura - MARGEM - diametro // 2 + 14
    cy = topo + MARGEM + diametro // 2 + (0 if formato == "feed" else 30)
    _selo_redondo(tela, textos.selo, cx, cy, diametro, enfase)
    return tela


def _molde_destaque(foto, ident, textos, formato):
    largura, altura, topo, base = _area(formato)
    tela = _foto_inteira(foto, largura, altura, 0 if formato == "feed" else topo)
    enfase, fundo = _cores(ident, textos)
    escuro = _fundo_escuro(fundo)
    largura_texto = largura - 2 * MARGEM
    titulo = ajustar(textos.titulo, "forte", largura_texto, 330, 104, 48, 3, 1.02)
    sub = ajustar(textos.subtitulo, "medio", largura_texto, 120, 42, 28, 2, 1.15) if textos.subtitulo.strip() else None
    tem_selo = bool(textos.selo.strip())
    altura_selo = 64
    altura_chamada = 92 if textos.chamada.strip() else 0
    conteudo = (altura_selo + 26 if tem_selo else 0) + titulo.altura + ((20 + sub.altura) if sub else 0)
    conteudo += (40 + altura_chamada) if altura_chamada else 0
    y = base - MARGEM - conteudo - (10 if formato == "feed" else 20)
    inicio = max(topo, y - 330)
    degrade(tela, escuro, inicio, altura, 0, 242, 1.25)
    cor_texto = tinta(escuro)
    if tem_selo:
        pilula(tela, textos.selo, MARGEM, y, enfase, tamanho=30, largura_max=500, caixa_alta=True, peso="forte", sombra=False)
        y += altura_selo + 26
    texto_com_sombra(tela, titulo, MARGEM, y, cor_texto, raio=12, opacidade=0.35)
    y += titulo.altura
    if sub:
        y += 20
        escrever(tela, sub, MARGEM, y, _misturar(cor_texto, escuro, 0.18))
        y += sub.altura
    if altura_chamada:
        pilula(tela, textos.chamada, MARGEM, y + 40, enfase, tamanho=40, largura_max=largura_texto)
    degrade(tela, (0, 0, 0), 0, round(altura * 0.2), 80, 0)
    assinar(tela, ident, MARGEM, topo + (MARGEM if formato == "feed" else 10))
    return tela


def _icone(tela: Image.Image, cx: int, cy: int, raio: int, cor, tipo: str) -> None:
    escala = 3
    lado = raio * 2 * escala
    grande = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(grande)
    desenho.ellipse((0, 0, lado - 1, lado - 1), fill=tuple(cor[:3]) + (255,))
    traco = tinta(cor) + (255,)
    grosso = round(lado * 0.1)
    if tipo == "x":
        a, b = lado * 0.32, lado * 0.68
        desenho.line((a, a, b, b), fill=traco, width=grosso)
        desenho.line((a, b, b, a), fill=traco, width=grosso)
    elif tipo == "ok":
        desenho.line((lado * 0.28, lado * 0.52, lado * 0.44, lado * 0.68, lado * 0.73, lado * 0.36), fill=traco, width=grosso, joint="curve")
    else:
        y = lado * 0.5
        desenho.line((lado * 0.26, y, lado * 0.7, y), fill=traco, width=grosso)
        desenho.polygon(((lado * 0.56, lado * 0.3), (lado * 0.8, y), (lado * 0.56, lado * 0.7)), fill=traco)
        if tipo == "baixo":
            grande = grande.rotate(-90, resample=Image.BICUBIC)
    peca = grande.resize((raio * 2, raio * 2), Image.LANCZOS)
    colar_com_sombra(tela, peca, cx - raio, cy - raio, raio=8, opacidade=0.3, desloc=(0, 4))


def _fundo_marca(largura: int, altura: int, cor: tuple[int, int, int], enfase) -> Image.Image:
    """Fundo da cor da marca com um degradê suave e duas formas grandes e discretas."""
    tela = Image.new("RGBA", (largura, altura), tuple(cor) + (255,))
    degrade(tela, _escuro(cor, 0.45), 0, altura, 0, 255, 1.0)
    pequeno = (largura // 4, altura // 4)
    formas = Image.new("RGBA", pequeno, tuple(cor) + (0,))
    desenho = ImageDraw.Draw(formas)
    a, b = pequeno
    desenho.ellipse((a * 0.5, -b * 0.15, a * 1.4, b * 0.4), fill=_misturar(cor, (255, 255, 255), 0.18) + (90,))
    desenho.ellipse((-a * 0.45, b * 0.72, a * 0.4, b * 1.2), fill=tuple(enfase[:3]) + (60,))
    formas = formas.filter(ImageFilter.GaussianBlur(14)).resize((largura, altura), Image.BICUBIC)
    tela.alpha_composite(formas)
    return tela


def _foto_no_quadro(foto: Image.Image, largura: int, altura: int, tela_app: bool, fundo) -> Image.Image:
    """A foto dentro de um quadro: print inteiro (sem corte) sobre fundo claro; foto de produto preenchendo."""
    if not tela_app:
        return arredondar(cobrir(foto, largura, altura), 26)
    quadro = Image.new("RGBA", (largura, altura), tuple(fundo) + (255,))
    rotulo = 100
    print_app = caber(foto.convert("RGBA"), largura - 40, altura - rotulo - 24)
    print_app = arredondar(print_app, 18)
    y = rotulo + (altura - rotulo - 24 - print_app.height) // 2
    colar_com_sombra(quadro, print_app, (largura - print_app.width) // 2, y, raio=10, opacidade=0.25, desloc=(0, 6))
    return arredondar(quadro, 26)


def _molde_antes_depois(foto, ident, textos, formato, tela_app=False):
    largura, altura, topo, base = _area(formato)
    enfase, fundo = _cores(ident, textos)
    fundo = _fundo_escuro(fundo)
    tela = _fundo_marca(largura, altura, fundo, enfase)
    cor_texto = tinta(fundo)
    y = topo + (MARGEM if formato == "feed" else 20)
    caixa_logo = assinar(tela, ident, largura / 2, y, "centro", altura_max=64)
    if caixa_logo[3] > caixa_logo[1]:
        y = caixa_logo[3] + 28
    titulo = ajustar(textos.titulo, "forte", largura - 2 * MARGEM, 200, 84, 44, 2, 1.02)
    escrever(tela, titulo, largura / 2, y, cor_texto, "centro")
    y += titulo.altura + 40
    rodape = 92 + 2 * 36 if textos.chamada.strip() else MARGEM
    fim = base - rodape
    claro = (236, 238, 242)
    antes_texto = textos.antes.strip() or "Do jeito antigo"
    vao = 28
    largura_painel = (largura - 2 * MARGEM - vao) // 2
    altura_painel = fim - y
    esquerda = caixa_redonda((largura_painel, altura_painel), 26, claro)
    pilula(esquerda, "ANTES", 30, 30, (107, 114, 128), BRANCO, tamanho=28, largura_max=300, peso="forte", sombra=False)
    centro_icone = round(altura_painel * (0.3 if formato == "feed" else 0.34))
    bloco = ajustar(antes_texto, "negrito", largura_painel - 60, altura_painel - centro_icone - 120, 54, 30, 7, 1.15)
    _icone(esquerda, largura_painel // 2, centro_icone, 52, (156, 163, 175), "x")
    escrever(esquerda, bloco, largura_painel / 2, centro_icone + 90, (55, 65, 81), "centro")
    colar_com_sombra(tela, esquerda, MARGEM, y, raio=14, opacidade=0.3)
    direita = _foto_no_quadro(foto, largura_painel, altura_painel, tela_app, _misturar(fundo, (255, 255, 255), 0.85))
    pilula(direita, "DEPOIS", 30, 30, enfase, tamanho=28, largura_max=300, peso="forte")
    colar_com_sombra(tela, direita, MARGEM + largura_painel + vao, y, raio=14, opacidade=0.35)
    _icone(tela, MARGEM + largura_painel + vao // 2, y + altura_painel // 2, 40, enfase, "seta")
    if textos.chamada.strip():
        pilula(tela, textos.chamada, largura / 2, fim + 36, enfase, tamanho=40, largura_max=largura - 2 * MARGEM, alinhar="centro")
    return tela


def _aparelho(foto: Image.Image, largura_max: int, altura_max: int) -> Image.Image:
    """Print num celular (retrato) ou numa janela de navegador (paisagem)."""
    proporcao = foto.width / foto.height
    if proporcao < 0.9:
        borda = 18
        tela_prop = min(max(proporcao, 0.42), 0.62)
        altura = altura_max
        largura = round((altura - 2 * borda) * tela_prop) + 2 * borda
        if largura > largura_max:
            largura = largura_max
            altura = round((largura - 2 * borda) / tela_prop) + 2 * borda
        raio = round(largura * 0.12)
        corpo = caixa_redonda((largura, altura), raio, (15, 17, 21))
        miolo = cobrir(foto, largura - 2 * borda, altura - 2 * borda, (0.5, 0.0))
        corpo.alpha_composite(arredondar(miolo, raio - borda), (borda, borda))
        ImageDraw.Draw(corpo).rounded_rectangle(
            (largura / 2 - 60, borda + 14, largura / 2 + 60, borda + 44), radius=15, fill=(15, 17, 21, 255)
        )
        return corpo
    barra = 54
    largura = largura_max
    altura = round(largura / proporcao) + barra
    if altura > altura_max:
        altura = altura_max
        largura = round((altura - barra) * proporcao)
    janela = caixa_redonda((largura, altura), 22, (229, 231, 235))
    desenho = ImageDraw.Draw(janela)
    for i, cor in enumerate(((239, 68, 68), (245, 158, 11), (34, 197, 94))):
        cx = 34 + i * 30
        desenho.ellipse((cx - 9, barra / 2 - 9, cx + 9, barra / 2 + 9), fill=cor)
    miolo = cobrir(foto, largura, altura - barra, (0.5, 0.0))
    mascara = Image.new("L", miolo.size, 255)
    canto = _mascara_redonda((44, 44), 22)
    mascara.paste(canto.crop((0, 22, 22, 44)), (0, miolo.height - 22))
    mascara.paste(canto.crop((22, 22, 44, 44)), (miolo.width - 22, miolo.height - 22))
    miolo.putalpha(mascara)
    janela.alpha_composite(miolo, (0, barra))
    return janela


def _molde_tela_app(foto, ident, textos, formato, tela_app=True):
    largura, altura, topo, base = _area(formato)
    enfase, fundo = _cores(ident, textos)
    fundo = _fundo_escuro(fundo)
    tela = _fundo_marca(largura, altura, fundo, enfase)
    cor_texto = tinta(fundo)
    y = topo + (MARGEM + 6 if formato == "feed" else 20)
    if textos.selo.strip():
        caixa = pilula(tela, textos.selo, largura / 2, y, enfase, tamanho=28, largura_max=500, alinhar="centro", caixa_alta=True, peso="forte", sombra=False)
        y = caixa[3] + 26
    titulo = ajustar(textos.titulo, "forte", largura - 2 * MARGEM, 210, 86, 44, 2, 1.02)
    escrever(tela, titulo, largura / 2, y, cor_texto, "centro")
    y += titulo.altura
    if textos.subtitulo.strip():
        sub = ajustar(textos.subtitulo, "medio", largura - 2 * MARGEM - 60, 100, 38, 26, 2, 1.15)
        y += 18
        escrever(tela, sub, largura / 2, y, _misturar(cor_texto, fundo, 0.2), "centro")
        y += sub.altura
    y += 46
    linha_rodape = 92
    fim = base - MARGEM - linha_rodape - 34
    if tela_app:
        peca = _aparelho(foto, largura - 2 * MARGEM - 80, fim - y)
    else:
        lado = min(largura - 2 * MARGEM - 80, fim - y)
        peca = caixa_redonda((lado, lado), 30, (255, 255, 255))
        peca.alpha_composite(arredondar(cobrir(foto, lado - 28, lado - 28), 20), (14, 14))
    colar_com_sombra(tela, peca, (largura - peca.width) // 2, y + (fim - y - peca.height) // 2, raio=26, opacidade=0.5, desloc=(0, 18))
    _rodape(tela, ident, textos, enfase, base)
    return tela


def _molde_story_chamada(foto, ident, textos, formato):
    largura, altura, topo, base = _area(formato)
    tela = _foto_cheia(foto, largura, altura)
    enfase, _fundo = _cores(ident, textos)
    titulo = ajustar(textos.titulo, "forte", largura - 2 * MARGEM, 300, 100, 48, 3, 1.02)
    sub = ajustar(textos.subtitulo, "medio", largura - 2 * MARGEM, 120, 42, 28, 2, 1.15) if textos.subtitulo.strip() else None
    y = topo + (MARGEM if formato == "feed" else 10)
    altura_topo = 96 + titulo.altura + ((20 + sub.altura) if sub else 0)
    degrade(tela, (0, 0, 0), 0, y + altura_topo + 260, 200, 0, 1.3)
    caixa_logo = assinar(tela, ident, largura / 2, y, "centro", altura_max=64)
    if caixa_logo[3] > caixa_logo[1]:
        y = caixa_logo[3] + 32
    texto_com_sombra(tela, titulo, largura / 2, y, BRANCO, "centro", raio=12, opacidade=0.5)
    y += titulo.altura
    if sub:
        y += 20
        texto_com_sombra(tela, sub, largura / 2, y, (240, 240, 240), "centro", raio=8, opacidade=0.5)
    chamada = textos.chamada.strip() or "Responda QUERO"
    altura_pilula = 120
    y_pilula = base - altura_pilula - (MARGEM if formato == "feed" else 30)
    degrade(tela, (0, 0, 0), y_pilula - 280, altura, 0, 170, 1.4)
    pilula(tela, chamada, largura / 2, y_pilula, BRANCO, GRAFITE, tamanho=50, largura_max=largura - 2 * MARGEM, alinhar="centro", peso="forte")
    if textos.selo.strip():
        pilula(tela, textos.selo, largura / 2, y_pilula - 96, enfase, tamanho=32, largura_max=600, alinhar="centro", caixa_alta=True, peso="forte")
    return tela


DESENHOS = {
    "faixa": _molde_faixa,
    "selo": _molde_selo,
    "destaque": _molde_destaque,
    "antes_depois": _molde_antes_depois,
    "tela_app": _molde_tela_app,
    "story_chamada": _molde_story_chamada,
}


def compor(
    foto: Image.Image,
    modelo: str,
    textos: Textos,
    ident: Identidade,
    formato: str = "feed",
    tela_app: bool = False,
) -> Image.Image:
    """Monta a peça final (RGB) no formato 'feed' (1080x1350) ou 'story' (1080x1920)."""
    formato = "story" if formato == "story" else "feed"
    modelo = modelo if modelo in DESENHOS else "destaque"
    if modelo == "selo" and not textos.selo.strip():
        modelo = "destaque"
    if not textos.titulo.strip():
        textos = Textos(**{**textos.__dict__, "titulo": textos.chamada or ident.assinatura or " "})
    foto = foto.convert("RGB")
    if modelo in ("antes_depois", "tela_app"):
        tela = DESENHOS[modelo](foto, ident, textos, formato, tela_app)
    else:
        tela = DESENHOS[modelo](foto, ident, textos, formato)
    return tela.convert("RGB")


def abrir_logo(dados: bytes | None) -> Image.Image | None:
    if not dados:
        return None
    try:
        with Image.open(io.BytesIO(dados)) as aberta:
            aberta.load()
            return ImageOps.exif_transpose(aberta).convert("RGBA")
    except Exception:
        return None


def normalizar_logo(dados: bytes) -> bytes:
    """Logo em PNG (mantém o fundo transparente), até 600 px no lado maior."""
    try:
        with Image.open(io.BytesIO(dados)) as aberta:
            aberta.load()
            logo = ImageOps.exif_transpose(aberta).convert("RGBA")
    except Exception as erro:
        raise ValueError("O logo não pôde ser lido. Use PNG, JPG ou WebP.") from erro
    caixa = logo.getbbox()
    if caixa:
        logo = logo.crop(caixa)
    logo = arte.reduzir(logo, 600)
    saida = io.BytesIO()
    logo.save(saida, "PNG", optimize=True)
    return saida.getvalue()
