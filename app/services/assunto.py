"""Onde está o assunto da foto (produto, pessoa, prato): a arte põe o texto fora dessa área.
Heurística leve só com Pillow: nitidez (o assunto costuma estar em foco), contraste local, diferença
para a cor das bordas (o fundo) e um peso maior no centro."""

from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageFilter, ImageOps

LADO = 112


def _normalizar(valores: list[float]) -> list[float]:
    ordenados = sorted(valores)
    teto = ordenados[int(len(ordenados) * 0.98)] or 1.0
    return [min(1.0, valor / teto) for valor in valores]


def _quantis(pesos: list[float], baixo: float, alto: float) -> tuple[int, int]:
    total = sum(pesos) or 1.0
    acumulado, inicio, fim = 0.0, 0, len(pesos) - 1
    achou_inicio = False
    for indice, peso in enumerate(pesos):
        acumulado += peso
        if not achou_inicio and acumulado >= total * baixo:
            inicio, achou_inicio = indice, True
        if acumulado >= total * alto:
            fim = indice
            break
    return inicio, fim


def _vao_forte(perfil: list[float], fracao: float, buraco: int) -> tuple[int, int]:
    """O trecho contínuo em volta do pico em que o perfil passa de 'fracao' do pico (aceita buracos curtos)."""
    if not perfil or max(perfil) <= 0:
        return 0, len(perfil) - 1
    pico = max(range(len(perfil)), key=perfil.__getitem__)
    limite = perfil[pico] * fracao
    inicio = fim = pico
    falta = 0
    for indice in range(pico - 1, -1, -1):
        if perfil[indice] >= limite:
            inicio, falta = indice, 0
        else:
            falta += 1
            if falta > buraco:
                break
    falta = 0
    for indice in range(pico + 1, len(perfil)):
        if perfil[indice] >= limite:
            fim, falta = indice, 0
        else:
            falta += 1
            if falta > buraco:
                break
    return inicio, fim


def _maior_regiao(forte: list[float], largura: int, altura: int) -> list[float]:
    """Fica só com a região ligada de mais peso (e as vizinhas quase tão fortes): reflexos e
    luzes soltas do fundo não esticam a caixa do assunto."""
    rotulos = [0] * len(forte)
    pesos: dict[int, float] = {}
    caixas: dict[int, list[int]] = {}
    proximo = 0
    for inicio, valor in enumerate(forte):
        if valor <= 0 or rotulos[inicio]:
            continue
        proximo += 1
        rotulos[inicio] = proximo
        pilha, peso = [inicio], 0.0
        caixa = [largura, altura, 0, 0]
        while pilha:
            atual = pilha.pop()
            peso += forte[atual]
            x, y = atual % largura, atual // largura
            caixa = [min(caixa[0], x), min(caixa[1], y), max(caixa[2], x), max(caixa[3], y)]
            for dy in (-2, -1, 0, 1, 2):
                for dx in (-2, -1, 0, 1, 2):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < largura and 0 <= ny < altura:
                        vizinho = ny * largura + nx
                        if forte[vizinho] > 0 and not rotulos[vizinho]:
                            rotulos[vizinho] = proximo
                            pilha.append(vizinho)
        pesos[proximo], caixas[proximo] = peso, caixa
    if not pesos:
        return forte
    maior = max(pesos, key=pesos.get)
    bx = caixas[maior]
    ficam = {maior}
    for rotulo, peso in pesos.items():
        caixa = caixas[rotulo]
        perto = caixa[0] <= bx[2] + largura * 0.08 and caixa[2] >= bx[0] - largura * 0.08 and caixa[1] <= bx[3] + altura * 0.08 and caixa[3] >= bx[1] - altura * 0.08
        if rotulo != maior and peso >= pesos[maior] * 0.2 and perto:
            ficam.add(rotulo)
    return [valor if rotulos[indice] in ficam else 0.0 for indice, valor in enumerate(forte)]


def _caixa(foto: Image.Image) -> tuple[float, float, float, float]:
    rgb = foto.convert("RGB")
    largura = LADO
    altura = max(16, round(LADO * rgb.height / rgb.width))
    pequeno = rgb.resize((largura, altura), Image.LANCZOS)
    cinza = ImageOps.grayscale(pequeno)
    nitidez = list(cinza.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(3)).getdata())
    borrado = pequeno.filter(ImageFilter.GaussianBlur(7))
    pixels = list(pequeno.getdata())
    medias = list(borrado.getdata())
    contraste = [abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) for a, b in zip(pixels, medias)]
    borda = [pixels[i] for i in range(largura)] + [pixels[(altura - 1) * largura + i] for i in range(largura)]
    borda += [pixels[y * largura] for y in range(altura)] + [pixels[y * largura + largura - 1] for y in range(altura)]
    fundo = tuple(sorted(cor[canal] for cor in borda)[len(borda) // 2] for canal in range(3))
    diferenca = [abs(p[0] - fundo[0]) + abs(p[1] - fundo[1]) + abs(p[2] - fundo[2]) for p in pixels]
    nitidez, contraste, diferenca = _normalizar(nitidez), _normalizar(contraste), _normalizar(diferenca)
    mapa = []
    for indice in range(largura * altura):
        x, y = indice % largura, indice // largura
        dx, dy = (x / (largura - 1) - 0.5) * 2, (y / (altura - 1) - 0.5) * 2
        centro = max(0.15, 1 - 0.55 * (dx * dx + dy * dy))
        valor = 0.45 * nitidez[indice] + 0.3 * contraste[indice] + 0.25 * diferenca[indice]
        mapa.append(valor * centro)
    corte = sorted(mapa)[int(len(mapa) * 0.82)]
    forte = _maior_regiao([valor if valor >= corte else 0.0 for valor in mapa], largura, altura)
    colunas = [sum(forte[y * largura + x] for y in range(altura)) for x in range(largura)]
    linhas = [sum(forte[y * largura : (y + 1) * largura]) for y in range(altura)]
    x0, x1 = _vao_forte(colunas, 0.2, 4)
    y0, y1 = _vao_forte(linhas, 0.05, 6)
    qx0, qx1 = _quantis(colunas, 0.03, 0.97)
    qy0, qy1 = _quantis(linhas, 0.02, 0.98)
    x0, x1 = max(x0, qx0), min(x1, qx1)
    y0, y1 = max(y0, qy0), min(y1, qy1)
    x0, x1 = x0 + (qx0 - x0) * 0.35, x1 + (qx1 - x1) * 0.35
    y0, y1 = y0 + (qy0 - y0) * 0.5, y1 + (qy1 - y1) * 0.5
    folga_x, folga_y = 0.03, 0.035
    caixa = (
        max(0.0, x0 / largura - folga_x),
        max(0.0, y0 / altura - folga_y),
        min(1.0, (x1 + 1) / largura + folga_x),
        min(1.0, (y1 + 1) / altura + folga_y),
    )
    if caixa[2] - caixa[0] < 0.15 or caixa[3] - caixa[1] < 0.15:
        return (0.25, 0.2, 0.75, 0.8)
    return caixa


@lru_cache(maxsize=64)
def _caixa_em_cache(dados: bytes, tamanho: tuple[int, int]) -> tuple[float, float, float, float]:
    return _caixa(Image.frombytes("RGB", tamanho, dados))


def achar_assunto(foto: Image.Image) -> tuple[float, float, float, float]:
    """Caixa do assunto em frações da foto: (x0, y0, x1, y1)."""
    pequena = foto.convert("RGB").copy()
    pequena.thumbnail((256, 256))
    return _caixa_em_cache(pequena.tobytes(), pequena.size)


@dataclass
class Lugar:
    """Onde a foto entra na peça: escala, canto de cima à esquerda e quanto da peça ela cobre (0 a 1)."""

    escala: float
    x: float
    y: float
    cobre: float
    livre: bool = True


def _cruza(a: tuple, b: tuple) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _vaos(ocupados: list[tuple], largura: int, altura: int) -> list[tuple[float, float]]:
    """Faixas de altura livres entre os blocos de texto largos (os que ocupam mais da metade da peça)."""
    largos = sorted((caixa[1], caixa[3]) for caixa in ocupados if caixa[2] - caixa[0] > largura * 0.5)
    vaos, cursor = [], 0.0
    for y0, y1 in largos:
        if y0 > cursor:
            vaos.append((cursor, y0))
        cursor = max(cursor, y1)
    if cursor < altura:
        vaos.append((cursor, float(altura)))
    return vaos or [(0.0, float(altura))]


def _passos(inicio: float, fim: float, quantos: int) -> list[float]:
    if fim <= inicio:
        return [inicio]
    return [inicio + (fim - inicio) * i / (quantos - 1) for i in range(quantos)]


def posicionar(
    tamanho_foto: tuple[int, int],
    assunto: tuple[float, float, float, float],
    largura: int,
    altura: int,
    ocupados: list[tuple],
    folga: int = 44,
) -> Lugar:
    """A maior escala (e o lugar) em que o assunto inteiro fica dentro da peça sem encostar em texto.
    Entre os lugares possíveis, prefere o que cobre mais a peça e o assunto no meio do espaço livre."""
    w, h = tamanho_foto
    ax0, ay0, ax1, ay1 = assunto
    escala = max(largura / w, altura / h)
    menor = min(largura / w, altura / h) * 0.4
    ocupados = [tuple(caixa) for caixa in ocupados if caixa[2] > caixa[0] and caixa[3] > caixa[1]]
    vaos = _vaos(ocupados, largura, altura)
    while escala >= menor:
        pw, ph = w * escala, h * escala
        sx0, sy0, sx1, sy1 = ax0 * pw - folga, ay0 * ph - folga, ax1 * pw + folga, ay1 * ph + folga
        x_lo = max(-sx0, largura - pw - largura * 0.3)
        x_hi = min(largura - sx1, largura * 0.3)
        y_lo = max(-sy0, altura - ph - altura * 0.5)
        y_hi = min(altura - sy1, altura * 0.5)
        if x_lo <= x_hi and y_lo <= y_hi:
            ideal_x, ideal_y = (largura - pw) / 2, (altura - ph) / 2
            xs = sorted(set(_passos(x_lo, x_hi, 9) + [min(max(ideal_x, x_lo), x_hi)]))
            ys = sorted(set(_passos(y_lo, y_hi, 41) + [min(max(ideal_y, y_lo), y_hi)]))
            melhor = None
            for x in xs:
                for y in ys:
                    caixa = (x + sx0, y + sy0, x + sx1, y + sy1)
                    if any(_cruza(caixa, ocupado) for ocupado in ocupados):
                        continue
                    cx = max(0.0, min(largura, x + pw) - max(0.0, x))
                    cy = max(0.0, min(altura, y + ph) - max(0.0, y))
                    cobre = (cx * cy) / (largura * altura)
                    meio_x, meio_y = (caixa[0] + caixa[2]) / 2, (caixa[1] + caixa[3]) / 2
                    vao = next((v for v in vaos if v[0] <= meio_y <= v[1]), (0, altura))
                    distancia = abs(meio_x - largura / 2) / largura + abs(meio_y - (vao[0] + vao[1]) / 2) / altura
                    nota = (round(cobre, 3), -distancia)
                    if melhor is None or nota > melhor[0]:
                        melhor = (nota, x, y, cobre)
            if melhor is not None:
                return Lugar(escala, melhor[1], melhor[2], melhor[3])
        escala *= 0.93
    escala = min(largura / w, altura / h)
    return Lugar(escala, (largura - w * escala) / 2, (altura - h * escala) / 2, 1.0, livre=False)
