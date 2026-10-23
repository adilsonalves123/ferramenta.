"""Anúncio desenhado. A frase, os benefícios e o botão são letra.
A tela do produto entra inteira — sem recorte — e grande, num lugar diferente em cada peça."""

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


def _retrato(foto: Image.Image | None) -> bool:
    if foto is None or foto.height < 1:
        return False
    return foto.width / foto.height < 0.9


def _colar(tela: Image.Image, peca: Image.Image, x: float, y: float) -> None:
    molde.colar_com_sombra(tela, peca, round(x), round(y), raio=22, opacidade=0.24, desloc=(0, 12))


def _rodape(tela, textos, ctx, y_conteudo: int, formato: str) -> int:
    """Faixa da chamada. No feed ela fecha a peça; no story ela cola no conteúdo."""
    barra = ctx["barra"]
    if not ctx["chamada"] or barra < 40:
        return ctx["y_barra"]
    if formato == "story":
        y_barra = min(y_conteudo + 28, ctx["base"] - barra)
    else:
        y_barra = ctx["altura"] - barra
    ImageDraw.Draw(tela).rectangle((0, y_barra, ctx["largura"], y_barra + barra), fill=ctx["enfase"] + (255,))
    _chamada_na_barra(tela, textos, ctx["chamada"], y_barra, barra, ctx["largura"], ctx["margem"], ctx["enfase"])
    return y_barra


def _chamada_na_barra(tela, textos, chamada: str, y_barra: int, barra: int, largura: int, margem: int, fundo) -> None:
    if not chamada or barra < 40:
        return
    cor = molde.tinta(fundo)
    bloco = _ajustar(textos, chamada, "forte", largura - 2 * margem - 64, barra - 18, 34, 16, 2, 1.02)
    y = y_barra + (barra - bloco.altura) / 2
    molde.escrever(tela, bloco, largura / 2, y, cor, "centro")
    if len(bloco.linhas) == 1:
        _seta(tela, largura - margem - 6, y + bloco.altura / 2, cor, 18)


def _cta(tela, textos, chamada: str, x: int, y: int, largura: int, altura: int, fundo, contorno: bool = False, cor=None) -> None:
    if not chamada or largura < 80 or altura < 44:
        return
    tinta = cor or molde.tinta(fundo)
    bloco = _ajustar(textos, chamada, "forte", largura - 96, altura - 16, 32, 15, 2, 1.02)
    if contorno:
        ImageDraw.Draw(tela).rounded_rectangle(
            (x, y, x + largura, y + altura), radius=altura // 2, outline=tinta + (255,), width=3
        )
        molde.escrever(tela, bloco, x + largura / 2, y + (altura - bloco.altura) / 2, tinta, "centro")
        if len(bloco.linhas) == 1:
            _seta(tela, x + largura - 46, y + altura / 2, tinta, 15)
        return
    peca = molde.caixa_redonda((largura, altura), min(22, altura // 2), fundo)
    molde.escrever(peca, bloco, largura / 2, (altura - bloco.altura) / 2, tinta, "centro")
    if len(bloco.linhas) == 1:
        _seta(peca, largura - 58, altura / 2, tinta, 16)
    molde.colar_com_sombra(tela, peca, x, y, raio=16, opacidade=0.22, desloc=(0, 8))


def _beneficios(tela, textos, itens: list[str], x: int, y: int, largura: int, fim: int, enfase, escuro) -> int:
    if not itens or y >= fim - 36:
        return y
    colunas = 2 if largura >= 680 and len(itens) >= 2 else 1
    vao = 18
    col_w = (largura - vao * (colunas - 1)) // colunas
    blocos = [_ajustar(textos, item, "negrito", col_w - 58, 74, 26, 15, 2, 1.08) for item in itens]
    indice = 0
    while indice < len(blocos):
        grupo = blocos[indice : indice + colunas]
        alto = max(max(56, bloco.altura + 12) for bloco in grupo)
        if y + alto > fim:
            break
        for i, bloco in enumerate(grupo):
            px = x + i * (col_w + vao)
            _icone(tela, px + 18, y + 20, 17, enfase, indice + i)
            molde.escrever(tela, bloco, px + 44, y + 2, escuro)
        y += alto
        indice += colunas
    return y


def _caminho(tela, textos, rotulos: list[str], x: int, y: int, largura: int, enfase, cor) -> int:
    itens = list(rotulos[:3])
    while len(itens) < 3:
        itens.append("")
    vao = 32
    col = (largura - 2 * vao) // 3
    altura = 0
    for indice, rotulo in enumerate(itens):
        px = x + indice * (col + vao)
        numero = _ajustar(textos, str(indice + 1), "forte", col - 8, 62, 52, 28, 1)
        molde.escrever(tela, numero, px + col / 2, y, enfase, "centro")
        extra = 0
        if rotulo.strip():
            nome = _ajustar(textos, rotulo, "forte", col - 6, 68, 22, 14, 2, 1.05, caixa_alta=True)
            molde.escrever(tela, nome, px + col / 2, y + numero.altura + 2, cor, "centro")
            extra = nome.altura + 2
        altura = max(altura, numero.altura + extra)
        if indice < 2:
            _seta(tela, px + col + 6, y + numero.altura * 0.5, enfase, 14)
    return y + altura


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


def _folha(textos, ident, formato):
    largura, altura, topo, base, margem = _medidas(formato)
    enfase, _fundo = molde._cores(ident, textos)
    claro = _cor_pedida(textos, "cor_fundo") or _papel(enfase)
    tela = Image.new("RGBA", (largura, altura), claro + (255,))
    chamada = _chamada(textos)
    barra = (100 if formato != "facebook" else 78) if chamada else 0
    y_barra = _barra_y(formato, base, altura, barra) if barra else (base if formato == "story" else altura)
    tinta_texto = _cor_pedida(textos, "cor_texto")
    return {
        "tela": tela,
        "largura": largura,
        "altura": altura,
        "topo": topo,
        "base": base,
        "margem": margem,
        "enfase": enfase,
        "claro": claro,
        "chamada": chamada,
        "barra": barra,
        "y_barra": y_barra,
        "titulo": tinta_texto or _tinta_marca(enfase, claro),
        "escuro": tinta_texto or (28, 32, 38),
        "apoio": tinta_texto or molde._misturar((28, 32, 38), claro, 0.32),
        "topo_util": topo + (8 if formato == "story" else 36),
    }


def _painel(foto, textos: molde.Textos, ident, formato, tela_app, sem_foto) -> Image.Image:
    if sem_foto or _retrato(foto):
        return _painel_lado(foto, textos, ident, formato, tela_app, sem_foto)
    return _painel_cima(foto, textos, ident, formato, tela_app, sem_foto)


def _painel_lado(foto, textos, ident, formato, tela_app, sem_foto) -> Image.Image:
    ctx = _folha(textos, ident, formato)
    tela, largura, margem = ctx["tela"], ctx["largura"], ctx["margem"]
    enfase, y_barra, fim = ctx["enfase"], ctx["y_barra"], ctx["y_barra"] - 22
    topo_util = ctx["topo_util"]
    faixa = int(largura * (0.48 if formato == "facebook" else 0.46))
    ImageDraw.Draw(tela).rectangle((faixa, 0, largura, y_barra), fill=enfase + (255,))
    zona_x, zona_w = faixa + 28, largura - faixa - 56
    zona_h = max(180, fim - topo_util)
    if sem_foto:
        lado = int(min(zona_w, zona_h) * 0.62)
        mono = _monograma(ident, molde._misturar(enfase, (255, 255, 255), 0.18), lado)
        tela.alpha_composite(mono, (zona_x + (zona_w - lado) // 2, topo_util + (zona_h - lado) // 2))
    else:
        peca = _produto(foto, zona_w, zona_h, tela_app)
        _colar(tela, peca, zona_x + (zona_w - peca.width) // 2, topo_util + max(0, (zona_h - peca.height) // 2))
    coluna = max(120, faixa - margem - 18)
    y = _logo(tela, ident, margem, topo_util, "esquerda", False, 58) + 24
    titulo = _ajustar(textos, textos.titulo, "forte", coluna, min(280, max(100, fim - y - 180)), 64 if formato != "facebook" else 42, 26, 4, 0.96)
    molde.escrever(tela, titulo, margem, y, ctx["titulo"])
    y += titulo.altura + 10
    if textos.subtitulo.strip() and y < fim - 70:
        sub = _ajustar(textos, textos.subtitulo, "medio", coluna, 68, 24, 16, 2, 1.12)
        molde.escrever(tela, sub, margem, y, ctx["apoio"])
        y += sub.altura + 14
    _beneficios(tela, textos, _lista(textos, "itens", 4), margem, y, coluna, fim, enfase, ctx["escuro"])
    if ctx["barra"]:
        ImageDraw.Draw(tela).rectangle((0, y_barra, largura, y_barra + ctx["barra"]), fill=enfase + (255,))
        _chamada_na_barra(tela, textos, ctx["chamada"], y_barra, ctx["barra"], largura, margem, enfase)
    return tela


def _painel_cima(foto, textos, ident, formato, tela_app, sem_foto) -> Image.Image:
    ctx = _folha(textos, ident, formato)
    tela, largura, margem = ctx["tela"], ctx["largura"], ctx["margem"]
    enfase, fim = ctx["enfase"], ctx["y_barra"] - 20
    y = _logo(tela, ident, margem, ctx["topo_util"], "esquerda", False, 56) + 16
    reserva = 250 if formato != "facebook" else 190
    peca = _produto(foto, largura - 2 * margem, max(200, fim - y - reserva), tela_app)
    _colar(tela, peca, (largura - peca.width) // 2, y)
    y += peca.height + 26
    titulo = _ajustar(textos, textos.titulo, "forte", largura - 2 * margem, min(160, max(72, fim - y - 70)), 52 if formato != "facebook" else 36, 26, 2, 0.98)
    molde.escrever(tela, titulo, margem, y, ctx["titulo"])
    y += titulo.altura + 8
    if textos.subtitulo.strip() and y < fim - 60:
        sub = _ajustar(textos, textos.subtitulo, "medio", largura - 2 * margem, 60, 24, 16, 2, 1.1)
        molde.escrever(tela, sub, margem, y, ctx["apoio"])
        y += sub.altura + 12
    y = _beneficios(tela, textos, _lista(textos, "itens", 4), margem, y, largura - 2 * margem, fim, enfase, ctx["escuro"])
    _rodape(tela, textos, ctx, y, formato)
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

    y = _caminho(tela, textos, _etapas(textos), margem, y + 8, largura - 2 * margem, enfase, tinta_texto or escuro) + 26
    chamada = _chamada(textos)
    botao_h = (84 if formato != "facebook" else 70) if chamada else 0
    espaco = fim - y - (botao_h + 24 if chamada else 8)
    if sem_foto:
        lado = max(140, min(340, espaco, largura // 3))
        mono = _monograma(ident, enfase, lado)
        tela.alpha_composite(mono, ((largura - lado) // 2, y + max(0, (max(espaco, lado) - lado) // 2)))
        y_fim = y + max(espaco, lado)
    else:
        peca = _produto(foto, largura - 2 * margem, max(180, espaco), tela_app)
        _colar(tela, peca, (largura - peca.width) // 2, y)
        y_fim = y + peca.height
    if chamada and botao_h:
        _cta(tela, textos, chamada, margem, min(fim - botao_h, y_fim + 20), largura - 2 * margem, botao_h, enfase)
    return tela


def _etapas(textos: molde.Textos) -> list[str]:
    rotulos = _lista(textos, "etapas", 3)
    if len(rotulos) < 3:
        for item in _lista(textos, "itens", 3):
            if item not in rotulos:
                rotulos.append(item)
            if len(rotulos) == 3:
                break
    return rotulos


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
    if not sem_foto and not tela_app:
        return _vitrine_foto(foto, textos, ident, formato)
    if not sem_foto and tela_app and not _retrato(foto):
        return _vitrine_frase(foto, textos, ident, formato)
    return _vitrine_coluna(foto, textos, ident, formato, tela_app, sem_foto)


def _noite_de(textos, ident, formato):
    largura, altura, topo, base, margem = _medidas(formato)
    enfase, _fundo = molde._cores(ident, textos)
    noite = _cor_pedida(textos, "cor_fundo") or _noite(ident)
    tela = Image.new("RGBA", (largura, altura), noite + (255,))
    tinta = _cor_pedida(textos, "cor_texto") or (molde.tinta(noite) if _cor_pedida(textos, "cor_fundo") else (255, 255, 255))
    return tela, largura, altura, topo, base, margem, enfase, noite, tinta


def _vitrine_frase(foto, textos, ident, formato) -> Image.Image:
    """Frase enorme em cima. A tela inteira ocupa a faixa de baixo."""
    tela, largura, _altura, topo, base, margem, enfase, noite, tinta = _noite_de(textos, ident, formato)
    topo_util = topo + (8 if formato == "story" else 40)
    fim = base - (16 if formato == "story" else 28)
    y = _logo(tela, ident, margem, topo_util, "esquerda", True, 60) + 22
    livre = max(280, fim - y)
    titulo = _ajustar(
        textos, textos.titulo, "forte", largura - 2 * margem, int(livre * 0.36),
        88 if formato != "facebook" else 56, 34, 4, 0.92, caixa_alta=True,
    )
    molde.escrever(tela, titulo, margem, y, tinta)
    y += titulo.altura + 16
    ImageDraw.Draw(tela).rectangle((margem, y, margem + 120, y + 8), fill=enfase + (255,))
    y += 24
    if textos.subtitulo.strip():
        sub = _ajustar(textos, textos.subtitulo, "medio", largura - 2 * margem, 90, 28, 18, 2, 1.12)
        molde.escrever(tela, sub, margem, y, tinta)
        y += sub.altura + 16
    chamada = _chamada(textos)
    if chamada:
        _cta(tela, textos, chamada, margem, y, min(640, largura - 2 * margem), 72, noite, contorno=True, cor=tinta)
        y += 88
    alto = max(220, fim - y - 16)
    peca = _produto(foto, largura - 72, alto, True)
    _colar(tela, peca, (largura - peca.width) // 2, y + 8)
    return tela


def _vitrine_coluna(foto, textos, ident, formato, tela_app, sem_foto) -> Image.Image:
    """Frase na esquerda, celular inteiro na direita."""
    tela, largura, altura, topo, base, margem, enfase, _noite_cor, tinta = _noite_de(textos, ident, formato)
    coluna = int(largura * (0.46 if formato == "facebook" else 0.42))
    topo_util = topo + (8 if formato == "story" else 40)
    fim = base - (20 if formato == "story" else 32)
    visual_x = coluna
    visual_w = largura - coluna
    if sem_foto:
        ImageDraw.Draw(tela).rectangle((visual_x, 0, largura, altura), fill=enfase + (255,))
        lado = int(min(visual_w, altura) * 0.42)
        mono = _monograma(ident, molde._misturar(enfase, (255, 255, 255), 0.16), lado)
        tela.alpha_composite(mono, (visual_x + (visual_w - lado) // 2, (altura - lado) // 2))
    else:
        peca = _produto(foto, visual_w - 40, max(200, fim - topo_util), tela_app)
        _colar(tela, peca, visual_x + (visual_w - peca.width) // 2, topo_util + max(0, (fim - topo_util - peca.height) // 2))
    y = _logo(tela, ident, margem, topo_util, "esquerda", True, 60) + 28
    titulo = _ajustar(
        textos, textos.titulo, "forte", coluna - margem - 24, max(120, fim - y - 180),
        72 if formato != "facebook" else 48, 30, 6, 0.94, caixa_alta=True,
    )
    molde.escrever(tela, titulo, margem, y, tinta)
    y += titulo.altura + 18
    ImageDraw.Draw(tela).rectangle((margem, y, margem + 100, y + 8), fill=enfase + (255,))
    y += 22
    if textos.subtitulo.strip():
        sub = _ajustar(textos, textos.subtitulo, "medio", coluna - margem - 28, 110, 26, 16, 3, 1.12)
        molde.escrever(tela, sub, margem, y, tinta)
    chamada = _chamada(textos)
    if chamada:
        _cta(tela, textos, chamada, margem, fim - 74, coluna - margem - 20, 74, (0, 0, 0), contorno=True, cor=tinta)
    return tela


def _vitrine_foto(foto, textos, ident, formato) -> Image.Image:
    tela, largura, altura, topo, base, margem, enfase, _noite_cor, tinta = _noite_de(textos, ident, formato)
    coluna = int(largura * (0.46 if formato == "facebook" else 0.42))
    topo_util = topo + (8 if formato == "story" else 40)
    fim = base - (20 if formato == "story" else 32)
    visual_w = largura - coluna
    tela.alpha_composite(molde.cobrir(foto, visual_w, altura, (0.58, 0.42)), (coluna, 0))
    y = _logo(tela, ident, margem, topo_util, "esquerda", True, 60) + 28
    titulo = _ajustar(
        textos, textos.titulo, "forte", coluna - margem - 24, max(120, fim - y - 200),
        72 if formato != "facebook" else 48, 30, 5, 0.94, caixa_alta=True,
    )
    molde.escrever(tela, titulo, margem, y, tinta)
    y += titulo.altura + 18
    ImageDraw.Draw(tela).rectangle((margem, y, margem + 100, y + 8), fill=enfase + (255,))
    y += 22
    if textos.subtitulo.strip():
        sub = _ajustar(textos, textos.subtitulo, "medio", coluna - margem - 28, 100, 26, 16, 3, 1.12)
        molde.escrever(tela, sub, margem, y, tinta)
    chamada = _chamada(textos)
    if chamada:
        _cta(tela, textos, chamada, margem, fim - 74, coluna - margem - 20, 74, (0, 0, 0), contorno=True, cor=tinta)
    segmentos = _lista(textos, "itens", 4)
    if segmentos:
        faixa_h = 108
        faixa_y = (base if formato == "story" else altura) - faixa_h
        faixa = Image.new("RGBA", (visual_w, faixa_h), (8, 10, 14, 188))
        tela.alpha_composite(faixa, (coluna, faixa_y))
        coluna_item = visual_w // len(segmentos)
        for indice, item in enumerate(segmentos):
            x = coluna + indice * coluna_item
            ImageDraw.Draw(tela).ellipse(
                (x + coluna_item / 2 - 5, faixa_y + 18, x + coluna_item / 2 + 5, faixa_y + 28), fill=enfase + (255,)
            )
            bloco = _ajustar(textos, item, "negrito", coluna_item - 16, 48, 18, 13, 2, 1.05, caixa_alta=True)
            molde.escrever(tela, bloco, x + coluna_item / 2, faixa_y + 38, (255, 255, 255), "centro")
    return tela
