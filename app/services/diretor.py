"""Diretor de arte: confere o plano da IA, edita a foto real da marca (ou cria uma) e monta a peça com o molde."""

import asyncio
import hashlib
import json
import logging
import random
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
from app.services.memoria import ContextoMemoria, e_captura, limpar_gancho
from app.services.openai_campanha import com_moderacao, fala_de_alcool

log = logging.getLogger("uvicorn.error")

NENHUMA = "nenhuma"
CAMPOS_TEXTO = ("titulo_arte", "subtitulo_arte", "selo", "chamada", "antes")
LIMITES = {
    "titulo_arte": (7, 60),
    "subtitulo_arte": (10, 90),
    "selo": (3, 18),
    "chamada": (5, 32),
    "antes": (10, 90),
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
    "passos",
)

REGRAS_ARTE = """

plano_arte: você também é o diretor de arte do post, com liberdade criativa: escolha o estilo e as cores pelo assunto e pelo clima do post, não por uma regra fixa. A foto da memória é material criativo: pode sair como está, ou servir de base para a IA de imagem melhorar (fundo, luz, cenário) mantendo o assunto real idêntico.
- ideia: 1 frase com a ideia criativa da peça.
- foto_base_id: id de uma foto da memória com papel "pode postar como está", "base para editar" ou "a IA decide" que mostra o assunto do post. Nunca uma foto de estilo. Se nenhuma combina, null (aí a foto é criada do zero).
- fotos_estilo_ids: até 2 ids de FOTOS DE ESTILO (ou de fotos do lugar, ou "a IA decide") que inspiram luz, cores e enquadramento. Pode ser [].
- edicao_foto: instrução curta para a IA de imagem, dizendo o que mudar na foto base mantendo o assunto real idêntico (ex.: "fundo limpo e claro, luz suave de fim de tarde, mais profundidade"). Escreva "nenhuma" se a foto for "pode postar como está", se for captura de tela, ou se não tiver foto base e a cena bastar. Em foto "a IA decide", escreva "nenhuma" se ela já está boa para postar; senão diga o que melhorar.
- modelo (o estilo da arte), um destes. Qualquer estilo serve para qualquer assunto; escolha o que conversa com o clima do post:
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
  story_chamada: foto cheia, título no alto e chamada grande embaixo (convite direto, "Responda QUERO").
- paleta, uma destas: marca (as cores da marca), urgencia (amarelo e preto), quente (laranja e marrom: comida, café, calor), fresca (verde-limão: fresco, bebida gelada, natural), fria (azul: tecnologia, limpeza, saúde), elegante (dourado e preto), doce (rosa: doces, beleza, delicadeza), natural (verde-sálvia: saúde, plantas), vibrante (magenta e roxo: festa, noite), neutra (branco e grafite: sóbrio), vermelha (vermelho forte). As cores da marca são o ponto de partida, mas troque quando o assunto pedir. Promoção não é sinônimo de vermelho nem de faixa: pense no que o post quer transmitir.
- posicao_texto: "auto" (a arte põe o texto na parte da foto sem o produto); "topo" ou "base" só se a foto pedir.
- titulo_arte: de 2 a 6 palavras, forte, para ler de longe. Sem emoji, sem hashtag.
- subtitulo_arte: até 8 palavras, ou "".
- selo: palavra curta para o selo ("Hoje", "Novo", "Sábado") ou um preço SÓ se o preço está escrito na memória. Senão "".
- chamada: chamada para ação de até 4 palavras ("Peça já", "Saiba mais", "Chame no direct", "Responda QUERO").
- antes: só no modelo antes_depois, a dor do cliente antes, até 8 palavras. Nos outros modelos, "".
- cor: quando a paleta é "marca", qual cor da marca vai na ênfase: "destaque" ou "principal".
- aviso: "" ou uma frase curta explicando um ajuste que você fez no pedido (ex.: por causa do CONAR).
Regras do plano_arte:
- Use só fatos da memória e do pedido. Não invente preço, desconto, produto, dia nem horário.
- Oferta e promoção só se estiverem em "Promoções fixas" ou se o pedido pedir com todas as letras.
- Captura de tela: modelo tela_app ou antes_depois, edicao_foto "nenhuma".
- Varie: não repita a combinação de modelo e paleta das últimas artes da marca, nem o ângulo."""

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
COMPOSICAO = {
    "faixa": "Keep the top third of the frame calm and uncluttered (it will hold a text banner); the main subject sits in the lower two thirds.",
    "story_chamada": "Keep the top third and the bottom fifth calm and uncluttered (they will hold text); the main subject sits in the middle.",
    "selo": "Keep the top-right corner and the bottom quarter calm and uncluttered (they will hold a badge and text); the main subject slightly left of center.",
    "destaque": "Keep the bottom third calm and darker (it will hold text); the main subject sits in the upper two thirds.",
    "alerta": "Keep the top third calm and uncluttered (it will hold a warning panel); the main subject sits in the lower two thirds.",
    "etiqueta": "Keep the bottom third calm (it will hold a price tag and text); the main subject sits in the upper two thirds.",
    "carimbo": "Keep the top third calm (it will hold a stamp); the main subject sits in the lower two thirds.",
    "neon": "Moody low light; keep the bottom third dark and calm (it will hold a neon sign); the main subject sits in the upper part.",
    "poster": "Keep the top 40% calm and simple (it will hold a big color block with huge type); the main subject sits low in the frame.",
    "minimal": "Clean, airy composition; keep the bottom quarter calm (it will hold a slim text bar).",
}
PALETAS_PLANO = ("marca", *molde.PALETAS)
ESTILOS_VARIAVEIS = ("faixa", "selo", "destaque", "alerta", "etiqueta", "carimbo", "neon", "poster", "minimal", "story_chamada")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
PALETAS_DO_ASSUNTO = (
    (re.compile(r"\b(app|sistema|aplicativo|tecnologia|celular|digital|online|site)\b"), "fria"),
    (re.compile(r"\b(festa|noite|show|musica|balada|happy hour|dj|samba|pagode)\b"), "vibrante"),
    (re.compile(r"\b(caipirinha|suco|gelad[oa]|drink|limao|verao|natural|salada|verde)\b"), "fresca"),
    (re.compile(r"\b(doce|bolo|confeitaria|beleza|salao|unha|cabelo|flor)\b"), "doce"),
    (re.compile(r"\b(cafe|pizza|lanche|hamburguer|churrasco|comida|prato|porcao|pao|padaria|feijoada|almoco)\b"), "quente"),
    (re.compile(r"\b(ultim[oa]s?|so hoje|acaba|corre|urgente|aviso|atencao)\b"), "urgencia"),
)


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
        if plano["modelo"] not in ("tela_app", "antes_depois"):
            plano["modelo"] = "tela_app"
    if base and contexto.papeis.get(base) == "postar":
        plano["edicao_foto"] = NENHUMA
    if _nenhuma(plano["edicao_foto"]):
        plano["edicao_foto"] = NENHUMA
    _sem_preco_inventado(plano, f"{contexto.fatos} {pedido}")
    legenda = _conar(plano, pedido, legenda)
    _coerencia(plano)
    return plano, legenda


def tem_cores(marca: Ficha | None) -> bool:
    return bool(marca and ((marca.cor_principal or "").strip() or (marca.cor_destaque or "").strip()))


def _paleta_do_assunto(plano: dict) -> str:
    texto = _sem_acento(" ".join(str(plano.get(campo) or "") for campo in ("ideia", *CAMPOS_TEXTO))).lower()
    for padrao, paleta in PALETAS_DO_ASSUNTO:
        if padrao.search(texto):
            return paleta
    return "neutra"


def variar(plano: dict, recentes: list[tuple[str, str]], marca_tem_cores: bool = True) -> dict:
    """Liberdade com variedade: sem cores da marca, 'marca' vira uma paleta do assunto; e a dupla
    modelo + paleta não repete as últimas artes da marca (nem o mesmo modelo 3 vezes seguidas)."""
    plano = dict(plano)
    if plano.get("modelo") in ("tela_app", "antes_depois", "livre"):
        return plano
    if plano.get("paleta") == "marca" and not marca_tem_cores:
        plano["paleta"] = _paleta_do_assunto(plano)
    sorteio = random.Random(f"{plano.get('titulo_arte')}|{plano.get('ideia')}")
    usados = set(recentes)
    modelos = [modelo for modelo, _paleta in recentes]
    if modelos.count(plano["modelo"]) >= 3:
        livres = [modelo for modelo in ESTILOS_VARIAVEIS if modelo not in modelos and (modelo != "selo" or plano.get("selo"))]
        if livres:
            plano["modelo"] = sorteio.choice(livres)
    if (plano["modelo"], plano.get("paleta")) in usados and plano.get("paleta") != "livre":
        outras = [paleta for paleta in PALETAS_PLANO if (plano["modelo"], paleta) not in usados and paleta != plano.get("paleta")]
        if not marca_tem_cores and "marca" in outras:
            outras.remove("marca")
        if outras:
            preferida = _paleta_do_assunto(plano)
            plano["paleta"] = preferida if preferida in outras else sorteio.choice(outras)
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


def prompt_edicao(plano: dict, com_estilo: bool) -> str:
    edicao = plano.get("edicao_foto") or NENHUMA
    if edicao == NENHUMA:
        edicao = "a cleaner, more attractive background and professional lighting"
    partes = [
        "Edit this real photo of the business into a premium Instagram photo.",
        "KEEP THE REAL SUBJECT IDENTICAL: same shape, size, colors, materials, details, packaging, label and branding. "
        "Do not replace, redraw, restyle or add another product or object.",
        f"What to change: {edicao}.",
        "Photographic realism, attractive professional lighting, sharp focus on the main subject, natural depth of field.",
        COMPOSICAO.get(plano.get("modelo", ""), "Main subject centered with breathing room around it."),
        "No text, no letters, no numbers, no logos, no watermark, no frame. Square image.",
    ]
    if com_estilo:
        partes.append(
            "The second image is ONLY a style reference (lighting, color palette, mood, framing). Do not copy its objects."
        )
    if plano.get("alcool"):
        partes.append("No people drinking, no glass raised to a mouth, no minors.")
    return " ".join(partes)


def prompt_criacao(plano: dict, cena: str, com_estilo: bool) -> str:
    assunto = (cena or plano.get("ideia") or plano.get("titulo_arte") or "the business").strip()[:600]
    partes = [
        f"Realistic photograph for an Instagram post: {assunto}.",
        "Attractive professional lighting, sharp focus on the main subject, natural depth of field.",
        COMPOSICAO.get(plano.get("modelo", ""), "Main subject centered with breathing room around it."),
        "No text, no letters, no numbers, no logos, no watermark, no screens with words. Square image.",
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
    if forcar_ia:
        return False
    return foto.papel == "postar" or (plano.get("edicao_foto") or NENHUMA) == NENHUMA


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
    session: AsyncSession, marca_id: int, plano: dict, cena: str, forcar_ia: bool
) -> tuple[bytes, str, str, bool]:
    """A foto base da arte: (bytes, url, o que foi feito, se a IA mexeu)."""
    from app.services.midia import editar_com_ia, gerar_com_ia

    foto = await _foto_da_marca(session, plano.get("foto_base_id"), marca_id)
    estilos = await _fotos_estilo(session, plano, marca_id)
    if foto is not None:
        if _sem_edicao(foto, plano, forcar_ia):
            return foto.arquivo, url_memoria(foto.id or 0), "foto da memória usada como está (sem custo de imagem)", False
        try:
            dados, usadas = await editar_com_ia(prompt_edicao(plano, bool(estilos)), [foto.arquivo, *estilos[:1]])
        except Exception as erro:
            if forcar_ia:
                raise
            motivo = str(erro).replace("\n", " ")[:120]
            return foto.arquivo, url_memoria(foto.id or 0), f"a edição com IA falhou ({motivo}); usei a foto original", False
        figura = await asyncio.to_thread(arte.abrir, dados)
        url = await guardar_figura(figura, marca_id, "arte-base")
        return dados, url, f"foto da memória editada com IA ({usadas} foto(s) de entrada)", True
    if estilos:
        dados, _usadas = await editar_com_ia(prompt_criacao(plano, cena, True), estilos[:1])
        passo = "foto criada com IA, inspirada numa foto de estilo"
    else:
        dados = await gerar_com_ia(prompt_criacao(plano, cena, False))
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
    )
    chave = f"{ident.principal}|{ident.destaque}|{ident.assinatura}|{logo.atualizado_em.isoformat() if logo else ''}"
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


async def refazer(session: AsyncSession, marca: Ficha | None, plano: dict) -> dict:
    """Só o molde de novo (texto ou modelo mudou), com a mesma foto base: sem custo de IA."""
    base_url = plano.get("base_url") or ""
    if not (base_url and await existe(base_url)):
        marca_id = marca.id if marca and marca.id else 0
        foto = await _foto_da_marca(session, plano.get("foto_base_id"), marca_id)
        if foto is None:
            raise RuntimeError("Esta arte ainda não tem foto. Use Criar foto com IA ou escolha uma foto da memória.")
        plano = {**plano, "base_url": url_memoria(foto.id or 0), "editada": False}
    return await produzir(session, marca, plano, reusar_base=True)
