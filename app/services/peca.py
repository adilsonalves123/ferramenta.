"""Anúncio desenhado: a frase, os benefícios e o botão são letra de verdade.
A foto entra só no lugar do produto (celular, se for tela; cartão, se for foto)."""

from PIL import Image, ImageDraw

from app.services import molde

PECAS = ("painel", "passos", "vitrine")


def desenhar(
    foto: Image.Image,
    modelo: str,
    textos: molde.Textos,
    ident: molde.Identidade,
    formato: str = "feed",
    tela_app: bool = False,
) -> Image.Image:
    modelo = modelo if modelo in PECAS else "painel"
    sem_foto = bool(getattr(textos, "sem_foto", False)) or foto is None
    tela_app = bool(tela_app) and not sem_foto
    montar = {"painel": _painel, "passos": _passos, "vitrine": _vitrine}[modelo]
    return montar(foto, textos, ident, formato, tela_app, sem_foto).convert("RGB")


def _medidas(formato: str) -> tuple[int, int, int, int, int]:
    largura, altura, topo, base = molde._area(formato)
    margem = 48 if formato == "facebook" else 68
    return largura, altura, topo, base, margem


def _papel(cor: tuple[int, int, int]) -> tuple[int, int, int]:
    claro = molde._misturar(cor, (255, 252, 247), 0.94)
    return claro if molde._luz(claro) >= 0.9 else (255, 250, 245)


def _noite(ident: molde.Identidade) -> tuple[int, int, int]:
    noite = molde._escuro(ident.principal, 0.78)
    return noite if molde._luz(noite) <= 0.07 else (12, 14, 18)


def _tinta_marca(enfase: tuple[int, int, int], fundo: tuple[int, int, int]) -> tuple[int, int, int]:
    if molde._contraste(enfase, fundo) >= 3.1:
        return enfase
    escuro = molde._escuro(enfase, 0.45)
    if molde._contraste(escuro, fundo) >= 3.1:
        return escuro
    return (22, 24, 28)


def _barra_y(formato: str, base: int, altura: int, barra: int) -> int:
    if formato == "story":
        return base - barra
    return altura - barra


def _logo(tela: Image.Image, ident: molde.Identidade, x: float, y: float, alinhar: str, sobre_escuro: bool, altura: int = 72) -> int:
    if ident.logo is None and not ident.assinatura.strip():
        return round(y)
    if sobre_escuro or ident.logo is None:
        caixa = molde.assinar(
            tela, ident, x, y, alinhar, "topo", altura, 280, posicionado=True
        )
        return caixa[3]
    logo = molde.caber(ident.logo.convert("RGBA"), 280, altura)
    if alinhar == "direita":
        px = round(x - logo.width)
    elif alinhar == "centro":
        px = round(x - logo.width / 2)
    else:
        px = round(x)
    tela.alpha_composite(logo, (px, round(y)))
    return round(y + logo.height)


def _seta(tela: Image.Image, x: float, y: float, cor: tuple[int, int, int], tamanho: int = 22) -> None:
    desenho = ImageDraw.Draw(tela)
    tinta = cor + (255,)
    desenho.line((x, y, x + tamanho, y), fill=tinta, width=max(4, tamanho // 5))
    desenho.polygon(
        [(x + tamanho - 2, y - tamanho * 0.42), (x + tamanho + tamanho * 0.48, y), (x + tamanho - 2, y + tamanho * 0.42)],
        fill=tinta,
    )


def _icone(tela: Image.Image, cx: int, cy: int, raio: int, cor: tuple[int, int, int], indice: int) -> None:
    desenho = ImageDraw.Draw(tela)
    desenho.ellipse((cx - raio, cy - raio, cx + raio, cy + raio), fill=cor + (255,))
    branco = molde.tinta(cor) + (255,)
    grosso = max(3, round(raio * 0.13))
    tipo = indice % 5
    if tipo == 0:
        desenho.line(
            (cx - raio * 0.42, cy + raio * 0.02, cx - raio * 0.08, cy + raio * 0.34, cx + raio * 0.46, cy - raio * 0.36),
            fill=branco,
            width=grosso,
            joint="curve",
        )
    elif tipo == 1:
        desenho.ellipse((cx - raio * 0.24, cy - raio * 0.46, cx + raio * 0.24, cy - raio * 0.02), fill=branco)
        desenho.pieslice((cx - raio * 0.5, cy + raio * 0.02, cx + raio * 0.5, cy + raio * 0.78), 200, 340, fill=branco)
    elif tipo == 2:
        desenho.ellipse((cx - raio * 0.4, cy - raio * 0.4, cx + raio * 0.4, cy + raio * 0.4), outline=branco, width=grosso)
        desenho.line((cx, cy, cx, cy - raio * 0.24), fill=branco, width=grosso)
        desenho.line((cx, cy, cx + raio * 0.2, cy + raio * 0.08), fill=branco, width=grosso)
    elif tipo == 3:
        larguras = (0.22, 0.4, 0.3)
        for i, altura_barra in enumerate(larguras):
            x0 = cx - raio * 0.4 + i * raio * 0.3
            desenho.rounded_rectangle(
                (x0, cy + raio * 0.34 - altura_barra * raio * 1.6, x0 + raio * 0.18, cy + raio * 0.34),
                radius=2,
                fill=branco,
            )
    else:
        desenho.rounded_rectangle(
            (cx - raio * 0.34, cy - raio * 0.28, cx + raio * 0.34, cy + raio * 0.34),
            radius=3,
            outline=branco,
            width=grosso,
        )
        desenho.line((cx - raio * 0.34, cy - raio * 0.02, cx + raio * 0.34, cy - raio * 0.02), fill=branco, width=grosso)


def _circulo(tela: Image.Image, cx: int, cy: int, raio: int, cor: tuple[int, int, int]) -> None:
    desenho = ImageDraw.Draw(tela)
    desenho.ellipse((cx - raio, cy - raio, cx + raio, cy + raio), fill=cor + (255,))


def _produto(foto: Image.Image, largura: int, altura: int, tela_app: bool) -> Image.Image:
    largura = max(80, largura)
    altura = max(80, altura)
    if tela_app:
        return molde._aparelho(foto, largura, altura)
    return molde.arredondar(molde.cobrir(foto, largura, altura, (0.5, 0.4)), 36)


def _monograma(ident: molde.Identidade, cor: tuple[int, int, int], tamanho: int) -> Image.Image:
    peca = Image.new("RGBA", (tamanho, tamanho), (0, 0, 0, 0))
    ImageDraw.Draw(peca).ellipse((0, 0, tamanho - 1, tamanho - 1), fill=cor + (255,))
    letra = (ident.assinatura or "A").strip()[:1].upper() or "A"
    bloco = molde.ajustar(letra, "forte", int(tamanho * 0.62), int(tamanho * 0.62), int(tamanho * 0.42), 48, 1)
    molde.escrever(peca, bloco, tamanho / 2, (tamanho - bloco.altura) / 2, molde.tinta(cor), "centro")
    return peca


def _chamada(textos: molde.Textos) -> str:
    return (textos.chamada or "").strip()


def _fator(textos) -> float:
    try:
        valor = float(getattr(textos, "escala_texto", 1) or 1)
    except (TypeError, ValueError):
        valor = 1.0
    return max(0.7, min(1.65, valor))


def _cor_pedida(textos, campo: str):
    valor = str(getattr(textos, campo, "") or "").strip()
    if not valor:
        return None
    return molde.cor_hex(valor, "#000000")


def _ajustar(textos, texto, peso, largura, altura, maximo, minimo, linhas, entrelinha=1.06, caixa_alta=False):
    fator = _fator(textos)
    return molde.ajustar(
        texto,
        peso,
        int(largura),
        max(int(altura), int(round(altura * fator))),
        max(int(minimo), int(round(maximo * fator))),
        int(minimo),
        linhas,
        entrelinha,
        caixa_alta,
    )


def _botao(tela: Image.Image, texto: str, x: int, y: int, largura: int, altura: int, fundo: tuple[int, int, int]) -> None:
    peca = molde.caixa_redonda((largura, altura), 22, fundo)
    bloco = molde.ajustar(texto, "forte", largura - 150, altura - 20, 40, 22, 1)
    molde.escrever(peca, bloco, largura / 2 - 18, (altura - bloco.altura) / 2, molde.tinta(fundo), "centro")
    _seta(peca, largura - 78, altura / 2, molde.tinta(fundo), 20)
    molde.colar_com_sombra(tela, peca, x, y, raio=18, opacidade=0.28, desloc=(0, 10))


def _lista(textos: molde.Textos, campo: str, limite: int) -> list[str]:
    bruto = getattr(textos, campo, None) or []
    saida = []
    for item in bruto:
        texto = " ".join(str(item).split())
        if texto and texto.lower() not in {linha.lower() for linha in saida}:
            saida.append(texto)
        if len(saida) == limite:
            break
    return saida


def _painel(foto, textos: molde.Textos, ident, formato, tela_app, sem_foto) -> Image.Image:
    largura, altura, topo, base, margem = _medidas(formato)
    enfase, _fundo = molde._cores(ident, textos)
    claro = _cor_pedida(textos, "cor_fundo") or _papel(enfase)
    tela = Image.new("RGBA", (largura, altura), claro + (255,))
    chamada = _chamada(textos)
    barra = (96 if formato != "facebook" else 80) if chamada else 0
    y_barra = _barra_y(formato, base, altura, barra) if barra else altura
    if barra:
        ImageDraw.Draw(tela).rectangle((0, y_barra, largura, y_barra + barra), fill=enfase + (255,))
    topo_util = topo + (24 if formato == "story" else 52)
    fim = y_barra - 36
    tinta_texto = _cor_pedida(textos, "cor_texto")
    cor_titulo = tinta_texto or _tinta_marca(enfase, claro)
    escuro = tinta_texto or (28, 32, 38)
    apoio = tinta_texto or molde._misturar((28, 32, 38), claro, 0.28)
    coluna = int(largura * 0.46)

    zona_x = margem + coluna + 8
    zona_w = largura - zona_x - 20
    zona_y = topo_util + 36
    zona_h = max(180, fim - zona_y)
    cx = zona_x + int(zona_w * 0.58)
    cy = zona_y + zona_h // 2
    raio = int(min(zona_w, zona_h) * 0.58)
    raio = min(raio, cx - (margem + coluna) - 12, y_barra - 8 - cy)
    if not sem_foto and raio > 40:
        _circulo(tela, cx, cy, raio, enfase)
    if sem_foto:
        lado = max(120, min(zona_w, zona_h) - 36)
        mono = _monograma(ident, molde._misturar(enfase, (255, 255, 255), 0.12), lado)
        molde.colar_com_sombra(tela, mono, cx - mono.width // 2, cy - mono.height // 2, raio=20, opacidade=0.22, desloc=(0, 10))
    else:
        peca = _produto(foto, int(zona_w * 0.94), int(zona_h * 0.92), tela_app)
        molde.colar_com_sombra(
            tela,
            peca,
            zona_x + (zona_w - peca.width) // 2,
            zona_y + max(0, (zona_h - peca.height) // 2),
            raio=26,
            opacidade=0.35,
            desloc=(0, 14),
        )

    _logo(tela, ident, margem, topo_util, "esquerda", False, 70)
    y = topo_util + 102
    titulo = _ajustar(textos, textos.titulo, "forte", coluna - 12, 340 if formato != "facebook" else 210, 92 if formato != "facebook" else 60, 36, 4, 0.96)
    molde.escrever(tela, titulo, margem, y, cor_titulo)
    y += titulo.altura + 16
    if textos.subtitulo.strip():
        sub = _ajustar(textos, textos.subtitulo, "medio", coluna - 12, 88, 30, 20, 2, 1.12)
        molde.escrever(tela, sub, margem, y, apoio)
        y += sub.altura + 26
    else:
        y += 12
    for indice, item in enumerate(_lista(textos, "itens", 5)):
        if y + 62 > fim:
            break
        _icone(tela, margem + 26, y + 24, 24, enfase, indice)
        bloco = _ajustar(textos, item, "negrito", coluna - 78, 70, 28, 18, 2, 1.05)
        molde.escrever(tela, bloco, margem + 62, y + 4, escuro)
        y += max(64, bloco.altura + 16)

    if chamada and barra:
        bloco = _ajustar(textos, chamada, "forte", largura - 220, barra - 16, 40, 24, 1)
        meio = y_barra + (barra - bloco.altura) / 2
        molde.escrever(tela, bloco, largura / 2 - 16, meio, molde.tinta(enfase), "centro")
        _seta(tela, largura - 92, meio + bloco.altura / 2, molde.tinta(enfase), 22)
    return tela


def _passos(foto, textos: molde.Textos, ident, formato, tela_app, sem_foto) -> Image.Image:
    largura, altura, topo, base, margem = _medidas(formato)
    enfase, _fundo = molde._cores(ident, textos)
    creme = _cor_pedida(textos, "cor_fundo") or _papel(enfase)
    tinta_texto = _cor_pedida(textos, "cor_texto")
    tela = Image.new("RGBA", (largura, altura), creme + (255,))
    topo_util = topo + (20 if formato == "story" else 48)
    fim = base - (28 if formato == "story" else 40)
    preto = (16, 16, 18)
    escuro = (32, 32, 34)

    y = topo_util
    if textos.selo.strip():
        molde.pilula(
            tela,
            textos.selo,
            largura - margem,
            y,
            enfase,
            tamanho=28,
            largura_max=280,
            alinhar="direita",
            caixa_alta=True,
            peso="forte",
            sombra=False,
        )
    y = _logo(tela, ident, margem, y, "esquerda", False, 64) + 32

    titulo_texto, barra_texto = _partir(textos.titulo, textos.subtitulo)
    titulo = _ajustar(
        textos,
        titulo_texto,
        "forte",
        largura - 2 * margem,
        280 if formato != "facebook" else 180,
        62 if formato != "facebook" else 44,
        32,
        3,
        0.98,
        caixa_alta=True,
    )
    cor_titulo = tinta_texto or (enfase if molde._contraste(enfase, creme) >= 2.6 else _tinta_marca(enfase, creme))
    molde.escrever(tela, titulo, margem, y, cor_titulo)
    y += titulo.altura + 16
    if barra_texto:
        bloco = _ajustar(textos, barra_texto, "forte", largura - 2 * margem - 56, 150, 40, 24, 2, 1.0, caixa_alta=True)
        faixa = molde.caixa_redonda((min(largura - 2 * margem, bloco.largura + 48), bloco.altura + 28), 8, preto)
        molde.escrever(faixa, bloco, 24, 14, (255, 255, 255))
        tela.alpha_composite(faixa, (margem, round(y)))
        y += faixa.height + 22

    rotulos = _lista(textos, "etapas", 3)
    if len(rotulos) < 3:
        for item in _lista(textos, "itens", 3):
            if item not in rotulos:
                rotulos.append(item)
            if len(rotulos) == 3:
                break
    while len(rotulos) < 3:
        rotulos.append("")

    botao_h = 92 if formato != "facebook" else 76
    y_botao = fim - botao_h
    vao = 48
    card_w = (largura - 2 * margem - 2 * vao) // 3
    card_h = max(160, min(460 if formato != "facebook" else 280, y_botao - y - 24))
    y_card = y + max(0, (y_botao - y - card_h) // 2)
    meio = 1 if not sem_foto else -1
    for indice, rotulo in enumerate(rotulos):
        x = margem + indice * (card_w + vao)
        if indice == meio:
            reserva = 70 if rotulo else 0
            peca = _produto(foto, card_w, max(120, card_h - reserva), tela_app)
            y_peca = y_card + max(0, (card_h - reserva - peca.height) // 2)
            molde.colar_com_sombra(tela, peca, x + (card_w - peca.width) // 2, y_peca, raio=22, opacidade=0.32, desloc=(0, 12))
            if rotulo:
                molde.pilula(
                    tela,
                    rotulo,
                    x + card_w / 2,
                    y_peca + peca.height + 12,
                    enfase,
                    tamanho=22,
                    largura_max=card_w - 8,
                    alinhar="centro",
                    caixa_alta=True,
                    peso="forte",
                    sombra=False,
                )
        else:
            cartao = molde.caixa_redonda((card_w, card_h), 28, (255, 255, 255))
            numero = _ajustar(textos, str(indice + 1), "forte", card_w - 36, card_h // 2, 92 if formato != "facebook" else 64, 40, 1)
            molde.escrever(cartao, numero, card_w / 2, 36, enfase if molde._contraste(enfase, (255, 255, 255)) >= 2.4 else preto, "centro")
            if rotulo:
                nome = _ajustar(textos, rotulo, "forte", card_w - 36, card_h // 3, 32, 18, 2, 1.02, caixa_alta=True)
                molde.escrever(cartao, nome, card_w / 2, card_h - nome.altura - 36, tinta_texto or escuro, "centro")
            molde.colar_com_sombra(tela, cartao, x, y_card, raio=18, opacidade=0.16, desloc=(0, 8))
        if indice < 2:
            _seta(tela, x + card_w + 6, y_card + card_h / 2, enfase, 18)

    chamada = _chamada(textos)
    if chamada:
        _botao(tela, chamada, margem, y_botao, largura - 2 * margem, botao_h, enfase)
    return tela


def _partir(titulo: str, subtitulo: str) -> tuple[str, str]:
    titulo = " ".join((titulo or "").split())
    subtitulo = " ".join((subtitulo or "").split())
    if subtitulo:
        return titulo, subtitulo
    palavras = titulo.split()
    if len(palavras) < 5:
        return titulo, ""
    corte = max(2, len(palavras) * 2 // 3)
    return " ".join(palavras[:corte]), " ".join(palavras[corte:])


def _vitrine(foto, textos: molde.Textos, ident, formato, tela_app, sem_foto) -> Image.Image:
    largura, altura, topo, base, margem = _medidas(formato)
    enfase, _fundo = molde._cores(ident, textos)
    noite = _cor_pedida(textos, "cor_fundo") or _noite(ident)
    tela = Image.new("RGBA", (largura, altura), noite + (255,))
    tinta_texto = _cor_pedida(textos, "cor_texto") or (molde.tinta(noite) if _cor_pedida(textos, "cor_fundo") else (255, 255, 255))
    coluna = int(largura * (0.46 if formato == "facebook" else 0.42))
    topo_util = topo + (16 if formato == "story" else 48)
    fim = base - (24 if formato == "story" else 40)
    visual_x = coluna
    visual_w = largura - coluna

    if sem_foto:
        ImageDraw.Draw(tela).rectangle((visual_x, 0, largura, altura), fill=enfase + (255,))
        mono = _monograma(ident, molde._misturar(enfase, (255, 255, 255), 0.16), int(min(visual_w, altura) * 0.42))
        tela.alpha_composite(mono, (visual_x + (visual_w - mono.width) // 2, (altura - mono.height) // 2))
    elif tela_app:
        painel = molde._misturar(noite, (255, 255, 255), 0.08)
        ImageDraw.Draw(tela).rectangle((visual_x, 0, largura, altura), fill=painel + (255,))
        peca = _produto(foto, visual_w - 100, int((fim - topo_util) * 0.7), True)
        molde.colar_com_sombra(
            tela,
            peca,
            visual_x + (visual_w - peca.width) // 2,
            topo_util + max(0, (fim - topo_util - peca.height) // 2),
            raio=26,
            opacidade=0.4,
            desloc=(0, 16),
        )
    else:
        foto_painel = molde.cobrir(foto, visual_w, altura, (0.58, 0.42))
        tela.alpha_composite(foto_painel, (visual_x, 0))

    y = _logo(tela, ident, margem, topo_util, "esquerda", True, 68) + 36
    titulo = _ajustar(
        textos,
        textos.titulo,
        "forte",
        coluna - margem - 28,
        fim - y - 220,
        78 if formato != "facebook" else 54,
        34,
        6,
        0.94,
        caixa_alta=True,
    )
    molde.escrever(tela, titulo, margem, y, tinta_texto)
    y += titulo.altura + 22
    ImageDraw.Draw(tela).rectangle((margem, y, margem + 112, y + 8), fill=enfase + (255,))
    y += 28
    if textos.subtitulo.strip():
        sub = _ajustar(textos, textos.subtitulo, "medio", coluna - margem - 36, 140, 30, 20, 3, 1.15)
        molde.escrever(tela, sub, margem, y, tinta_texto)
        y += sub.altura + 28

    chamada = _chamada(textos)
    if chamada:
        botao_w = coluna - margem - 28
        botao_h = 78
        botao_y = fim - botao_h
        desenho = ImageDraw.Draw(tela)
        desenho.rounded_rectangle(
            (margem, botao_y, margem + botao_w, botao_y + botao_h),
            radius=40,
            outline=(255, 255, 255, 255),
            width=3,
        )
        bloco = _ajustar(textos, chamada, "forte", botao_w - 120, botao_h - 16, 30, 18, 1)
        molde.escrever(tela, bloco, margem + botao_w / 2 - 14, botao_y + (botao_h - bloco.altura) / 2, tinta_texto, "centro")
        _seta(tela, margem + botao_w - 58, botao_y + botao_h / 2, (255, 255, 255), 16)

    segmentos = _lista(textos, "itens", 4)
    if segmentos and not sem_foto and not tela_app:
        faixa_h = 118
        faixa_y = (base if formato == "story" else altura) - faixa_h
        faixa = Image.new("RGBA", (visual_w, faixa_h), (8, 10, 14, 188))
        tela.alpha_composite(faixa, (visual_x, faixa_y))
        coluna_item = visual_w // len(segmentos)
        for indice, item in enumerate(segmentos):
            x = visual_x + indice * coluna_item
            desenho = ImageDraw.Draw(tela)
            desenho.ellipse((x + coluna_item / 2 - 5, faixa_y + 22, x + coluna_item / 2 + 5, faixa_y + 32), fill=enfase + (255,))
            bloco = _ajustar(textos, item, "negrito", coluna_item - 20, 52, 18, 13, 2, 1.05, caixa_alta=True)
            molde.escrever(tela, bloco, x + coluna_item / 2, faixa_y + 44, (255, 255, 255), "centro")
    return tela
