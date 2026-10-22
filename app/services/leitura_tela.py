"""Lê um print de sistema: onde está o número, o botão e a faixa que aguenta uma linha."""

import io
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageOps


@dataclass
class LeituraTela:
    e_tela: bool
    quieta: str
    numero: tuple[float, float, float, float]
    botao: tuple[float, float, float, float]
    lista: tuple[float, float, float, float]

    def texto(self) -> str:
        lugar = {"topo": "no topo", "base": "na base"}.get(self.quieta, "em lugar nenhum: não há faixa livre")
        return (
            "Leitura da tela: a única faixa livre para uma linha curta fica "
            + lugar
            + ". O título tem no máximo 4 palavras e nomeia um número, um botão ou um item que está visível. "
            "Sem subtítulo. Sem botão de chamada em cima da tela."
        )


def ler_tela_bytes(dados: bytes) -> LeituraTela:
    with Image.open(io.BytesIO(dados)) as aberta:
        foto = ImageOps.exif_transpose(aberta).convert("RGB")
    return ler_tela(foto)


def ler_tela(foto: Image.Image) -> LeituraTela:
    rgb = foto.convert("RGB")
    rgb.thumbnail((160, 280))
    largura, altura = rgb.size
    pixels = list(rgb.getdata())
    bordas = list(ImageOps.grayscale(rgb).filter(ImageFilter.FIND_EDGES).getdata())
    borda_linha = [_media(bordas[y * largura : (y + 1) * largura]) / 255 for y in range(altura)]
    viva_linha = [_fracao_viva(pixels[y * largura : (y + 1) * largura]) for y in range(altura)]
    ocupada = [borda > 0.12 or viva > 0.22 for borda, viva in zip(borda_linha, viva_linha)]
    meio = _media(borda_linha[int(altura * 0.3) : int(altura * 0.7)] or borda_linha)
    topo = _media(borda_linha[: max(1, int(altura * 0.18))])
    base = _media(borda_linha[int(altura * 0.82) :] or borda_linha[-1:])
    viva_topo = _media(viva_linha[: max(1, int(altura * 0.18))])
    viva_base = _media(viva_linha[int(altura * 0.82) :] or viva_linha[-1:])
    quieta = ""
    if topo <= base and topo < 0.16 and topo < meio * 0.75 and viva_topo < 0.18:
        quieta = "topo"
    elif base < 0.16 and base < meio * 0.75 and viva_base < 0.18:
        quieta = "base"
    botao = _caixa_de_faixa(_faixa(viva_linha, 0.28, 0.02, 0.2), altura)
    lista = _caixa_de_faixa(_faixa([1.0 if item else 0.0 for item in ocupada], 0.5, 0.12, 0.7), altura)
    numero = _caixa_de_faixa(_janela_densa(borda_linha, botao, 0.16), altura)
    if _sobrepoe(numero, lista):
        numero = _miolo(lista)
    ocupadas = sum(1 for item in ocupada if item) / max(1, altura)
    tem_botao = botao[3] - botao[1] >= 0.04
    e_tela = ocupadas > 0.34 or (tem_botao and bool(quieta))
    return LeituraTela(e_tela, quieta, numero, botao, lista)


def _media(valores: list[float]) -> float:
    return sum(valores) / len(valores) if valores else 0.0


def _fracao_viva(linha: list[tuple[int, int, int]]) -> float:
    vivas = 0
    for vermelho, verde, azul in linha:
        maior, menor = max(vermelho, verde, azul), min(vermelho, verde, azul)
        verde_forte = verde > 110 and verde > vermelho + 18 and verde > azul + 8
        viva = maior > 130 and maior - menor > 70
        if verde_forte or viva:
            vivas += 1
    return vivas / len(linha) if linha else 0.0


def _faixa(perfil: list[float], limite: float, minima: float, maxima: float) -> tuple[int, int]:
    """A faixa contínua mais larga acima do limite, entre minima e maxima da altura."""
    altura = len(perfil)
    melhor, atual = (0, 0), (0, 0)
    for indice, valor in enumerate(perfil + [0.0]):
        if valor >= limite:
            if atual[1] <= atual[0]:
                atual = (indice, indice + 1)
            else:
                atual = (atual[0], indice + 1)
            continue
        if atual[1] - atual[0] > melhor[1] - melhor[0]:
            melhor = atual
        atual = (0, 0)
    if melhor[1] <= melhor[0]:
        return 0, 0
    tamanho = (melhor[1] - melhor[0]) / altura
    if tamanho < minima or tamanho > maxima:
        return 0, 0
    return melhor


def _janela_densa(perfil: list[float], evitar: tuple[float, float, float, float], fracao: float) -> tuple[int, int]:
    altura = len(perfil)
    janela = max(4, round(altura * fracao))
    melhor, nota = (0, janela), -1.0
    for inicio in range(0, altura - janela + 1):
        centro = (inicio + janela / 2) / altura
        if evitar[1] <= centro <= evitar[3]:
            continue
        soma = sum(perfil[inicio : inicio + janela])
        if soma > nota:
            melhor, nota = (inicio, inicio + janela), soma
    return melhor


def _caixa_de_faixa(faixa: tuple[int, int], altura: int) -> tuple[float, float, float, float]:
    if faixa[1] <= faixa[0] or altura <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    y0 = max(0.0, faixa[0] / altura - 0.02)
    y1 = min(1.0, faixa[1] / altura + 0.02)
    return (0.06, y0, 0.94, y1)


def _miolo(caixa: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    y0, y1 = caixa[1], caixa[3]
    meio = (y0 + y1) / 2
    return (caixa[0], max(y0, meio - 0.08), caixa[2], min(y1, meio + 0.08))


def _sobrepoe(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[1] < b[3] and b[1] < a[3] and min(a[3], b[3]) - max(a[1], b[1]) > 0.06
