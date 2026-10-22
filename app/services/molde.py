"""Moldes de arte: a foto vira a peça do post (feed 1080x1350 e Story 1080x1920), com o texto sempre cabendo."""

import colorsys
import io
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.services import arte
from app.services.assunto import Lugar, achar_assunto, posicionar

PASTA_FONTES = Path(__file__).resolve().parent.parent / "fontes"
ARQUIVOS_FONTE = {
    "forte": "Montserrat-ExtraBold.ttf",
    "negrito": "Montserrat-Bold.ttf",
    "medio": "Montserrat-SemiBold.ttf",
}
VERSAO = "3"
FEED = (1080, 1350)
STORY = (1080, 1920)
FACEBOOK = (1080, 1080)
SEGURO_STORY = (250, 300)
MODELOS = (
    "faixa",
    "selo",
    "destaque",
    "antes_depois",
    "tela_app",
    "story_chamada",
    "alerta",
    "etiqueta",
    "carimbo",
    "neon",
    "poster",
    "minimal",
    "livre",
)
NOMES_MODELOS = {
    "faixa": "Faixa",
    "selo": "Selo",
    "destaque": "Destaque",
    "antes_depois": "Antes e depois",
    "tela_app": "Imagem em destaque",
    "story_chamada": "Chamada",
    "alerta": "Alerta",
    "etiqueta": "Etiqueta",
    "carimbo": "Carimbo",
    "neon": "Neon",
    "poster": "Pôster",
    "minimal": "Minimalista",
    "livre": "Só a imagem",
}
# (cor de ênfase, cor de apoio escura). "marca" usa as cores da marca; "livre" usa a cor que a pessoa pediu.
PALETAS = {
    "urgencia": ((255, 214, 10), (17, 17, 17)),
    "quente": ((244, 140, 46), (72, 30, 14)),
    "fresca": ((163, 230, 53), (8, 58, 44)),
    "fria": ((56, 189, 248), (12, 24, 52)),
    "elegante": ((212, 175, 90), (16, 16, 18)),
    "doce": ((244, 114, 182), (74, 22, 70)),
    "natural": ((190, 214, 140), (40, 56, 34)),
    "vibrante": ((217, 70, 239), (40, 14, 96)),
    "neutra": ((255, 255, 255), (20, 24, 32)),
    "vermelha": ((230, 57, 70), (36, 20, 24)),
}
NOMES_PALETAS = {
    "marca": "cores da marca",
    "urgencia": "amarelo e preto (urgência)",
    "quente": "laranja e marrom (comida, calor)",
    "fresca": "verde-limão e verde-escuro (fresco, natural)",
    "fria": "azul e marinho (tecnologia, limpeza)",
    "elegante": "dourado e preto (elegante)",
    "doce": "rosa e ameixa (doce, delicado)",
    "natural": "verde-sálvia e oliva (natural, saúde)",
    "vibrante": "magenta e roxo (festa, noite)",
    "neutra": "branco e grafite (sóbrio)",
    "vermelha": "vermelho e vinho (forte)",
    "livre": "a cor que você pediu",
}
POSICOES_TEXTO = ("auto", "topo", "base")
COR_PRINCIPAL = "#1B2A41"
COR_DESTAQUE = "#E63946"
BRANCO = (255, 255, 255)
GRAFITE = (17, 24, 39)
MARGEM = 64
Caixa = tuple[float, float, float, float]


@dataclass
class Identidade:
    principal: tuple[int, int, int] = (27, 42, 65)
    destaque: tuple[int, int, int] = (230, 57, 70)
    logo: Image.Image | None = None
    assinatura: str = ""
    lado: str = ""


@dataclass
class Textos:
    titulo: str = ""
    subtitulo: str = ""
    selo: str = ""
    chamada: str = ""
    antes: str = ""
    cor: str = "destaque"
    paleta: str = "marca"
    posicao: str = "auto"
    cor_livre: str = ""
    camadas: list | None = None


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


def _peca_pilula(
    texto: str,
    fundo,
    cor_texto=None,
    tamanho: int = 40,
    largura_max: int = 700,
    caixa_alta: bool = False,
    peso: str = "negrito",
) -> Image.Image | None:
    texto = (texto or "").strip()
    if not texto:
        return None
    folga_x = round(tamanho * 0.95)
    folga_y = round(tamanho * 0.62)
    bloco = ajustar(texto, peso, largura_max - 2 * folga_x, tamanho * 2, tamanho, 22, 1, caixa_alta=caixa_alta)
    altura_texto = _montar_bloco(["H"], bloco.letra, bloco.tamanho, 1.0).topo
    largura = bloco.largura + 2 * folga_x
    altura = altura_texto + 2 * folga_y
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
    return peca


def medir_pilula(texto: str, tamanho: int = 40, largura_max: int = 700, caixa_alta: bool = False, peso: str = "negrito") -> tuple[int, int]:
    peca = _peca_pilula(texto, (0, 0, 0), tamanho=tamanho, largura_max=largura_max, caixa_alta=caixa_alta, peso=peso)
    return (peca.width, peca.height) if peca else (0, 0)


def _x_alinhado(x: float, largura: int, alinhar: str) -> int:
    if alinhar == "centro":
        return round(x - largura / 2)
    if alinhar == "direita":
        return round(x - largura)
    return round(x)


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
    peca = _peca_pilula(texto, fundo, cor_texto, tamanho, largura_max, caixa_alta, peso)
    if peca is None:
        return (int(x), int(y), int(x), int(y))
    x0, y0 = _x_alinhado(x, peca.width, alinhar), round(y)
    if sombra:
        colar_com_sombra(tela, peca, x0, y0, raio=12, opacidade=0.35, desloc=(0, 6))
    else:
        tela.alpha_composite(peca, (x0, y0))
    return (x0, y0, x0 + peca.width, y0 + peca.height)


def _chip_assinatura(
    ident: Identidade, altura_max: int = 76, largura_max: int = 250, fundo=(0, 0, 0, 120), cor_texto=(255, 255, 255, 245)
) -> Image.Image | None:
    """Logo da marca num chip branco; sem logo, o texto da marca d'água numa pílula (escura por padrão)."""
    if ident.logo is not None:
        logo = caber(ident.logo.convert("RGBA"), largura_max, altura_max)
        folga = 14
        chip = caixa_redonda((logo.width + 2 * folga, logo.height + 2 * folga), 18, (255, 255, 255, 238))
        chip.alpha_composite(logo, (folga, folga))
        return chip
    if ident.assinatura.strip():
        bloco = ajustar(ident.assinatura, "negrito", largura_max + 120, 60, 30, 20, 1)
        folga_x, folga_y = 22, 14
        chip = caixa_redonda((bloco.largura + 2 * folga_x, bloco.altura + 2 * folga_y), (bloco.altura + 2 * folga_y) // 2, fundo)
        escrever(chip, bloco, folga_x, folga_y, cor_texto)
        return chip
    return None


def _canto_logo(ident: Identidade, x: float, alinhar: str, largura: int) -> tuple[float, str]:
    """Se a marca escolheu o lado do logo, ignora o canto que o molde usaria."""
    if ident.lado not in ("esquerda", "direita", "centro") or largura <= 0:
        return x, alinhar
    if ident.lado == "centro":
        return largura / 2, "centro"
    if ident.lado == "direita":
        return largura - MARGEM, "direita"
    return float(MARGEM), "esquerda"


def caixa_assinatura(
    ident: Identidade,
    x: float,
    y: float,
    alinhar: str = "esquerda",
    vertical: str = "topo",
    altura_max: int = 76,
    largura_max: int = 250,
    largura: int = 0,
) -> tuple[int, int, int, int]:
    """Onde o logo vai ficar (para reservar o lugar antes de pôr a foto)."""
    x, alinhar = _canto_logo(ident, x, alinhar, largura)
    chip = _chip_assinatura(ident, altura_max, largura_max)
    if chip is None:
        return (int(x), int(y), int(x), int(y))
    x0 = _x_alinhado(x, chip.width, alinhar)
    y0 = round(y - chip.height) if vertical == "base" else round(y)
    return (x0, y0, x0 + chip.width, y0 + chip.height)


def assinar(
    tela: Image.Image,
    ident: Identidade,
    x: float,
    y: float,
    alinhar: str = "esquerda",
    vertical: str = "topo",
    altura_max: int = 76,
    largura_max: int = 250,
    posicionado: bool = False,
) -> tuple[int, int, int, int]:
    chip = _chip_assinatura(ident, altura_max, largura_max)
    if chip is None:
        return (int(x), int(y), int(x), int(y))
    x0, y0, x1, y1 = caixa_assinatura(
        ident, x, y, alinhar, vertical, altura_max, largura_max, 0 if posicionado else tela.width
    )
    colar_com_sombra(tela, chip, x0, y0, raio=10, opacidade=0.25, desloc=(0, 4))
    return (x0, y0, x1, y1)


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
    if formato == "facebook":
        largura, altura = FACEBOOK
        return largura, altura, 36, altura - 36
    largura, altura = FEED
    return largura, altura, 0, altura


def _cores(ident: Identidade, textos: Textos) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """(ênfase, fundo): a cor que chama atenção e a cor de apoio para barras e fundos.
    A paleta do plano manda; 'marca' usa as cores da marca."""
    if textos.paleta == "livre" and textos.cor_livre:
        enfase = cor_hex(textos.cor_livre, COR_DESTAQUE)
        return enfase, (_escuro(enfase, 0.8) if _luz(enfase) > 0.08 else _misturar(enfase, (255, 255, 255), 0.85))
    if textos.paleta in PALETAS:
        return PALETAS[textos.paleta]
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
    if textos.chamada.strip() and caixa_logo[2] > caixa_logo[0]:
        if caixa_logo[0] <= MARGEM + 8:
            x_chamada = caixa_logo[2] + 30
            espaco = largura - MARGEM - x_chamada
        else:
            x_chamada = MARGEM
            espaco = caixa_logo[0] - MARGEM - 30
        pilula(tela, textos.chamada, x_chamada, y + 8, enfase, tamanho=40, largura_max=max(200, espaco))
    return y


def _mascara_bordas(largura: int, altura: int, esquerda: bool, direita: bool, cima: bool, baixo: bool, sumir: int = 140) -> Image.Image:
    """Máscara que some aos poucos nas bordas da foto que ficam dentro da peça."""
    mascara = Image.new("L", (largura, altura), 255)
    sumir = max(2, min(sumir, largura // 3, altura // 3))
    rampa = [round(255 * (i / sumir) ** 1.3) for i in range(sumir)]
    horizontal = Image.new("L", (sumir, 1))
    horizontal.putdata(rampa)
    vertical = Image.new("L", (1, sumir))
    vertical.putdata(rampa)
    if esquerda:
        mascara.paste(ImageChops.darker(mascara.crop((0, 0, sumir, altura)), horizontal.resize((sumir, altura))), (0, 0))
    if direita:
        faixa = horizontal.transpose(Image.FLIP_LEFT_RIGHT).resize((sumir, altura))
        mascara.paste(ImageChops.darker(mascara.crop((largura - sumir, 0, largura, altura)), faixa), (largura - sumir, 0))
    if cima:
        mascara.paste(ImageChops.darker(mascara.crop((0, 0, largura, sumir)), vertical.resize((largura, sumir))), (0, 0))
    if baixo:
        faixa = vertical.transpose(Image.FLIP_TOP_BOTTOM).resize((largura, sumir))
        mascara.paste(ImageChops.darker(mascara.crop((0, altura - sumir, largura, altura)), faixa), (0, altura - sumir))
    return mascara


def _fundo_foto(foto: Image.Image, largura: int, altura: int, lugar: Lugar, brilho: float = 0.5) -> Image.Image:
    """A foto no lugar escolhido; o que sobra da peça é a própria foto desfocada (sem faixa vazia)."""
    tela = arte.fundo_desfocado(foto, largura, altura, brilho=brilho).convert("RGBA")
    pw, ph = max(1, round(foto.width * lugar.escala)), max(1, round(foto.height * lugar.escala))
    peca = foto.convert("RGB").resize((pw, ph), Image.LANCZOS)
    if lugar.escala > 1.25:
        peca = peca.filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=2))
    x, y = round(lugar.x), round(lugar.y)
    mascara = _mascara_bordas(pw, ph, x > 0, x + pw < largura, y > 0, y + ph < altura)
    peca = peca.convert("RGBA")
    peca.putalpha(mascara)
    recorte = (max(0, -x), max(0, -y), min(pw, largura - x), min(ph, altura - y))
    if recorte[2] > recorte[0] and recorte[3] > recorte[1]:
        tela.alpha_composite(peca.crop(recorte), (max(0, x), max(0, y)))
    return tela


Layout = Callable[[str], tuple[list[Caixa], Callable[[Image.Image], None]]]


def _rodar(
    foto: Image.Image,
    assunto: Caixa,
    formato: str,
    layout: Layout,
    posicao: str,
    opcoes: tuple[str, ...],
    brilho: float = 0.5,
    efeito: Callable[[Image.Image], Image.Image] | None = None,
) -> Image.Image:
    """Mede os textos de cada posição possível, põe a foto onde o assunto não fica embaixo de texto
    e desenha a arte na posição em que a foto sai maior."""
    largura, altura, _topo, _base = _area(formato)
    candidatas = [posicao] if posicao in opcoes else list(opcoes)
    escolha = None
    for indice, pos in enumerate(candidatas):
        ocupados, desenhar = layout(pos)
        lugar = posicionar(foto.size, assunto, largura, altura, ocupados)
        nota = lugar.escala * (0.75 + 0.25 * lugar.cobre) * (1.0 if indice == 0 else 0.96) * (1.0 if lugar.livre else 0.5)
        if escolha is None or nota > escolha[0]:
            escolha = (nota, lugar, desenhar)
    _nota, lugar, desenhar = escolha
    tela = _fundo_foto(foto, largura, altura, lugar, brilho)
    if efeito is not None:
        tela = efeito(tela)
    desenhar(tela)
    return tela


def _altura_girada(largura: float, altura: float, angulo: float) -> int:
    import math

    radianos = math.radians(abs(angulo))
    return round(altura * math.cos(radianos) + largura * math.sin(radianos))


def _layout_faixa(ident, textos, formato, pos):
    largura, altura, topo, base = _area(formato)
    enfase, fundo = _cores(ident, textos)
    titulo = ajustar(textos.titulo, "forte", largura - 2 * MARGEM - 40, 270, 128, 56, 2, 1.0, caixa_alta=True)
    altura_faixa = titulo.altura + 2 * 34
    girada = _altura_girada(largura * 1.3, altura_faixa, 4)
    sub = ajustar(textos.subtitulo, "negrito", largura - 2 * MARGEM - 120, 130, 50, 30, 2, 1.1) if textos.subtitulo.strip() else None
    largura_tag = (sub.largura + 2 * 34) if sub else 0
    altura_tag = _altura_girada(largura_tag, sub.altura + 2 * 22, 4) if sub else 0
    y_rodape = base - MARGEM - 92
    if pos == "base":
        centro = y_rodape - 30 - (altura_tag - 10 if sub else 0) - girada // 2
    else:
        centro = topo + (40 if formato == "feed" else 30) + girada // 2
    ocupados = [(0, centro - girada / 2, largura, centro + girada / 2)]
    if sub:
        y_tag = centro + girada // 2 - 10
        ocupados.append((largura / 2 - largura_tag / 2 - 20, y_tag, largura / 2 + largura_tag / 2 + 20, y_tag + altura_tag))
    if formato == "story" and pos == "topo":
        y_rodape = round(centro + girada / 2 + (altura_tag if sub else 0) + 24)
    ocupados += _caixas_rodape(ident, textos, y_rodape + MARGEM + 92)

    def desenhar(tela):
        if pos == "base":
            degrade(tela, (0, 0, 0), round(centro - girada), altura, 0, 190)
        else:
            degrade(tela, (0, 0, 0), round(altura * 0.66), altura, 0, 170)
        _faixa_inclinada(tela, titulo, enfase, centro)
        if sub:
            tag = Image.new("RGBA", (largura_tag, sub.altura + 2 * 22), (255, 255, 255, 255))
            escrever(tag, sub, largura_tag / 2, 22, _fundo_escuro(fundo), "centro")
            tag = tag.rotate(4, resample=Image.BICUBIC, expand=True)
            colar_com_sombra(tela, tag, round(largura / 2 - tag.width / 2), centro + girada // 2 - 10, raio=12, opacidade=0.35)
        _rodape(tela, ident, textos, enfase, base, y_rodape)

    return ocupados, desenhar


def _caixas_rodape(ident: Identidade, textos: Textos, base: int, largura: int = FEED[0]) -> list[Caixa]:
    """O que a linha de baixo ocupa (chamada à esquerda e logo à direita)."""
    y = base - MARGEM - 92
    caixas = [caixa_assinatura(ident, largura - MARGEM, y + 92 / 2 - 52, "direita", altura_max=76, largura=largura)]
    if textos.chamada.strip():
        logo = caixas[0]
        if logo[2] > logo[0] and logo[0] <= MARGEM + 8:
            x_chamada = logo[2] + 30
            espaco = largura - MARGEM - x_chamada
        else:
            x_chamada = MARGEM
            espaco = (logo[0] - MARGEM - 30) if logo[2] > logo[0] else largura - 2 * MARGEM
        w, h = medir_pilula(textos.chamada, 40, max(200, espaco))
        caixas.append((x_chamada, y + 8, x_chamada + w, y + 8 + h))
    return caixas


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


def _layout_selo(ident, textos, formato, pos):
    largura, altura, topo, base = _area(formato)
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
    altura_barra = folga + conteudo + linha_rodape + folga - (0 if formato == "feed" else 20)
    if formato == "feed":
        y_barra = base - altura_barra if pos == "base" else 0
    else:
        y_barra = base - altura_barra if pos == "base" else topo
    diametro = 330 if formato == "feed" else 380
    cx = largura - MARGEM - diametro // 2 + 14
    if pos == "base":
        cy = topo + MARGEM + diametro // 2 + (0 if formato == "feed" else 30)
    else:
        cy = base - MARGEM - diametro // 2 - (0 if formato == "feed" else 30)
    ocupados = [(recuo, y_barra, largura - recuo, y_barra + altura_barra), (cx - diametro * 0.6, cy - diametro * 0.6, largura, cy + diametro * 0.6)]

    def desenhar(tela):
        if formato == "feed":
            if pos == "base":
                degrade(tela, (0, 0, 0), y_barra - 200, y_barra, 0, 110)
            else:
                degrade(tela, (0, 0, 0), y_barra + altura_barra, y_barra + altura_barra + 200, 110, 0)
            tela.alpha_composite(Image.new("RGBA", (largura, altura_barra), tuple(barra) + (250,)), (0, y_barra))
        else:
            if pos == "base":
                degrade(tela, (0, 0, 0), y_barra - 300, altura, 0, 200, 1.2)
            else:
                degrade(tela, (0, 0, 0), 0, y_barra + altura_barra + 300, 200, 0, 1.2)
            cartao = caixa_redonda((largura - 2 * recuo, altura_barra), 36, tuple(barra) + (246,))
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
        _selo_redondo(tela, textos.selo, cx, cy, diametro, enfase)

    return ocupados, desenhar


def _layout_destaque(ident, textos, formato, pos):
    largura, altura, topo, base = _area(formato)
    enfase, fundo = _cores(ident, textos)
    escuro = _fundo_escuro(fundo)
    largura_texto = largura - 2 * MARGEM
    titulo = ajustar(textos.titulo, "forte", largura_texto, 330, 104, 48, 3, 1.02)
    sub = ajustar(textos.subtitulo, "medio", largura_texto, 120, 42, 28, 2, 1.15) if textos.subtitulo.strip() else None
    altura_selo = medir_pilula(textos.selo, 30, 500, True, "forte")[1]
    altura_chamada = medir_pilula(textos.chamada, 40, largura_texto)[1]
    conteudo = (altura_selo + 26 if altura_selo else 0) + titulo.altura + ((20 + sub.altura) if sub else 0)
    conteudo += (40 + altura_chamada) if altura_chamada else 0
    if pos == "topo":
        y0 = topo + (MARGEM if formato == "feed" else 20)
        logo = caixa_assinatura(ident, MARGEM, base - (MARGEM if formato == "feed" else 10), vertical="base", largura=largura)
    else:
        y0 = base - MARGEM - conteudo - (10 if formato == "feed" else 20)
        logo = caixa_assinatura(ident, MARGEM, topo + (MARGEM if formato == "feed" else 10), largura=largura)
    ocupados = [(0, y0 - 20, largura, y0 + conteudo + 20), logo]

    def desenhar(tela):
        if pos == "topo":
            degrade(tela, escuro, 0, min(altura, y0 + conteudo + 260), 242, 0, 1.25)
            degrade(tela, (0, 0, 0), round(altura * 0.82), altura, 0, 80)
        else:
            degrade(tela, escuro, max(0, y0 - 260), altura, 0, 242, 1.25)
            degrade(tela, (0, 0, 0), 0, round(altura * 0.2), 80, 0)
        cor_texto = tinta(escuro)
        y = y0
        if altura_selo:
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
        assinar(tela, ident, logo[0], logo[1], posicionado=True)

    return ocupados, desenhar


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


def _layout_story_chamada(ident, textos, formato, _pos):
    largura, altura, topo, base = _area(formato)
    enfase, _fundo = _cores(ident, textos)
    titulo = ajustar(textos.titulo, "forte", largura - 2 * MARGEM, 300, 100, 48, 3, 1.02)
    sub = ajustar(textos.subtitulo, "medio", largura - 2 * MARGEM, 120, 42, 28, 2, 1.15) if textos.subtitulo.strip() else None
    y_inicio = topo + (MARGEM if formato == "feed" else 10)
    logo = caixa_assinatura(ident, largura / 2, y_inicio, "centro", altura_max=64, largura=largura)
    y_titulo = logo[3] + 32 if logo[3] > logo[1] else y_inicio
    fim_topo = y_titulo + titulo.altura + ((20 + sub.altura) if sub else 0)
    meia = max(titulo.largura, sub.largura if sub else 0, logo[2] - logo[0]) / 2 + 20
    chamada = textos.chamada.strip()
    w_chamada, h_chamada = medir_pilula(chamada, 50, largura - 2 * MARGEM, peso="forte") if chamada else (0, 0)
    y_pilula = base - h_chamada - (MARGEM if formato == "feed" else 30)
    w_selo, h_selo = medir_pilula(textos.selo, 32, 600, True, "forte")
    y_selo = y_pilula - h_selo - 24
    ocupados = [
        (largura / 2 - meia, y_inicio, largura / 2 + meia, fim_topo + 10),
    ]
    if w_chamada:
        ocupados.append((largura / 2 - w_chamada / 2, y_pilula, largura / 2 + w_chamada / 2, y_pilula + h_chamada))
    if w_selo:
        ocupados.append((largura / 2 - w_selo / 2, y_selo, largura / 2 + w_selo / 2, y_selo + h_selo))

    def desenhar(tela):
        degrade(tela, (0, 0, 0), 0, fim_topo + 260, 200, 0, 1.3)
        assinar(tela, ident, largura / 2, y_inicio, "centro", altura_max=64)
        texto_com_sombra(tela, titulo, largura / 2, y_titulo, BRANCO, "centro", raio=12, opacidade=0.5)
        if sub:
            texto_com_sombra(tela, sub, largura / 2, y_titulo + titulo.altura + 20, (240, 240, 240), "centro", raio=8, opacidade=0.5)
        degrade(tela, (0, 0, 0), (y_selo if w_selo else y_pilula) - 240, altura, 0, 170, 1.4)
        if chamada:
            pilula(tela, chamada, largura / 2, y_pilula, BRANCO, GRAFITE, tamanho=50, largura_max=largura - 2 * MARGEM, alinhar="centro", peso="forte")
        if w_selo:
            pilula(tela, textos.selo, largura / 2, y_selo, enfase, tamanho=32, largura_max=600, alinhar="centro", caixa_alta=True, peso="forte")

    return ocupados, desenhar


def _contraste(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    claro, escuro = sorted((_luz(a), _luz(b)), reverse=True)
    return (claro + 0.05) / (escuro + 0.05)


def _escuro_oposto(cor: tuple[int, int, int]) -> tuple[int, int, int]:
    return (17, 17, 17) if _luz(cor) > 0.25 else (250, 250, 250)


def _y_zona(pos: str, formato: str, topo: int, base: int, altura_bloco: int, folga_feed: int = MARGEM, folga_story: int = 10) -> int:
    folga = folga_feed if formato == "feed" else folga_story
    return topo + folga if pos == "topo" else base - folga - altura_bloco


def _escurecer_zona(tela: Image.Image, cor, pos: str, y0: float, y1: float, forca: int = 225, alcance: int = 240) -> None:
    if pos == "topo":
        degrade(tela, cor, 0, min(tela.height, round(y1 + alcance)), forca, 0, 1.25)
    else:
        degrade(tela, cor, max(0, round(y0 - alcance)), tela.height, 0, forca, 1.25)


def _logo_oposto(ident: Identidade, pos: str, formato: str, topo: int, base: int, alinhar: str = "esquerda") -> Caixa:
    x = MARGEM if alinhar == "esquerda" else FEED[0] - MARGEM
    if pos == "topo":
        return caixa_assinatura(ident, x, base - (MARGEM if formato == "feed" else 10), alinhar, "base", 70, 230, FEED[0])
    return caixa_assinatura(ident, x, topo + (MARGEM if formato == "feed" else 10), alinhar, "topo", 70, 230, FEED[0])


def _assinar_em(tela: Image.Image, ident: Identidade, caixa: Caixa) -> None:
    if caixa[2] > caixa[0]:
        chip = _chip_assinatura(ident, 70, 230)
        if chip is not None:
            colar_com_sombra(tela, chip, round(caixa[0]), round(caixa[1]), raio=10, opacidade=0.25, desloc=(0, 4))


def _listras(largura: int, altura: int, cor_a, cor_b, passo: int = 40) -> Image.Image:
    faixa = Image.new("RGBA", (largura, altura), tuple(cor_a[:3]) + (255,))
    desenho = ImageDraw.Draw(faixa)
    for x in range(-altura, largura + altura, passo * 2):
        desenho.polygon([(x, 0), (x + passo, 0), (x + passo - altura, altura), (x - altura, altura)], fill=tuple(cor_b[:3]) + (255,))
    return faixa


def _triangulo_alerta(lado: int, cor, sinal) -> Image.Image:
    escala = 3
    grande = Image.new("RGBA", (lado * escala, lado * escala), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(grande)
    s = lado * escala
    desenho.polygon([(s / 2, s * 0.04), (s * 0.98, s * 0.92), (s * 0.02, s * 0.92)], fill=tuple(cor[:3]) + (255,))
    desenho.rounded_rectangle((s * 0.45, s * 0.32, s * 0.55, s * 0.64), radius=s * 0.04, fill=tuple(sinal[:3]) + (255,))
    desenho.ellipse((s * 0.44, s * 0.7, s * 0.56, s * 0.82), fill=tuple(sinal[:3]) + (255,))
    return grande.resize((lado, lado), Image.LANCZOS)


def _layout_alerta(ident, textos, formato, pos):
    largura, altura, topo, base = _area(formato)
    enfase, _fundo = PALETAS["urgencia"] if textos.paleta == "marca" else _cores(ident, textos)
    escuro = _escuro_oposto(enfase)
    largura_texto = largura - 2 * MARGEM
    listra, folga = 30, 42
    selo = ajustar(textos.selo, "forte", largura_texto - 100, 60, 44, 28, 1, caixa_alta=True) if textos.selo.strip() else None
    titulo = ajustar(textos.titulo, "forte", largura_texto, 330, 116, 52, 3, 0.98, caixa_alta=True)
    sub = ajustar(textos.subtitulo, "negrito", largura_texto, 110, 42, 28, 2, 1.12) if textos.subtitulo.strip() else None
    chip = _chip_assinatura(ident, 64, 220, tuple(escuro) + (255,), tuple(enfase) + (255,))
    w_chamada, h_chamada = medir_pilula(textos.chamada, 40, largura_texto - (chip.width + 40 if chip else 0))
    linha = max(h_chamada, chip.height if chip else 0)
    conteudo = (max(64, selo.altura) + 24 if selo else 0) + titulo.altura + ((18 + sub.altura) if sub else 0) + ((32 + linha) if linha else 0)
    altura_painel = 2 * listra + 2 * folga + conteudo
    y0 = _y_zona(pos, formato, topo, base, altura_painel, 40, 0)
    ocupados = [(0, y0 - 10, largura, y0 + altura_painel + 10)]

    def desenhar(tela):
        painel = Image.new("RGBA", (largura, altura_painel), tuple(enfase) + (255,))
        painel.alpha_composite(_listras(largura, listra, escuro, enfase), (0, 0))
        painel.alpha_composite(_listras(largura, listra, escuro, enfase), (0, altura_painel - listra))
        y = listra + folga
        cor_texto = tinta(enfase)
        if selo:
            painel.alpha_composite(_triangulo_alerta(64, escuro, enfase), (MARGEM, round(y + (max(64, selo.altura) - 64) / 2)))
            escrever(painel, selo, MARGEM + 84, y + (max(64, selo.altura) - selo.altura) / 2, escuro)
            y += max(64, selo.altura) + 24
        escrever(painel, titulo, MARGEM, y, cor_texto)
        y += titulo.altura
        if sub:
            y += 18
            escrever(painel, sub, MARGEM, y, _misturar(cor_texto, enfase, 0.12))
            y += sub.altura
        if linha:
            y += 32
            if w_chamada:
                painel_pilula = _peca_pilula(textos.chamada, escuro, enfase, 40, largura_texto)
                painel.alpha_composite(painel_pilula, (MARGEM, round(y + (linha - painel_pilula.height) / 2)))
            if chip:
                painel.alpha_composite(chip, (largura - MARGEM - chip.width, round(y + (linha - chip.height) / 2)))
        colar_com_sombra(tela, painel, 0, y0, raio=18, opacidade=0.45, desloc=(0, 10))

    return ocupados, desenhar


def _peca_etiqueta(texto: str, cor, largura_max: int = 470) -> Image.Image:
    """Etiqueta de preço com furo e barbante, levemente girada."""
    cor = tuple(cor[:3])
    bloco = ajustar(texto, "forte", largura_max - 160, 150, 96, 34, 2, 0.98, caixa_alta=True)
    h = max(150, bloco.altura + 80)
    ponta = round(h * 0.42)
    w = ponta + bloco.largura + 90
    escala = 3
    pad_topo, pad_esq = 110, 80
    S = lambda valor: round(valor * escala)  # noqa: E731
    grande = Image.new("RGBA", (S(w + pad_esq + 12), S(h + pad_topo + 12)), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(grande)
    ox, oy = S(pad_esq), S(pad_topo)
    desenho.rounded_rectangle((ox + S(ponta - 24), oy, ox + S(w), oy + S(h)), radius=S(22), fill=cor + (255,))
    desenho.polygon([(ox, oy + S(h / 2)), (ox + S(ponta), oy), (ox + S(ponta), oy + S(h))], fill=cor + (255,))
    claro = _misturar(cor, (255, 255, 255), 0.45) + (200,)
    desenho.rounded_rectangle((ox + S(ponta + 4), oy + S(12), ox + S(w - 12), oy + S(h - 12)), radius=S(14), outline=claro, width=S(3))
    hx, hy, raio = ox + S(ponta * 0.6), oy + S(h / 2), S(h * 0.075)
    desenho.ellipse((hx - raio - S(6), hy - raio - S(6), hx + raio + S(6), hy + raio + S(6)), fill=claro[:3] + (255,))
    desenho.ellipse((hx - raio, hy - raio, hx + raio, hy + raio), fill=(0, 0, 0, 0))
    desenho.line([(hx - raio * 0.4, hy - raio * 0.8), (hx - S(40), hy - S(70)), (S(6), S(8))], fill=(245, 243, 238, 255), width=S(4), joint="curve")
    peca = grande.resize((w + pad_esq + 12, h + pad_topo + 12), Image.LANCZOS)
    escrever(peca, bloco, pad_esq + ponta + (w - ponta) / 2 - 6, pad_topo + (h - bloco.altura) / 2, tinta(cor), "centro")
    return peca.rotate(-9, resample=Image.BICUBIC, expand=True)


def _layout_etiqueta(ident, textos, formato, pos):
    largura, altura, topo, base = _area(formato)
    enfase, fundo = _cores(ident, textos)
    escuro = _fundo_escuro(fundo)
    largura_texto = largura - 2 * MARGEM
    no_selo = textos.selo.strip() or textos.chamada.strip() or "Oferta"
    chamada_fora = textos.chamada.strip() if textos.selo.strip() else ""
    etiqueta = _peca_etiqueta(no_selo, enfase, 400)
    x_etiqueta = largura - etiqueta.width - 10
    lado_a_lado = x_etiqueta + 70 - MARGEM - 24 >= 440
    largura_titulo = (x_etiqueta + 70 - MARGEM - 24) if lado_a_lado else largura_texto
    titulo = ajustar(textos.titulo, "forte", largura_titulo, 330, 96, 46, 3, 1.02)
    sub = ajustar(textos.subtitulo, "medio", largura_titulo, 110, 38, 26, 2, 1.15) if textos.subtitulo.strip() else None
    w_chamada, h_chamada = medir_pilula(chamada_fora, 40, largura_titulo)
    bloco = titulo.altura + ((18 + sub.altura) if sub else 0) + ((32 + h_chamada) if h_chamada else 0)
    if lado_a_lado:
        total = max(bloco, etiqueta.height - 60)
        y_zona = _y_zona(pos, formato, topo, base, total)
        y_texto = y_zona + (total - bloco) / 2
        y_etiqueta = y_zona + (total - etiqueta.height) / 2 - 30
    else:
        sobra = 50
        total = etiqueta.height - sobra + bloco
        y_zona = _y_zona(pos, formato, topo, base, total)
        if pos == "topo":
            y_texto = y_zona
            y_etiqueta = y_zona + bloco - sobra + 20
        else:
            y_etiqueta = y_zona
            y_texto = y_zona + etiqueta.height - sobra
    y_texto, y_etiqueta = round(y_texto), round(y_etiqueta)
    largura_bloco = max(titulo.largura, sub.largura if sub else 0, w_chamada)
    logo = _logo_oposto(ident, pos, formato, topo, base)
    ocupados = [
        (MARGEM, y_texto - 10, MARGEM + largura_bloco + 10, y_texto + bloco + 10),
        (x_etiqueta + 60, y_etiqueta + 50, largura - 20, y_etiqueta + etiqueta.height - 10),
        logo,
    ]

    def desenhar(tela):
        _escurecer_zona(tela, escuro, pos, min(y_texto, y_etiqueta), max(y_texto + bloco, y_etiqueta + etiqueta.height))
        cor_texto = tinta(escuro)
        texto_com_sombra(tela, titulo, MARGEM, y_texto, cor_texto, raio=12, opacidade=0.4)
        y = y_texto + titulo.altura
        if sub:
            y += 18
            escrever(tela, sub, MARGEM, y, _misturar(cor_texto, escuro, 0.15))
            y += sub.altura
        if h_chamada:
            pilula(tela, chamada_fora, MARGEM, y + 32, enfase, tamanho=40, largura_max=largura_texto)
        colar_com_sombra(tela, etiqueta, x_etiqueta, y_etiqueta, raio=16, opacidade=0.45, desloc=(0, 12))
        _assinar_em(tela, ident, logo)

    return ocupados, desenhar


def _peca_carimbo(titulo: str, selo: str, cor, largura_max: int) -> Image.Image:
    """Carimbo de borda dupla, com falhas de tinta, girado."""
    cor = tuple(cor[:3])
    bloco = ajustar(titulo, "forte", largura_max - 140, 330, 108, 44, 3, 0.98, caixa_alta=True)
    acima = ajustar(selo, "negrito", largura_max - 260, 50, 34, 22, 1, caixa_alta=True) if selo.strip() else None
    w = max(bloco.largura, (acima.largura + 160) if acima else 0) + 140
    h = bloco.altura + 108 + ((acima.altura + 30) if acima else 0)
    escala = 2
    grande = Image.new("RGBA", (w * escala, h * escala), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(grande)
    desenho.rounded_rectangle((8, 8, w * escala - 8, h * escala - 8), radius=52, outline=cor + (255,), width=20)
    desenho.rounded_rectangle((44, 44, w * escala - 44, h * escala - 44), radius=28, outline=cor + (255,), width=7)
    peca = grande.resize((w, h), Image.LANCZOS)
    y = 54
    if acima:
        escrever(peca, acima, w / 2, y, cor + (255,), "centro")
        meio = y + acima.altura / 2
        traco = ImageDraw.Draw(peca)
        traco.line((48, meio, w / 2 - acima.largura / 2 - 22, meio), fill=cor + (255,), width=4)
        traco.line((w / 2 + acima.largura / 2 + 22, meio, w - 48, meio), fill=cor + (255,), width=4)
        y += acima.altura + 30
    escrever(peca, bloco, w / 2, y, cor + (255,), "centro")
    ruido = Image.effect_noise((w, h), 46).point(lambda valor: 0 if valor < 74 else 255).filter(ImageFilter.GaussianBlur(0.8))
    alfa = ImageChops.multiply(peca.getchannel("A"), ruido).point(lambda valor: int(valor * 0.94))
    peca.putalpha(alfa)
    return peca.rotate(7, resample=Image.BICUBIC, expand=True)


def _layout_carimbo(ident, textos, formato, pos):
    largura, altura, topo, base = _area(formato)
    enfase, fundo = _cores(ident, textos)
    escuro = _fundo_escuro(fundo)
    tinta_carimbo = enfase if _contraste(enfase, escuro) >= 2.4 else _misturar(enfase, (255, 255, 255), 0.65)
    carimbo = _peca_carimbo(textos.titulo, textos.selo, tinta_carimbo, largura - 2 * MARGEM - 40)
    sub = ajustar(textos.subtitulo, "negrito", largura - 2 * MARGEM, 110, 42, 28, 2, 1.12) if textos.subtitulo.strip() else None
    w_chamada, h_chamada = medir_pilula(textos.chamada, 40, largura - 2 * MARGEM)
    bloco = carimbo.height + ((16 + sub.altura) if sub else 0) + ((28 + h_chamada) if h_chamada else 0)
    y0 = _y_zona(pos, formato, topo, base, bloco, 40, 0)
    x_carimbo = (largura - carimbo.width) // 2
    logo = _logo_oposto(ident, pos, formato, topo, base)
    meia = max(carimbo.width, sub.largura if sub else 0, w_chamada) / 2
    ocupados = [(largura / 2 - meia, y0, largura / 2 + meia, y0 + bloco), logo]

    def desenhar(tela):
        _escurecer_zona(tela, escuro, pos, y0, y0 + bloco, 215)
        tela.alpha_composite(carimbo, (x_carimbo, y0))
        y = y0 + carimbo.height
        if sub:
            y += 16
            texto_com_sombra(tela, sub, largura / 2, y, BRANCO, "centro", raio=8, opacidade=0.5)
            y += sub.altura
        if h_chamada:
            pilula(tela, textos.chamada, largura / 2, y + 28, enfase, tamanho=40, largura_max=largura - 2 * MARGEM, alinhar="centro")
        _assinar_em(tela, ident, logo)

    return ocupados, desenhar


def _neon(cor) -> tuple[int, int, int]:
    h, s, _v = colorsys.rgb_to_hsv(*(canal / 255 for canal in cor[:3]))
    if s >= 0.2:
        s = max(s, 0.7)
    r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
    return (round(r * 255), round(g * 255), round(b * 255))


def _outro_neon(cor) -> tuple[int, int, int]:
    h, s, _v = colorsys.rgb_to_hsv(*(canal / 255 for canal in cor[:3]))
    if s < 0.2:
        return (125, 211, 252)
    r, g, b = colorsys.hsv_to_rgb((h + 0.5) % 1.0, max(s, 0.6), 1.0)
    return (round(r * 255), round(g * 255), round(b * 255))


def _brilhar(tela: Image.Image, camada: Image.Image, cor) -> None:
    """Contorno de neon: o desenho da camada vira tubo de luz na cor dada."""
    caixa = camada.getbbox()
    if not caixa:
        return
    folga = 90
    x0, y0 = max(0, caixa[0] - folga), max(0, caixa[1] - folga)
    x1, y1 = min(tela.width, caixa[2] + folga), min(tela.height, caixa[3] + folga)
    alfa = camada.getchannel("A").crop((x0, y0, x1, y1))
    cor = tuple(cor[:3])
    for raio, forca in ((36, 0.9), (18, 1.0), (7, 1.0)):
        brilho = Image.new("RGBA", alfa.size, cor + (255,))
        brilho.putalpha(alfa.filter(ImageFilter.GaussianBlur(raio)).point(lambda valor, f=forca: min(255, int(valor * f * 1.6))))
        tela.alpha_composite(brilho, (x0, y0))
    nucleo = Image.new("RGBA", alfa.size, _misturar(cor, (255, 255, 255), 0.72) + (255,))
    nucleo.putalpha(alfa)
    tela.alpha_composite(nucleo, (x0, y0))


def _layout_neon(ident, textos, formato, pos):
    largura, altura, topo, base = _area(formato)
    enfase, _fundo = _cores(ident, textos)
    luz = _neon(enfase)
    segunda = _outro_neon(enfase)
    largura_texto = largura - 2 * MARGEM - 80
    selo = ajustar(textos.selo, "negrito", largura_texto, 50, 34, 22, 1, caixa_alta=True) if textos.selo.strip() else None
    titulo = ajustar(textos.titulo, "forte", largura_texto, 320, 108, 50, 3, 1.04, caixa_alta=True)
    sub = ajustar(textos.subtitulo, "medio", largura_texto, 110, 40, 28, 2, 1.15) if textos.subtitulo.strip() else None
    w_chamada, h_chamada = medir_pilula(textos.chamada, 38, largura_texto)
    moldura = 46
    dentro = (selo.altura + 22 if selo else 0) + titulo.altura + ((22 + sub.altura) if sub else 0)
    largura_moldura = max(titulo.largura, sub.largura if sub else 0, selo.largura if selo else 0) + 2 * moldura
    bloco = dentro + 2 * moldura + ((40 + h_chamada) if h_chamada else 0)
    y0 = _y_zona(pos, formato, topo, base, bloco, 48, 10)
    logo = _logo_oposto(ident, pos, formato, topo, base, "direita")
    ocupados = [(largura / 2 - largura_moldura / 2 - 20, y0 - 20, largura / 2 + largura_moldura / 2 + 20, y0 + bloco + 20), logo]

    def desenhar(tela):
        tela.alpha_composite(Image.new("RGBA", tela.size, (4, 4, 16, 70)))
        _escurecer_zona(tela, (4, 4, 16), pos, y0, y0 + bloco, 230, 300)
        camada = Image.new("RGBA", tela.size, (0, 0, 0, 0))
        ImageDraw.Draw(camada).rounded_rectangle(
            (largura / 2 - largura_moldura / 2, y0, largura / 2 + largura_moldura / 2, y0 + dentro + 2 * moldura), radius=34, outline=(255, 255, 255, 255), width=6
        )
        _brilhar(tela, camada, segunda)
        y = y0 + moldura
        if selo:
            camada = Image.new("RGBA", tela.size, (0, 0, 0, 0))
            escrever(camada, selo, largura / 2, y, (255, 255, 255, 255), "centro")
            _brilhar(tela, camada, segunda)
            y += selo.altura + 22
        camada = Image.new("RGBA", tela.size, (0, 0, 0, 0))
        escrever(camada, titulo, largura / 2, y, (255, 255, 255, 255), "centro")
        _brilhar(tela, camada, luz)
        y += titulo.altura
        if sub:
            y += 22
            texto_com_sombra(tela, sub, largura / 2, y, (245, 245, 250), "centro", raio=10, opacidade=0.6)
        if h_chamada:
            y_chamada = y0 + dentro + 2 * moldura + 40
            camada = Image.new("RGBA", tela.size, (0, 0, 0, 0))
            peca = _peca_pilula(textos.chamada, (0, 0, 0, 0), (255, 255, 255, 255), 38, largura_texto)
            x_chamada = round(largura / 2 - peca.width / 2)
            ImageDraw.Draw(camada).rounded_rectangle((x_chamada, y_chamada, x_chamada + peca.width, y_chamada + peca.height), radius=peca.height // 2, outline=(255, 255, 255, 255), width=5)
            camada.alpha_composite(peca, (x_chamada, round(y_chamada)))
            _brilhar(tela, camada, luz)
        _assinar_em(tela, ident, logo)

    return ocupados, desenhar


def _duotone(tela: Image.Image, escuro, claro, mistura: float = 0.72) -> Image.Image:
    cinza = ImageOps.autocontrast(ImageOps.grayscale(tela.convert("RGB")), cutoff=2)
    duo = ImageOps.colorize(cinza, tuple(escuro[:3]), tuple(claro[:3])).convert("RGBA")
    return Image.blend(tela.convert("RGBA"), duo, mistura)


def _layout_poster(ident, textos, formato, pos):
    largura, altura, topo, base = _area(formato)
    enfase, fundo = _cores(ident, textos)
    cor_bloco = enfase
    cor_texto = tinta(cor_bloco)
    largura_texto = largura - 2 * MARGEM
    selo = ajustar(textos.selo, "negrito", largura_texto, 50, 34, 22, 1, caixa_alta=True) if textos.selo.strip() else None
    titulo = ajustar(textos.titulo, "forte", largura_texto, 560 if formato == "story" else 470, 210, 84, 3, 0.9, caixa_alta=True)
    sub = ajustar(textos.subtitulo, "medio", largura_texto, 110, 42, 28, 2, 1.15) if textos.subtitulo.strip() else None
    cor_pilula = _fundo_escuro(fundo) if _contraste(_fundo_escuro(fundo), cor_bloco) >= 2 else BRANCO
    w_chamada, h_chamada = medir_pilula(textos.chamada, 40, largura_texto)
    folga, corte = 56, 80
    conteudo = (selo.altura + 18 if selo else 0) + titulo.altura + ((20 + sub.altura) if sub else 0) + ((30 + h_chamada) if h_chamada else 0)
    if pos == "topo":
        y_texto = topo + folga
        fim = y_texto + conteudo + folga
        caixa_bloco = (0, 0, largura, fim + corte)
    else:
        y_texto = base - folga - conteudo
        inicio = y_texto - folga
        caixa_bloco = (0, inicio - corte, largura, altura)
    logo = _logo_oposto(ident, pos, formato, topo, base, "direita")
    ocupados = [caixa_bloco, logo]

    def desenhar(tela):
        forma = Image.new("RGBA", tela.size, (0, 0, 0, 0))
        desenho = ImageDraw.Draw(forma)
        if pos == "topo":
            desenho.polygon([(0, 0), (largura, 0), (largura, fim), (0, fim + corte)], fill=tuple(cor_bloco) + (255,))
        else:
            desenho.polygon([(0, inicio), (largura, inicio - corte), (largura, altura), (0, altura)], fill=tuple(cor_bloco) + (255,))
        tela.alpha_composite(_sombra_de(forma, 18, 0.45, (0, 8)))
        tela.alpha_composite(forma)
        y = y_texto
        if selo:
            escrever(tela, selo, MARGEM, y, _misturar(cor_texto, cor_bloco, 0.15))
            y += selo.altura + 18
        escrever(tela, titulo, MARGEM, y, cor_texto)
        y += titulo.altura
        if sub:
            y += 20
            escrever(tela, sub, MARGEM, y, _misturar(cor_texto, cor_bloco, 0.12))
            y += sub.altura
        if h_chamada:
            pilula(tela, textos.chamada, MARGEM, y + 30, cor_pilula, tamanho=40, largura_max=largura_texto, sombra=False)
        _assinar_em(tela, ident, logo)

    return ocupados, desenhar


def _seta(tela: Image.Image, x: float, y: float, tamanho: int, cor) -> None:
    desenho = ImageDraw.Draw(tela)
    desenho.line((x, y, x + tamanho, y), fill=tuple(cor[:3]) + (255,), width=4)
    desenho.polygon([(x + tamanho + 2, y), (x + tamanho - 10, y - 9), (x + tamanho - 10, y + 9)], fill=tuple(cor[:3]) + (255,))


def _layout_minimal(ident, textos, formato, pos):
    largura, altura, topo, base = _area(formato)
    enfase, fundo = _cores(ident, textos)
    elegante = textos.paleta == "elegante"
    barra = (16, 16, 18) if elegante else (250, 249, 246)
    cor_titulo = (244, 239, 228) if elegante else GRAFITE
    cor_sub = _misturar(cor_titulo, barra, 0.4)
    acento = enfase if _contraste(enfase, barra) >= 2.2 else (_fundo_escuro(fundo) if not elegante else (212, 175, 90))
    chip = _chip_assinatura(ident, 60, 200, (0, 0, 0, 0), tuple(acento) + (255,))
    folga_chip = (chip.width + 36) if chip else 0
    if ident.lado == "esquerda":
        x_texto = MARGEM + folga_chip
        largura_texto = largura - MARGEM - x_texto
    elif ident.lado == "centro" and chip:
        x_texto = MARGEM
        largura_texto = max(240, int((largura - chip.width) / 2 - MARGEM - 24))
    else:
        x_texto = MARGEM
        largura_texto = largura - 2 * MARGEM - folga_chip
    selo = ajustar(textos.selo, "negrito", largura_texto, 40, 26, 20, 1, caixa_alta=True) if textos.selo.strip() else None
    titulo = ajustar(textos.titulo, "medio", largura_texto, 170, 66, 40, 2, 1.08)
    sub = ajustar(textos.subtitulo, "medio", largura_texto, 90, 32, 24, 2, 1.25) if textos.subtitulo.strip() else None
    chamada = ajustar(textos.chamada, "negrito", largura_texto - 60, 44, 30, 22, 1) if textos.chamada.strip() else None
    folga = 50
    conteudo = 6 + 24 + ((selo.altura + 14) if selo else 0) + titulo.altura + ((14 + sub.altura) if sub else 0) + ((26 + chamada.altura) if chamada else 0)
    altura_barra = 2 * folga + conteudo
    if pos == "topo":
        y_texto = topo + folga
        caixa_barra = (0, 0, largura, topo + altura_barra)
    else:
        y_texto = base - folga - conteudo
        caixa_barra = (0, y_texto - folga, largura, altura)
    ocupados = [caixa_barra]

    def desenhar(tela):
        if pos == "topo":
            degrade(tela, (0, 0, 0), caixa_barra[3], caixa_barra[3] + 50, 50, 0)
        else:
            degrade(tela, (0, 0, 0), caixa_barra[1] - 50, caixa_barra[1], 0, 50)
        tela.alpha_composite(Image.new("RGBA", (largura, round(caixa_barra[3] - caixa_barra[1])), barra + (250,)), (0, round(caixa_barra[1])))
        desenho = ImageDraw.Draw(tela)
        y = y_texto
        desenho.rectangle((x_texto, y, x_texto + 90, y + 6), fill=tuple(acento) + (255,))
        y += 6 + 24
        if selo:
            escrever(tela, selo, x_texto, y, acento)
            y += selo.altura + 14
        escrever(tela, titulo, x_texto, y, cor_titulo)
        y += titulo.altura
        if sub:
            y += 14
            escrever(tela, sub, x_texto, y, cor_sub)
            y += sub.altura
        if chamada:
            y += 26
            escrever(tela, chamada, x_texto, y, acento)
            _seta(tela, x_texto + chamada.largura + 16, y + chamada.altura / 2, 34, acento)
        if chip:
            meio = y_texto + conteudo / 2
            if ident.lado == "esquerda":
                x_chip = MARGEM
            elif ident.lado == "centro":
                x_chip = (largura - chip.width) / 2
            else:
                x_chip = largura - MARGEM - chip.width
            colar_com_sombra(tela, chip, round(x_chip), round(meio - chip.height / 2), raio=8, opacidade=0.18, desloc=(0, 3))

    return ocupados, desenhar


def _layout_livre(ident, textos, formato, _pos):
    largura, _altura, _topo, base = _area(formato)
    logo = caixa_assinatura(ident, largura - MARGEM, base - (MARGEM if formato == "feed" else 10), "direita", "base", 60, 200, largura)

    def desenhar(tela):
        assinar(tela, ident, largura - MARGEM, base - (MARGEM if formato == "feed" else 10), "direita", "base", 60, 200)

    return [logo], desenhar


LAYOUTS = {
    "faixa": (_layout_faixa, ("topo", "base")),
    "selo": (_layout_selo, ("base", "topo")),
    "destaque": (_layout_destaque, ("base", "topo")),
    "story_chamada": (_layout_story_chamada, ("fixo",)),
    "alerta": (_layout_alerta, ("topo", "base")),
    "etiqueta": (_layout_etiqueta, ("base", "topo")),
    "carimbo": (_layout_carimbo, ("topo", "base")),
    "neon": (_layout_neon, ("base", "topo")),
    "poster": (_layout_poster, ("topo", "base")),
    "minimal": (_layout_minimal, ("base", "topo")),
    "livre": (_layout_livre, ("fixo",)),
}


def _compor_camadas(foto: Image.Image, textos: Textos, ident: Identidade, formato: str) -> Image.Image:
    """A pessoa posicionou cada texto na imagem: desenha a foto e as peças onde ela deixou."""
    largura, altura, _topo, base = _area(formato)
    tela = cobrir(foto, largura, altura)
    enfase, _fundo = _cores(ident, textos)
    tamanhos = {"titulo": 78, "subtitulo": 40, "selo": 30, "chamada": 36}
    for item in textos.camadas or []:
        texto = (item.get("texto") or "").strip()
        if not texto:
            continue
        escala = float(item.get("escala") or 1)
        tamanho = max(18, min(160, round(tamanhos.get(item.get("id"), 48) * escala)))
        peso = item.get("fonte") if item.get("fonte") in ARQUIVOS_FONTE else "forte"
        cor = cor_hex(item.get("cor") or "", "#%02X%02X%02X" % enfase)
        x = float(item.get("x") or 0) * largura
        y = float(item.get("y") or 0) * altura
        largura_max = max(180, largura - int(x) - 36)
        estilo = item.get("estilo") or "faixa"
        if estilo == "texto":
            bloco = ajustar(texto, peso, largura_max, altura, tamanho, max(16, tamanho // 2), 3)
            texto_com_sombra(tela, bloco, x, y, BRANCO if not item.get("cor") else cor)
            continue
        if estilo in ("pilula", "selo"):
            pilula(
                tela,
                texto,
                x,
                y,
                cor,
                tamanho=tamanho,
                largura_max=largura_max,
                caixa_alta=estilo == "selo",
                peso=peso,
            )
            continue
        bloco = ajustar(texto, peso, largura_max, altura, tamanho, max(16, tamanho // 2), 2)
        faixa = Image.new("RGBA", (bloco.largura + 48, bloco.altura + 28), tuple(cor[:3]) + (235,))
        escrever(faixa, bloco, 24, 14, tinta(cor))
        colar_com_sombra(tela, arredondar(faixa, 12), round(x), round(y), raio=12, opacidade=0.3, desloc=(0, 6))
    assinar(tela, ident, largura - MARGEM, base - (MARGEM if formato == "feed" else 10), "direita", "base", 60, 200)
    return tela


def compor(
    foto: Image.Image,
    modelo: str,
    textos: Textos,
    ident: Identidade,
    formato: str = "feed",
    tela_app: bool = False,
    assunto: Caixa | None = None,
) -> Image.Image:
    """Monta a peça final (RGB): feed 1080x1350, story 1080x1920 ou facebook 1080x1080.
    O texto vai para a parte da peça em que o assunto da foto não está."""
    formato = formato if formato in ("story", "facebook") else "feed"
    modelo = modelo if modelo in MODELOS else "destaque"
    if modelo == "selo" and not textos.selo.strip():
        modelo = "destaque"
    if not textos.titulo.strip() and modelo != "livre":
        textos = Textos(**{**textos.__dict__, "titulo": textos.chamada or ident.assinatura or " "})
    foto = foto.convert("RGB")
    if textos.camadas:
        return _compor_camadas(foto, textos, ident, formato).convert("RGB")
    if modelo == "antes_depois":
        return _molde_antes_depois(foto, ident, textos, formato, tela_app).convert("RGB")
    if modelo == "tela_app":
        return _molde_tela_app(foto, ident, textos, formato, tela_app).convert("RGB")
    assunto = assunto or achar_assunto(foto)
    layout, opcoes = LAYOUTS[modelo]
    posicao = textos.posicao if textos.posicao in opcoes else "auto"
    efeito = None
    if modelo == "poster":
        enfase, fundo = _cores(ident, textos)
        efeito = lambda tela: _duotone(tela, _escuro(_fundo_escuro(fundo), 0.2), _misturar(enfase, (255, 255, 255), 0.55))  # noqa: E731
    tela = _rodar(
        foto, assunto, formato, lambda pos: layout(ident, textos, formato, pos), posicao, opcoes, 0.35 if modelo == "neon" else 0.5, efeito
    )
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
