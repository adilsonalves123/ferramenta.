"""Diretor de arte: confere o plano da IA, edita a foto real da marca (ou cria uma) e monta a peça com o molde."""

import asyncio
import hashlib
import json
import logging
import re
import unicodedata

from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Ficha, LogoMarca, MemoriaFoto
from app.services import arte, molde
from app.services.imagens import (
    achar_variante,
    bytes_da_url,
    existe,
    guardar_figura,
    id_da_url,
    id_memoria_da_url,
    url_memoria,
)
from app.services.memoria import ContextoMemoria, chamada_curta, e_captura, fechar_legenda, limpar_gancho
from app.services.openai_campanha import com_moderacao, fala_de_alcool

log = logging.getLogger("uvicorn.error")

NENHUMA = "nenhuma"
CAMPOS_TEXTO = ("titulo_arte", "subtitulo_arte", "selo", "chamada", "antes")
LIMITES = {
    "titulo_arte": (16, 120),
    "subtitulo_arte": (18, 140),
    "selo": (4, 22),
    "chamada": (6, 40),
    "antes": (12, 90),
}
CAMPOS_GUARDADOS = (
    "ideia",
    "foto_base_id",
    "fotos_estilo_ids",
    "edicao_foto",
    "modelo",
    *CAMPOS_TEXTO,
    "cor",
    "paleta",
    "posicao_texto",
    "cor_livre",
    "aviso",
    "tela_app",
    "alcool",
    "base_url",
    "feed_url",
    "story_url",
    "editada",
    "camadas",
    "passos",
    "itens",
    "etapas",
    "sem_foto",
    "cor_texto",
    "cor_fundo",
    "escala_texto",
    "recorte_caixa",
)

REGRAS_ARTE = """

plano_arte: você também é o diretor de arte do post, com liberdade criativa: escolha o estilo e as cores pelo assunto e pelo clima do post, não por uma regra fixa. A foto da memória é material criativo: pode sair como está, ou servir de base para a IA de imagem melhorar (fundo, luz, cenário) mantendo o assunto real idêntico.
- ideia: 1 frase com a ideia criativa da peça.
- foto_base_id: id de uma foto da memória com papel "pode postar como está", "base para editar" ou "a IA decide" que mostra o assunto do post. Nunca uma foto de estilo. Se nenhuma combina, null (aí a foto é criada do zero).
- fotos_estilo_ids: até 2 ids de FOTOS DE ESTILO (ou de fotos do lugar, ou "a IA decide") que inspiram luz, cores e enquadramento. Pode ser [].
- edicao_foto: instrução curta para a IA de imagem, dizendo o que mudar na foto base mantendo o assunto real idêntico (ex.: "fundo limpo e claro, luz suave de fim de tarde, mais profundidade"). Escreva "nenhuma" se a foto for "pode postar como está", se for captura de tela, ou se não tiver foto base e a cena bastar. Em foto "a IA decide", escreva "nenhuma" se ela já está boa para postar; senão diga o que melhorar.
- modelo (o estilo da arte), um destes. Se o pedido listar os moldes da marca, use só esses:
  faixa: faixa inclinada com o título atravessando a peça (energia, recado direto);
  selo: selo redondo grande + barra com o título (novidade, dia da semana, preço que está na memória);
  destaque: degradê com título, subtítulo e chamada (produto, serviço, bastidores);
  alerta: painel com listras de aviso e letras grossas (urgência de verdade: últimas horas, último dia, aviso importante);
  etiqueta: etiqueta de preço pendurada (preço ou condição que está na memória);
  carimbo: carimbo girado de borda dupla (garantia, aprovado, original, o mais pedido);
  neon: letreiro de neon sobre a foto escurecida (noite, festa, música, happy hour);
  poster: tipografia enorme num bloco de cor e a foto em duas cores (evento, lançamento, frase de impacto);
  minimal: barra limpa com letras pequenas e elegantes (marca sofisticada, produto bonito que fala por si);
  antes_depois: o problema do cliente antes x a solução depois;
  tela_app: imagem em destaque, inteira dentro de uma moldura (captura de tela, peça pronta ou produto);
  story_chamada: foto cheia, título no alto e a chamada curta do canal da marca embaixo.
- paleta: "marca" (as duas cores da ficha). Não troque por outra paleta por causa do assunto.
- posicao_texto: "auto" (a arte põe o texto na parte da foto sem o produto); "topo" ou "base" só se a foto pedir.
- titulo_arte: de 2 a 6 palavras, forte, para ler de longe. Sem emoji, sem hashtag.
- subtitulo_arte: até 8 palavras, ou "".
- selo: palavra curta para o selo ("Hoje", "Novo", "Sábado") ou um preço SÓ se o preço está escrito na memória. Senão "".
- chamada: até 4 palavras, no canal da marca (o que está em "Onde o cliente responde" e "Frase da chamada"). Sem "Peça já", "Saiba mais", "Chame no direct" ou "Responda QUERO".
- antes: só no modelo antes_depois, a dor do cliente antes, até 8 palavras. Nos outros modelos, "".
- cor: quando a paleta é "marca", qual cor da marca vai na ênfase: "destaque" ou "principal".
- aviso: "" ou uma frase curta explicando um ajuste que você fez no pedido (ex.: por causa do CONAR).
Regras do plano_arte:
- Use só fatos da memória e do pedido. Não invente preço, desconto, produto, dia nem horário.
- Oferta e promoção só se estiverem em "Promoções fixas" ou se o pedido pedir com todas as letras.
- Captura de tela: modelo tela_app ou antes_depois, edicao_foto "nenhuma".
- Varie a frase e o ângulo. O molde e a paleta ficam no kit da marca."""

REGRAS_ARTE_ALCOOL = """
CONAR na arte (bebida alcoólica):
- Nada de oferta de quantidade: "em dobro", "dose dupla", "2 por 1", "compre 1 leve 2", "open bar", "beba à vontade", "rodada liberada", selo "2x". Se o pedido pedir isso, escolha uma alternativa permitida ("Happy hour de terça", "Drink do dia") e explique em aviso.
- A edição da foto nunca mostra pessoas bebendo nem copo levado à boca.
- A legenda termina com "Beba com moderação." antes das hashtags."""

PRECO = re.compile(r"R\$\s*\d+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?\s*(?:reais|%)|\b\d+[.,]\d{2}\b", re.IGNORECASE)
EXCESSO_ARTE = re.compile(
    r"(?<!\w)(?:(?:em\s+)?dobro|dose\s+dupla|\d+\s*(?:x|por)\s*\d+|dois\s+por\s+um|compre\s+\S+\s+leve\s+\S+|"
    r"leve\s+\S+\s*,?\s*pague\s+\S+|open\s*bar|[àa]\s+vontade|liberad[oa]s?|rodada\s+(?:gr[aá]tis|liberada|dupla|extra)|"
    r"ilimitad[oa]s?|sem\s+limite|\d+\s*x|x\s*\d+|bebam?)(?!\w)",
    re.IGNORECASE,
)
EXCESSO_LEGENDA = re.compile(
    r"(?<!\w)(?:em\s+dobro|dose\s+dupla|\d+\s*(?:x|por)\s*\d+|dois\s+por\s+um|compre\s+\S+\s+leve\s+\S+|"
    r"leve\s+\S+\s*,?\s*pague\s+\S+|open\s*bar|rodada\s+(?:gr[aá]tis|liberada|dupla)|beba\s+mais|"
    r"(?:beb\w*|cervej\w*|chop\w*|drinks?|drinques?|doses?|caipirinhas?)\s+(?:[àa]\s+vontade|liberad[oa]s?|ilimitad[oa]s?))(?!\w)",
    re.IGNORECASE,
)
DIAS = {
    "segunda": "segunda",
    "terca": "terça",
    "quarta": "quarta",
    "quinta": "quinta",
    "sexta": "sexta",
    "sabado": "sábado",
    "domingo": "domingo",
}
# Onde a letra do molde entra. A foto reserva essa faixa; o Pillow escreve depois.
_ZONA_MOLDE = {
    "faixa": "topo",
    "story_chamada": "dupla",
    "selo": "selo",
    "destaque": "base",
    "alerta": "topo",
    "etiqueta": "base",
    "carimbo": "topo",
    "neon": "base",
    "poster": "topo",
    "minimal": "base",
}
_CALMA = {
    "topo": (
        "Commercial framing: the top third stays calm, simple and empty of the subject "
        "(plain wall, soft blur or an uncluttered surface). "
        "The main subject is large and sharp in the lower two thirds and does not touch the top edge. "
        "Do not center the subject. Do not fill the frame with extra objects."
    ),
    "base": (
        "Commercial framing: the bottom third stays calm, simple and slightly darker "
        "(plain surface, soft blur or an uncluttered background). "
        "The main subject is large and sharp in the upper two thirds and does not touch the bottom edge. "
        "Do not center the subject. Do not fill the frame with extra objects."
    ),
    "dupla": (
        "Commercial framing for text that will be added later at the top and at the bottom. "
        "The top quarter and the bottom quarter stay calm, simple and empty of the subject "
        "(plain wall, soft blur or an uncluttered surface). "
        "The main subject is large and sharp in the middle band and does not touch the top or bottom edges. "
        "Do not fill the frame with detail."
    ),
    "selo": (
        "Commercial framing: the top-right corner and the bottom quarter stay calm and empty of the subject. "
        "The main subject sits left of center, large and sharp. "
        "Do not center the subject. Do not fill the corners with extra objects."
    ),
}
_PRESERVAR = {
    "topo": (
        "Keep the real subject identical and where it already is. "
        "Do not add objects, people or detail in the top third: that area will hold text."
    ),
    "base": (
        "Keep the real subject identical and where it already is. "
        "Do not add objects, people or detail in the bottom third: that area will hold text."
    ),
    "dupla": (
        "Keep the real subject identical and where it already is. "
        "Do not add objects, people or detail in the top quarter or the bottom quarter: those areas will hold text."
    ),
    "selo": (
        "Keep the real subject identical and where it already is. "
        "Do not add objects or detail in the top-right corner or the bottom quarter: those areas will hold text."
    ),
}
PALETAS_PLANO = ("marca", *molde.PALETAS)
PECAS_DESENHADAS = ("painel", "passos", "vitrine")
MOLDES_PADRAO = ("destaque", "minimal", "story_chamada")
MOLDES_KIT = (
    "destaque",
    "minimal",
    "story_chamada",
    "faixa",
    "poster",
    "selo",
    "carimbo",
    "etiqueta",
    "alerta",
    "neon",
)
LADOS_LOGO = ("esquerda", "direita", "centro")
CHAMADAS_GENERICAS = {
    "peca ja",
    "peca agora",
    "saiba mais",
    "chame no direct",
    "chama no direct",
    "responda quero",
    "link na bio",
    "confira",
    "venha conferir",
    "aproveite",
    "comece ja",
    "comece agora",
    "experimente",
    "experimente agora",
    "experimente ja",
}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
# Frase de anúncio vazio. "aproveite" sozinho já entra; "nossa tecnologia" pega o slogan inteiro.
_SLOGAN_VAZIO = (
    "aproveite",
    "saiba mais",
    "peca ja",
    "comece ja",
    "comece agora",
    "experimente",
    "praticidade",
    "facilidade",
    "nossa tecnologia",
    "descomplique",
    "simplifique",
    "fale conosco",
    "aumente seus lucros",
    "sem estresse",
    "controle total",
)


def _chamada_vazia(texto: str) -> bool:
    simples = _sem_acento((texto or "").lower())
    if not simples or simples in CHAMADAS_GENERICAS:
        return True
    return any(frase in simples for frase in _SLOGAN_VAZIO)


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def _limpo(valor, limite: int) -> str:
    texto = re.sub(r"[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F]", "", str(valor or ""))
    return " ".join(texto.split())[:limite].strip()


def _texto_arte(valor, palavras: int, caracteres: int) -> str:
    texto = _limpo(valor, 400)
    texto = re.sub(r"#\S+", "", texto)
    texto = re.sub(r"[\"“”«»]", "", texto).strip()
    texto = " ".join(texto.split()[:palavras])
    if len(texto) > caracteres:
        texto = texto[:caracteres].rsplit(" ", 1)[0]
    return texto.strip(" .,;:-")


def _linhas(valor, quantidade: int, palavras: int, caracteres: int) -> list[str]:
    if isinstance(valor, str):
        valor = [parte.strip() for parte in re.split(r"[|\n]", valor)]
    if not isinstance(valor, list):
        return []
    saida: list[str] = []
    vistos: set[str] = set()
    for item in valor:
        texto = _texto_arte(item, palavras, caracteres)
        chave = texto.lower()
        if texto and chave not in vistos:
            saida.append(texto)
            vistos.add(chave)
        if len(saida) >= quantidade:
            break
    return saida


def _sem_preco_linhas(linhas: list[str], fonte: str) -> list[str]:
    permitidos = _precos(fonte)
    saida: list[str] = []
    for linha in linhas:
        for achado in PRECO.findall(linha):
            if re.sub(r"\D", "", achado).lstrip("0") not in permitidos:
                linha = linha.replace(achado, "")
        linha = " ".join(linha.split()).strip(" .,;:-")
        if linha:
            saida.append(linha)
    return saida


def _inteiro(valor) -> int | None:
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None


def _nenhuma(texto: str) -> bool:
    return _sem_acento(texto or "").strip().lower().strip(".") in {"", "nenhuma", "nenhum", "none", "-", "nao", "sem edicao"}


def _precos(texto: str) -> set[str]:
    return {re.sub(r"\D", "", achado).lstrip("0") for achado in PRECO.findall(texto or "")}


def _sem_preco_inventado(plano: dict, fonte: str) -> None:
    """Preço ou porcentagem na arte só se estiver escrito na memória ou no pedido."""
    permitidos = _precos(fonte)
    for campo in CAMPOS_TEXTO:
        valor = plano[campo]
        inventados = [achado for achado in PRECO.findall(valor) if re.sub(r"\D", "", achado).lstrip("0") not in permitidos]
        if not inventados:
            continue
        if campo == "selo":
            plano[campo] = ""
            continue
        for achado in inventados:
            valor = valor.replace(achado, "")
        plano[campo] = " ".join(valor.split()).strip(" .,;:-")


def _limpar_excesso(texto: str, padrao: re.Pattern) -> str:
    limpo = padrao.sub("", texto)
    limpo = re.sub(r"[ \t]{2,}", " ", limpo)
    limpo = re.sub(r" +([,.!?])", r"\1", limpo)
    return "\n".join(linha.strip() for linha in limpo.split("\n")).strip(" ,;:-")


def _titulo_permitido(texto: str) -> str:
    simples = _sem_acento(texto.lower())
    for chave, nome in DIAS.items():
        if re.search(rf"\b{chave}", simples):
            return f"Happy hour de {nome}"
    return "Drink do dia"


def pedido_do_usuario(produto: str) -> str:
    """O que a pessoa pediu no chat (o resto do texto é a ficha da marca)."""
    achado = re.search(r"Pedido:\s*(.*?)\.\s*Tipo de post:", produto or "", re.DOTALL)
    return (achado.group(1) if achado else produto or "")[:600]


def _conar(plano: dict, pedido: str, legenda: str) -> str:
    """Bebida alcoólica: sem oferta de quantidade na arte nem na legenda, e 'Beba com moderação.'"""
    textos = [pedido, legenda, plano["ideia"], *(plano[campo] for campo in CAMPOS_TEXTO)]
    if not fala_de_alcool(" ".join(textos)):
        plano["alcool"] = False
        return legenda
    plano["alcool"] = True
    trocas: list[tuple[str, str]] = []
    for campo in CAMPOS_TEXTO:
        valor = plano[campo]
        if not valor or not EXCESSO_ARTE.search(valor):
            continue
        if campo == "titulo_arte":
            novo = _titulo_permitido(f"{valor} {pedido}")
        elif campo == "selo":
            novo = ""
        elif campo == "chamada":
            novo = "Venha conferir"
        else:
            novo = _limpar_excesso(valor, EXCESSO_ARTE)
            if len(novo.split()) < 2:
                novo = ""
        trocas.append((valor, novo))
        plano[campo] = novo
    if EXCESSO_ARTE.search(plano["ideia"]):
        plano["ideia"] = _limpar_excesso(plano["ideia"], EXCESSO_ARTE)
    nova_legenda = _limpar_excesso(legenda, EXCESSO_LEGENDA) if legenda and EXCESSO_LEGENDA.search(legenda) else legenda
    if trocas or nova_legenda != legenda or EXCESSO_ARTE.search(pedido or ""):
        antes = next((velho for velho, _novo in trocas if velho), "")
        troca = f'Troquei "{antes}" por "{plano["titulo_arte"]}". ' if antes and plano["titulo_arte"] != antes else ""
        plano["aviso"] = (
            troca
            + "Pelo CONAR, propaganda de bebida alcoólica não pode oferecer quantidade "
            + '(como "em dobro", "2 por 1" ou "open bar"). A legenda termina com "Beba com moderação."'
        )
    return com_moderacao(nova_legenda) if nova_legenda else nova_legenda


def _camadas_validas(bruto) -> list:
    """Posição livre de cada texto na imagem: arrastar, tamanho, fonte, cor e estilo."""
    if not isinstance(bruto, list):
        return []
    limites = {"titulo": LIMITES["titulo_arte"], "subtitulo": LIMITES["subtitulo_arte"], "selo": LIMITES["selo"], "chamada": LIMITES["chamada"]}
    estilos = {"texto", "faixa", "pilula", "selo"}
    fontes = {"forte", "negrito", "medio"}
    saida = []
    for item in bruto[:6]:
        if not isinstance(item, dict) or item.get("id") not in limites:
            continue
        palavras, caracteres = limites[item["id"]]
        texto = _texto_arte(item.get("texto"), palavras, caracteres)
        if not texto:
            continue
        def numero(chave: str, padrao: float, minimo: float, maximo: float) -> float:
            try:
                valor = float(item.get(chave))
            except (TypeError, ValueError):
                valor = padrao
            return max(minimo, min(maximo, valor))

        cor = str(item.get("cor") or "").strip().upper()
        saida.append(
            {
                "id": item["id"],
                "texto": texto,
                "x": round(numero("x", 0.08, 0, 0.86), 4),
                "y": round(numero("y", 0.08, 0, 0.92), 4),
                "escala": round(numero("escala", 1, 0.55, 2.4), 3),
                "fonte": item.get("fonte") if item.get("fonte") in fontes else "forte",
                "cor": cor if HEX.match(cor) else "",
                "estilo": item.get("estilo") if item.get("estilo") in estilos else "faixa",
            }
        )
    return saida


def _coerencia(plano: dict) -> None:
    if plano.get("paleta") == "livre" and not plano.get("cor_livre"):
        plano["paleta"] = "marca"
    if plano["modelo"] == "selo" and not plano["selo"]:
        plano["modelo"] = "destaque"
    if plano["modelo"] == "antes_depois" and not plano["antes"]:
        plano["modelo"] = "tela_app" if plano["tela_app"] else "destaque"
    if plano["modelo"] != "antes_depois":
        plano["antes"] = ""


def validar_plano(
    bruto,
    contexto: ContextoMemoria | None = None,
    pedido: str = "",
    legenda: str = "",
    gancho: str = "",
) -> tuple[dict, str]:
    """Deixa o plano seguro (ids da marca, papel das fotos, preço, CONAR). Devolve (plano, legenda ajustada)."""
    dados = bruto.model_dump() if isinstance(bruto, BaseModel) else dict(bruto or {})
    contexto = contexto or ContextoMemoria()
    plano: dict = {
        "ideia": _limpo(dados.get("ideia"), 220),
        "modelo": dados.get("modelo") if dados.get("modelo") in molde.MODELOS else "destaque",
        "cor": "principal" if dados.get("cor") == "principal" else "destaque",
        "paleta": dados.get("paleta") if dados.get("paleta") in (*PALETAS_PLANO, "livre") else "marca",
        "posicao_texto": dados.get("posicao_texto") if dados.get("posicao_texto") in molde.POSICOES_TEXTO else "auto",
        "cor_livre": str(dados.get("cor_livre") or "").strip().upper() if HEX.match(str(dados.get("cor_livre") or "").strip()) else "",
        "edicao_foto": _limpo(dados.get("edicao_foto"), 400),
        "aviso": _limpo(dados.get("aviso"), 300),
    }
    for campo in CAMPOS_TEXTO:
        plano[campo] = _texto_arte(dados.get(campo), *LIMITES[campo])
    if not plano["titulo_arte"]:
        plano["titulo_arte"] = _texto_arte(gancho, *LIMITES["titulo_arte"])
    base = _inteiro(dados.get("foto_base_id"))
    if base not in contexto.usaveis:
        base = None
    estilos: list[int] = []
    for valor in dados.get("fotos_estilo_ids") or []:
        ident = _inteiro(valor)
        livre = contexto.papeis.get(ident) == "auto" and contexto.tipos.get(ident) != "captura"
        if ident and ident != base and ident not in estilos and (ident in contexto.estilo or ident in contexto.lugar or livre):
            estilos.append(ident)
    plano["foto_base_id"] = base
    plano["fotos_estilo_ids"] = estilos[:2]
    plano["tela_app"] = bool(base) and contexto.tipos.get(base) == "captura"
    if plano["tela_app"]:
        plano["edicao_foto"] = NENHUMA
        if plano["modelo"] not in ("tela_app", "antes_depois", "tela_limpa", *PECAS_DESENHADAS):
            plano["modelo"] = "tela_app"
    if base and contexto.papeis.get(base) == "postar":
        plano["edicao_foto"] = NENHUMA
    if _nenhuma(plano["edicao_foto"]):
        plano["edicao_foto"] = NENHUMA
    _sem_preco_inventado(plano, f"{contexto.fatos} {pedido}")
    legenda = fechar_legenda(_conar(plano, pedido, legenda), contexto.mapa)
    curta = chamada_curta(contexto.mapa)
    generica = _chamada_vazia(plano["chamada"])
    if generica or not plano["chamada"]:
        plano["chamada"] = _texto_arte(curta, *LIMITES["chamada"]) if curta else ""
    _coerencia(plano)
    plano["camadas"] = _camadas_validas(dados.get("camadas"))
    fonte = f"{contexto.fatos} {pedido}"
    plano["itens"] = _sem_preco_linhas(_linhas(dados.get("itens"), 5, 8, 48), fonte)
    plano["etapas"] = _sem_preco_linhas(_linhas(dados.get("etapas"), 3, 4, 28), fonte)
    plano["sem_foto"] = bool(dados.get("sem_foto"))
    plano["cor_texto"] = str(dados.get("cor_texto") or "").strip().upper()
    plano["cor_fundo"] = str(dados.get("cor_fundo") or "").strip().upper()
    if plano["cor_texto"] and not HEX.match(plano["cor_texto"]):
        plano["cor_texto"] = ""
    if plano["cor_fundo"] and not HEX.match(plano["cor_fundo"]):
        plano["cor_fundo"] = ""
    try:
        escala = float(dados.get("escala_texto") or 1)
    except (TypeError, ValueError):
        escala = 1
    plano["escala_texto"] = round(min(1.65, max(0.7, escala)), 2)
    return plano, legenda


def tem_cores(marca: Ficha | None) -> bool:
    return bool(marca and ((marca.cor_principal or "").strip() or (marca.cor_destaque or "").strip()))


def normalizar_moldes(texto: str) -> str:
    escolhidos: list[str] = []
    for nome in (texto or "").split(","):
        nome = nome.strip()
        if nome in MOLDES_KIT and nome not in escolhidos:
            escolhidos.append(nome)
        if len(escolhidos) == 3:
            break
    return ",".join(escolhidos)


def moldes_da_marca(marca: Ficha | None) -> tuple[str, ...]:
    texto = normalizar_moldes(getattr(marca, "moldes", "") or "")
    return tuple(texto.split(",")) if texto else MOLDES_PADRAO


def lado_da_marca(marca: Ficha | None) -> str:
    lado = (getattr(marca, "logo_lado", "") or "").strip()
    return lado if lado in LADOS_LOGO else ""


def texto_kit(marca: Ficha | None) -> str:
    """O que a IA lê para não trocar o figurino da marca."""
    nomes = ", ".join(moldes_da_marca(marca))
    lado = lado_da_marca(marca)
    extra = f" Logo no {lado}." if lado else ""
    return (
        "Kit visual desta marca: paleta sempre \"marca\", as duas cores da ficha. "
        f"Moldes permitidos, e só estes: {nomes}. "
        "Se forem três artes, cada uma usa um molde dessa lista e a posição do texto muda "
        "(auto, topo ou base). As frases não são sinônimos: cada arte defende um argumento diferente. "
        "Não invente outra paleta."
        + extra
    )


def aplicar_kit(plano: dict, marca: Ficha | None) -> dict:
    """Prende a arte nas cores e nos moldes da marca. Captura de tela continua no molde dela."""
    plano = dict(plano)
    if plano.get("modelo") not in ("tela_app", "antes_depois", "livre", "tela_limpa", *PECAS_DESENHADAS):
        moldes = moldes_da_marca(marca)
        if plano.get("modelo") not in moldes:
            plano["modelo"] = moldes[0]
    plano["paleta"] = "marca"
    plano["cor_livre"] = ""
    return plano


def variar(plano: dict, recentes: list[tuple[str, str]], marca: Ficha | None = None) -> dict:
    """Mantém a paleta da marca e, se o mesmo molde saiu três vezes, troca por outro do kit."""
    plano = aplicar_kit(plano, marca)
    if plano.get("modelo") in ("tela_app", "antes_depois", "livre", "tela_limpa", *PECAS_DESENHADAS):
        _coerencia(plano)
        return plano
    moldes = moldes_da_marca(marca)
    usados = [modelo for modelo, _paleta in recentes]
    if len(moldes) > 1 and usados.count(plano["modelo"]) >= 3:
        outros = [modelo for modelo in moldes if modelo not in usados]
        if outros:
            plano["modelo"] = outros[0]
    _coerencia(plano)
    return plano


def plano_padrao(legenda: str, gancho: str = "") -> dict:
    """Plano simples quando a legenda foi escrita à mão (sem plano da IA)."""
    primeira = next((linha for linha in (legenda or "").splitlines() if linha.strip()), "")
    titulo = limpar_gancho(gancho) or limpar_gancho(re.sub(r"#\S+", "", primeira))
    plano, _legenda = validar_plano(
        {"modelo": "destaque", "titulo_arte": titulo, "cor": "destaque", "edicao_foto": NENHUMA}, None, "", legenda
    )
    return plano


def _url_interna(valor) -> str:
    texto = str(valor or "").strip()[:500]
    return texto if id_da_url(texto) is not None or id_memoria_da_url(texto) is not None else ""


def do_cliente(dados: dict | None, contexto: ContextoMemoria) -> dict | None:
    """Plano que voltou do navegador (editado na prévia): valida de novo e guarda só links internos."""
    if not isinstance(dados, dict) or not dados.get("modelo"):
        return None
    escrito = " ".join(str(dados.get(campo) or "") for campo in CAMPOS_TEXTO)
    plano, _legenda = validar_plano(dados, contexto, escrito)
    for chave in ("base_url", "feed_url", "story_url"):
        plano[chave] = _url_interna(dados.get(chave))
    plano["editada"] = bool(dados.get("editada"))
    return plano


def ler_plano(texto: str | None) -> dict | None:
    try:
        plano = json.loads(texto or "")
    except ValueError:
        return None
    return plano if isinstance(plano, dict) and plano.get("modelo") else None


def gravar_plano(plano: dict | None) -> str:
    if not plano:
        return ""
    return json.dumps({chave: plano.get(chave) for chave in CAMPOS_GUARDADOS if chave in plano}, ensure_ascii=False)


def resumo_plano(plano: dict | None) -> dict | None:
    """O que a prévia e a fila mostram e deixam editar."""
    if not plano:
        return None
    return {chave: plano.get(chave) for chave in CAMPOS_GUARDADOS if chave != "passos"}


async def _foto_da_marca(session: AsyncSession, foto_id, marca_id: int) -> MemoriaFoto | None:
    ident = _inteiro(foto_id)
    if not ident:
        return None
    foto = await session.get(MemoriaFoto, ident)
    if foto is None or foto.marca_id != marca_id:
        return None
    return foto


async def _fotos_estilo(session: AsyncSession, plano: dict, marca_id: int) -> list[bytes]:
    fotos = []
    for ident in plano.get("fotos_estilo_ids") or []:
        foto = await _foto_da_marca(session, ident, marca_id)
        if foto is not None and not e_captura(foto):
            fotos.append(foto.arquivo)
    return fotos


def zona_composicao(modelo: str, posicao: str = "auto") -> str:
    """Faixa calma da foto: a posição pedida manda; senão, a faixa natural do molde."""
    if posicao == "topo":
        return "topo"
    if posicao == "base":
        return "base"
    return _ZONA_MOLDE.get(modelo or "", "dupla")


def zona_dos_planos(planos: list[dict]) -> str:
    """Uma foto serve várias artes: se o texto cai em cima e embaixo, as duas faixas ficam calmas."""
    if len(planos) <= 1:
        plano = planos[0] if planos else {}
        return zona_composicao(str(plano.get("modelo") or ""), str(plano.get("posicao_texto") or "auto"))
    posicoes = {str(plano.get("posicao_texto") or "auto") for plano in planos}
    if posicoes == {"topo"}:
        return "topo"
    if posicoes == {"base"}:
        return "base"
    return "dupla"


def frase_da_zona(zona: str, preservar: bool = False) -> str:
    tabela = _PRESERVAR if preservar else _CALMA
    return tabela.get(zona, tabela["dupla"])


def prompt_edicao(plano: dict, com_estilo: bool, zona: str = "") -> str:
    edicao = (plano.get("edicao_foto") or "").strip()
    if not edicao or edicao == NENHUMA:
        edicao = "Do not change the photograph. Keep the subject, background, lighting and framing exactly as they are."
    zona = zona or zona_composicao(str(plano.get("modelo") or ""), str(plano.get("posicao_texto") or "auto"))
    partes = [
        "Edit this real photo of the business into a premium Instagram photo.",
        "KEEP THE REAL SUBJECT IDENTICAL: same shape, size, colors, materials, details, packaging, label and branding. "
        "Do not replace, redraw, restyle or add another product or object.",
        f"What to change: {edicao}.",
        "Photographic realism, attractive professional lighting, sharp focus on the main subject, natural depth of field.",
        frase_da_zona(zona, preservar=True),
        "No text, no letters, no numbers, no logos, no watermark, no frame.",
    ]
    if com_estilo:
        partes.append(
            "The second image is ONLY a style reference (lighting, color palette, mood, framing). Do not copy its objects."
        )
    if plano.get("alcool"):
        partes.append("No people drinking, no glass raised to a mouth, no minors.")
    return " ".join(partes)


def prompt_criacao(plano: dict, cena: str, com_estilo: bool, zona: str = "") -> str:
    assunto = (cena or plano.get("ideia") or plano.get("titulo_arte") or "the business").strip()[:600]
    zona = zona or zona_composicao(str(plano.get("modelo") or ""), str(plano.get("posicao_texto") or "auto"))
    partes = [
        f"Realistic photograph for an Instagram post: {assunto}.",
        "Attractive professional lighting, sharp focus on the main subject, natural depth of field.",
        frase_da_zona(zona),
        "No text, no letters, no numbers, no logos, no watermark, no screens with words.",
    ]
    if plano.get("edicao_foto") and plano["edicao_foto"] != NENHUMA:
        partes.insert(1, f"Details: {plano['edicao_foto']}.")
    if com_estilo:
        partes.append(
            "Use the given image ONLY as style inspiration (lighting, colors, mood, framing): create a new photo, do not copy it."
        )
    if plano.get("alcool"):
        partes.append("No people drinking, no glass raised to a mouth, no minors.")
    return " ".join(partes)


def _sem_edicao(foto: MemoriaFoto, plano: dict, forcar_ia: bool) -> bool:
    if e_captura(foto):
        return True
    if (plano.get("edicao_foto") or NENHUMA) == NENHUMA:
        return True
    if forcar_ia:
        return False
    return foto.papel == "postar"


async def vai_usar_ia(session: AsyncSession, marca_id: int, plano: dict, forcar_ia: bool = False) -> bool:
    """True quando montar esta arte pede uma foto nova da IA (custa); False quando sai só com o molde."""
    foto = await _foto_da_marca(session, plano.get("foto_base_id"), marca_id)
    if foto is not None and _sem_edicao(foto, plano, forcar_ia):
        return False
    if forcar_ia:
        return True
    base = plano.get("base_url") or ""
    return not (base and await existe(base))


async def _preparar_base(
    session: AsyncSession, marca_id: int, plano: dict, cena: str, forcar_ia: bool, zona: str = ""
) -> tuple[bytes, str, str, bool]:
    """A foto base da arte: (bytes, url, o que foi feito, se a IA mexeu)."""
    from app.services.midia import editar_com_ia, gerar_com_ia

    foto = await _foto_da_marca(session, plano.get("foto_base_id"), marca_id)
    estilos = await _fotos_estilo(session, plano, marca_id)
    if foto is not None:
        if _sem_edicao(foto, plano, forcar_ia):
            return foto.arquivo, url_memoria(foto.id or 0), "foto da memória usada como está (sem custo de imagem)", False
        try:
            dados, usadas = await editar_com_ia(prompt_edicao(plano, bool(estilos), zona), [foto.arquivo, *estilos[:1]])
        except Exception as erro:
            if forcar_ia:
                raise
            motivo = str(erro).replace("\n", " ")[:120]
            return foto.arquivo, url_memoria(foto.id or 0), f"a edição com IA falhou ({motivo}); usei a foto original", False
        figura = await asyncio.to_thread(arte.abrir, dados)
        url = await guardar_figura(figura, marca_id, "arte-base")
        return dados, url, f"foto da memória editada com IA ({usadas} foto(s) de entrada)", True
    if estilos:
        dados, _usadas = await editar_com_ia(prompt_criacao(plano, cena, True, zona), estilos[:1])
        passo = "foto criada com IA, inspirada numa foto de estilo"
    else:
        dados = await gerar_com_ia(prompt_criacao(plano, cena, False, zona))
        passo = "foto criada com IA do zero"
    figura = await asyncio.to_thread(arte.abrir, dados)
    url = await guardar_figura(figura, marca_id, "arte-base")
    return dados, url, passo, True


async def identidade(session: AsyncSession, marca: Ficha | None) -> tuple[molde.Identidade, str]:
    """Cores, logo e assinatura da marca, e uma chave que muda quando algum deles muda."""
    if marca is None or not marca.id:
        return molde.Identidade(), "sem-marca"
    logo = await session.get(LogoMarca, marca.id)
    ident = molde.Identidade(
        principal=molde.cor_hex(marca.cor_principal, molde.COR_PRINCIPAL),
        destaque=molde.cor_hex(marca.cor_destaque, molde.COR_DESTAQUE),
        logo=molde.abrir_logo(logo.arquivo) if logo else None,
        assinatura=(marca.marca_dagua or marca.nome or "").strip(),
        lado=lado_da_marca(marca),
    )
    chave = f"{ident.principal}|{ident.destaque}|{ident.assinatura}|{ident.lado}|{logo.atualizado_em.isoformat() if logo else ''}"
    return ident, chave


def textos_do_plano(plano: dict) -> molde.Textos:
    return molde.Textos(
        titulo=plano.get("titulo_arte") or "",
        subtitulo=plano.get("subtitulo_arte") or "",
        selo=plano.get("selo") or "",
        chamada=plano.get("chamada") or "",
        antes=plano.get("antes") or "",
        cor=plano.get("cor") or "destaque",
        paleta=plano.get("paleta") or "marca",
        posicao=plano.get("posicao_texto") or "auto",
        cor_livre=plano.get("cor_livre") or "",
        camadas=list(plano.get("camadas") or []),
        itens=list(plano.get("itens") or []),
        etapas=list(plano.get("etapas") or []),
        sem_foto=bool(plano.get("sem_foto")),
        cor_texto=plano.get("cor_texto") or "",
        cor_fundo=plano.get("cor_fundo") or "",
        escala_texto=float(plano.get("escala_texto") or 1),
    )


async def _montar(session: AsyncSession, marca: Ficha | None, plano: dict, base: bytes) -> tuple[str, str]:
    ident, chave_marca = await identidade(session, marca)
    textos = textos_do_plano(plano)
    marca_id = marca.id if marca and marca.id else 0
    chave = hashlib.sha1(
        json.dumps(
            [molde.VERSAO, plano["modelo"], textos.__dict__, chave_marca, plano.get("base_url"), plano.get("tela_app")],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()[:16]
    base_id = id_da_url(plano.get("base_url") or "")
    if base_id is not None:
        feed = await achar_variante(base_id, f"arte-feed:{chave}")
        story = await achar_variante(base_id, f"arte-story:{chave}")
        if feed and story:
            return feed, story

    def desenhar(formato: str):
        return molde.compor(arte.abrir(base), plano["modelo"], textos, ident, formato, bool(plano.get("tela_app")))

    feed_img, story_img = await asyncio.gather(asyncio.to_thread(desenhar, "feed"), asyncio.to_thread(desenhar, "story"))
    feed = await guardar_figura(feed_img, marca_id, "arte", base_id, f"arte-feed:{chave}")
    story = await guardar_figura(story_img, marca_id, "arte", base_id, f"arte-story:{chave}")
    return feed, story


async def desenhar_um(
    session: AsyncSession, marca: Ficha | None, plano: dict, base: bytes, formato: str
) -> str:
    """Monta uma peça só, no tamanho pedido, com a foto que já existe. Sem custo de IA."""
    ident, _chave = await identidade(session, marca)
    textos = textos_do_plano(plano)
    marca_id = marca.id if marca and marca.id else 0
    alvo = formato if formato in ("story", "facebook") else "feed"

    recortes = {
        "topo": (0.0, 0.0, 1.0, 0.42),
        "meio": (0.0, 0.3, 1.0, 0.7),
        "base": (0.0, 0.58, 1.0, 1.0),
    }

    def desenhar():
        caixa = plano.get("recorte_caixa")
        if isinstance(caixa, (list, tuple)) and len(caixa) == 4:
            assunto = tuple(float(valor) for valor in caixa)
        elif plano.get("modelo") == "tela_limpa":
            assunto = (0.0, 0.0, 1.0, 1.0)
        else:
            assunto = recortes.get(str(plano.get("recorte") or ""))
        return molde.compor(
            arte.abrir(base),
            plano["modelo"],
            textos,
            ident,
            alvo,
            bool(plano.get("tela_app")),
            assunto=assunto,
        )

    imagem = await asyncio.to_thread(desenhar)
    return await guardar_figura(imagem, marca_id, "arte")


async def produzir(
    session: AsyncSession,
    marca: Ficha | None,
    plano: dict,
    cena: str = "",
    forcar_ia: bool = False,
    reusar_base: bool = True,
) -> dict:
    """Faz a arte do plano: foto base (reaproveitada, original, editada ou criada) e o molde em feed e Story.
    Devolve o plano com base_url, feed_url, story_url e os passos que rodaram."""
    plano = dict(plano)
    marca_id = marca.id if marca and marca.id else 0
    passos: list[str] = []
    base_url = plano.get("base_url") or ""
    dados = None
    if reusar_base and not forcar_ia and base_url and await existe(base_url):
        try:
            dados = await bytes_da_url(base_url)
            passos.append("foto base reaproveitada (sem custo de imagem)")
        except Exception:
            dados = None
    if dados is None:
        dados, base_url, passo, editada = await _preparar_base(session, marca_id, plano, cena, forcar_ia)
        plano["base_url"] = base_url
        plano["editada"] = editada
        passos.append(passo)
    plano["feed_url"], plano["story_url"] = await _montar(session, marca, plano, dados)
    passos.append(f"molde {plano['modelo']} montado em feed e Story")
    plano["passos"] = passos
    log.info("arte marca=%s: %s", marca_id, " | ".join(passos))
    return plano


async def refazer(session: AsyncSession, marca: Ficha | None, plano: dict, formato: str = "feed") -> dict:
    """Só o molde de novo (texto ou modelo mudou), com a mesma foto base: sem custo de IA."""
    base_url = plano.get("base_url") or ""
    if not (base_url and await existe(base_url)):
        feed = plano.get("feed_url") or ""
        if feed and await existe(feed):
            plano = {**plano, "base_url": feed, "editada": False}
            base_url = feed
    if not (base_url and await existe(base_url)):
        marca_id = marca.id if marca and marca.id else 0
        foto = await _foto_da_marca(session, plano.get("foto_base_id"), marca_id)
        if foto is None:
            raise RuntimeError("Esta arte ainda não tem foto. Use Criar foto com IA ou escolha uma foto da memória.")
        plano = {**plano, "base_url": url_memoria(foto.id or 0), "editada": False}
    plano = await produzir(session, marca, plano, reusar_base=True)
    if formato == "facebook":
        dados = await bytes_da_url(plano["base_url"])
        plano["feed_url"] = await desenhar_um(session, marca, plano, dados, "facebook")
        plano["story_url"] = ""
    return plano
