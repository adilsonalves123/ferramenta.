"""Rascunho do post na prévia: cada mensagem do chat AJUSTA o rascunho (foto, arte, legenda ou formato)
em vez de criar outro post. Guarda as últimas versões para a pessoa voltar atrás."""

import asyncio
import json
import logging
import random
import re
import unicodedata

from PIL import Image, ImageChops, ImageFilter, ImageOps
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.models import Agendamento, Ficha, MemoriaFoto, Rascunho, agora
from app.schemas import IntencaoAjuste, ItemRascunho, VersaoRascunho
from app.services import arte, diretor
from app.services.imagens import bytes_da_url, existe, guardar_figura, id_da_url, url_memoria
from app.services.memoria import NOMES_TIPOS, artes_recentes, e_captura, fechar_legenda, listar_fotos, montar_contexto, tipo_efetivo
from app.services.openai_campanha import cliente_openai, com_moderacao, fala_de_alcool

log = logging.getLogger("uvicorn.error")

MAX_VERSOES = 15
FORMATOS = {"feed", "story", "ambos", "facebook"}
# Fração da foto que pode mudar ao pôr um print na tela do celular; acima disso a IA refez a cena.
LIMITE_MUDANCA_TELA = 0.45
NOMES_FORMATOS = {"feed": "Feed", "story": "Story", "ambos": "Feed e Story", "facebook": "Facebook"}
ROTULOS_ARTE = {
    "titulo_arte": "o título",
    "subtitulo_arte": "o subtítulo",
    "selo": "o selo",
    "chamada": "a chamada",
    "antes": "o antes",
}

SISTEMA_AJUSTE = """Você é o assistente de um app que cria posts de Instagram para pequenos negócios, em português do Brasil.
Já existe um RASCUNHO na prévia: uma foto com a arte por cima (título, subtítulo, selo, chamada) e a legenda.
A pessoa mandou uma mensagem no chat. Entenda o que ela quer MUDAR NESTE RASCUNHO.
Regra principal: ajuste o rascunho atual. Nunca recomece do zero, a não ser que ela peça com todas as letras um post novo, outro post ou outro assunto.

tipos (pode ter mais de um):
- imagem: mudar a foto em si (colocar um print ou captura de tela na tela do celular ou do computador, trocar o fundo, tirar ou pôr um objeto, mudar a luz ou as cores da foto).
- arte: mudar os textos ou o desenho por cima da foto (título, subtítulo, selo, chamada, antes, modelo da arte, cor).
- legenda: reescrever o texto do post (a legenda).
- formato: trocar entre feed, story ou os dois.
- novo: SÓ se a pessoa pedir claramente um post novo, outro post ou outro assunto ("faz outro post sobre...", "esquece esse, cria um de..."). Pedido de ajuste nunca é novo.
instrucao_imagem: se tipos tem imagem, a instrução em INGLÊS para a IA de imagem, curta e literal, só com a mudança pedida. A foto atual é "image 1" e as fotos de fotos_ids são "image 2", "image 3"... na ordem; nunca escreva o id (ex.: "Put image 2, the app screenshot, on the phone screen"). Senão "".
tela_no_aparelho: true se o pedido é colocar um print, captura de tela, screenshot ou tela do app dentro da tela de um celular, tablet ou computador que aparece na foto.
fotos_ids: ids das FOTOS DA MEMÓRIA que a pessoa citou pelo nome ou pela descrição (ex.: "o print do app", "a tela de fiado", "a foto da fachada") e as que ela anexou. Use só ids da lista. [] se nenhuma.
arte: só os campos que ela pediu para mudar, com o texto novo exato; null nos outros. titulo_arte até 6 palavras, selo até 3, chamada até 4.
- modelo (o estilo da arte): faixa, selo, destaque, antes_depois, tela_app (imagem em destaque dentro de uma moldura de celular), story_chamada, alerta (painel de aviso com listras amarelas e pretas), etiqueta (etiqueta de preço), carimbo, neon (letreiro de neon), poster (letras enormes num bloco de cor), minimal (barra limpa e elegante) ou livre (só a imagem, sem textos); null se ela não pediu.
- paleta, se ela pedir um clima de cor: marca (as cores da marca), urgencia (amarelo e preto), quente, fresca, fria, elegante (dourado e preto), doce (rosa), natural, vibrante (magenta e roxo), neutra (branco e grafite), vermelha; null se ela não pediu.
- cor_livre: código #RRGGBB se ela pedir uma cor específica ("faz em amarelo" = "#FFD60A", "azul" = "#2563EB", "verde" = "#16A34A", "laranja" = "#F97316", "roxo" = "#7C3AED", "rosa" = "#EC4899", "preto" = "#111111"); null se não.
- posicao_texto: "topo" ou "base" se ela pedir o texto em cima ou embaixo, "auto" para deixar a arte decidir; null se não pediu.
- cor: principal ou destaque (qual cor da marca vai na ênfase); null se ela não pediu.
legenda_nova: se tipos tem legenda, a legenda inteira reescrita como ela pediu, no contrato da marca: item concreto na primeira linha, a frase da chamada do canal e as hashtags fixas. Mantenha os fatos e o site da legenda atual. Senão "". Não invente preço, produto, horário nem promoção.
formato: feed, story ou ambos se tipos tem formato; senão null.
pergunta: uma pergunta curta SÓ se não der para saber o que fazer (ex.: ela fala "o print" e a memória tem vários prints e nenhum foi anexado: "Qual print? A tela de fiado ou a de vendas?"). Senão "".
resumo_mudanca: em primeira pessoa, curto, o que você vai mudar (ex.: "Coloquei o print da tela de fiado no celular"). Sem ponto final."""


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c))


def _ler_versoes(registro: Rascunho) -> list[dict]:
    try:
        versoes = json.loads(registro.versoes or "[]")
    except ValueError:
        return []
    return [versao for versao in versoes if isinstance(versao, dict)] if isinstance(versoes, list) else []


def plano_de(registro: Rascunho) -> dict | None:
    return diretor.ler_plano(registro.plano_arte)


def _primeira_linha(legenda: str) -> str:
    return next((linha.strip() for linha in (legenda or "").splitlines() if linha.strip()), "")[:300]


def _gravar_estado(registro: Rascunho, legenda: str, plano: dict | None, formato: str | None = None) -> None:
    registro.legenda = (legenda or "").strip()
    registro.titulo = _primeira_linha(registro.legenda)
    registro.plano_arte = diretor.gravar_plano(plano)
    if formato in FORMATOS:
        registro.formato = formato
    plano = plano or {}
    registro.imagem_base_id = id_da_url(plano.get("base_url") or "")
    registro.imagem_final_feed_id = id_da_url(plano.get("feed_url") or "")
    registro.imagem_final_story_id = id_da_url(plano.get("story_url") or "")
    registro.atualizado_em = agora()


def _nova_versao(registro: Rascunho, descricao: str) -> int:
    versoes = _ler_versoes(registro)
    numero = max((int(versao.get("n") or 0) for versao in versoes), default=0) + 1
    versoes.append(
        {
            "n": numero,
            "criado_em": agora().isoformat(),
            "descricao_mudanca": (descricao or "").strip()[:200],
            "imagem_base_id": registro.imagem_base_id,
            "imagem_final_feed_id": registro.imagem_final_feed_id,
            "imagem_final_story_id": registro.imagem_final_story_id,
            "legenda": registro.legenda,
            "formato": registro.formato,
            "plano_arte": registro.plano_arte,
        }
    )
    registro.versoes = json.dumps(versoes[-MAX_VERSOES:], ensure_ascii=False)
    return numero


def para_item(registro: Rascunho) -> ItemRascunho:
    plano = plano_de(registro)
    versoes = []
    for versao in _ler_versoes(registro):
        plano_versao = diretor.ler_plano(versao.get("plano_arte")) or {}
        versoes.append(
            VersaoRascunho(
                n=int(versao.get("n") or 0),
                criado_em=str(versao.get("criado_em") or ""),
                descricao_mudanca=str(versao.get("descricao_mudanca") or ""),
                feed_url=str(plano_versao.get("feed_url") or ""),
                story_url=str(plano_versao.get("story_url") or ""),
            )
        )
    return ItemRascunho(
        id=registro.id or 0,
        marca_id=registro.marca_id,
        legenda=registro.legenda or "",
        titulo=registro.titulo or "",
        plano_arte=diretor.resumo_plano(plano),
        formato=registro.formato or "feed",
        base_url=(plano or {}).get("base_url") or "",
        feed_url=(plano or {}).get("feed_url") or "",
        story_url=(plano or {}).get("story_url") or "",
        cena=registro.cena or "",
        gancho=registro.gancho or "",
        agendamento_id=registro.agendamento_id,
        versoes=versoes,
        versao_atual=versoes[-1].n if versoes else 0,
    )


async def atual(session: AsyncSession, marca_id: int) -> Rascunho | None:
    resultado = await session.exec(
        select(Rascunho)
        .where(Rascunho.marca_id == marca_id, Rascunho.status == "aberto")
        .order_by(col(Rascunho.atualizado_em).desc(), col(Rascunho.id).desc())
    )
    return resultado.first()


async def fechar_abertos(session: AsyncSession, marca_id: int) -> None:
    """O rascunho aberto sai do estúdio. Se já tem arte, fica na prateleira como guardado."""
    resultado = await session.exec(
        select(Rascunho).where(Rascunho.marca_id == marca_id, Rascunho.status == "aberto")
    )
    for registro in resultado.all():
        plano = plano_de(registro)
        tem_arte = bool(plano and (plano.get("feed_url") or plano.get("story_url")))
        registro.status = "guardado" if tem_arte else "fechado"
        registro.atualizado_em = agora()


async def criar(
    session: AsyncSession,
    marca: Ficha | None,
    legenda: str,
    plano_bruto: dict | None,
    formato: str = "feed",
    cena: str = "",
    gancho: str = "",
    descricao: str = "",
    agendamento_id: int | None = None,
) -> Rascunho:
    """Novo rascunho da marca (o anterior fica fechado). O plano volta a ser conferido aqui."""
    marca_id = marca.id if marca and marca.id else 0
    contexto = await montar_contexto(session, marca)
    plano = diretor.do_cliente(plano_bruto, contexto) if plano_bruto else None
    if plano and plano.get("base_url") and not plano.get("feed_url"):
        try:
            plano = await diretor.refazer(session, marca, plano, formato)
        except Exception as erro:
            log.warning("rascunho: não montei a arte da foto base (%s)", str(erro)[:160])
    await fechar_abertos(session, marca_id)
    registro = Rascunho(
        marca_id=marca_id,
        cena=(cena or "").strip()[:800],
        gancho=(gancho or "").strip()[:80],
        agendamento_id=agendamento_id,
    )
    _gravar_estado(registro, legenda, plano, formato if formato in FORMATOS else "feed")
    _nova_versao(registro, descricao or "Primeira versão")
    session.add(registro)
    await session.commit()
    await session.refresh(registro)
    return registro


async def salvar(
    session: AsyncSession,
    registro: Rascunho,
    marca: Ficha | None,
    legenda: str | None,
    plano_bruto: dict | None,
    formato: str | None,
    descricao: str = "",
    nova_versao: bool = False,
) -> Rascunho:
    """Guarda o que mudou à mão na prévia (legenda digitada, Refazer arte, Trocar foto, Criar foto com IA)."""
    plano = plano_de(registro)
    if plano_bruto is not None:
        plano = diretor.do_cliente(plano_bruto, await montar_contexto(session, marca)) or plano
    _gravar_estado(registro, registro.legenda if legenda is None else legenda, plano, formato)
    if nova_versao:
        _nova_versao(registro, descricao or "Mudança na prévia")
    await session.commit()
    await session.refresh(registro)
    return registro


async def restaurar(session: AsyncSession, registro: Rascunho, numero: int) -> Rascunho:
    versao = next((item for item in _ler_versoes(registro) if int(item.get("n") or 0) == numero), None)
    if versao is None:
        raise ValueError("Essa versão não está mais guardada.")
    plano = diretor.ler_plano(versao.get("plano_arte"))
    _gravar_estado(registro, str(versao.get("legenda") or ""), plano, str(versao.get("formato") or registro.formato))
    _nova_versao(registro, f"Voltei para a versão {numero}")
    await session.commit()
    await session.refresh(registro)
    return registro


async def abrir_da_fila(session: AsyncSession, registro: Agendamento, marca: Ficha | None) -> Rascunho:
    from app.services.publicar import texto_do_post

    plano = diretor.ler_plano(registro.plano_arte)
    if plano is None:
        raise ValueError("Este post da fila não tem arte para ajustar.")
    if registro.imagem_url and not plano.get("feed_url"):
        plano["feed_url"] = registro.imagem_url
    return await criar(
        session,
        marca,
        texto_do_post(registro.titulo or "", registro.legenda or ""),
        plano,
        registro.formato or "feed",
        registro.cena or "",
        registro.gancho or "",
        "Post da fila aberto para ajustar",
        registro.id,
    )


async def salvar_na_fila(session: AsyncSession, rascunho: Rascunho) -> Agendamento:
    """Leva o rascunho de volta para o post da fila de onde ele veio."""
    registro = await session.get(Agendamento, rascunho.agendamento_id or 0)
    if registro is None or registro.marca_id != rascunho.marca_id:
        raise LookupError("Esse post não está mais na fila.")
    if registro.status not in {"na_fila", "falta_imagem"}:
        raise ValueError("Esse post não está mais na fila.")
    plano = plano_de(rascunho)
    legenda = (rascunho.legenda or "").strip()
    if legenda:
        registro.titulo = _primeira_linha(legenda)
        registro.legenda = legenda
    registro.formato = rascunho.formato if rascunho.formato in FORMATOS else registro.formato
    if plano is not None:
        registro.plano_arte = diretor.gravar_plano(plano)
        registro.foto_id = plano.get("foto_base_id")
        if plano.get("feed_url"):
            registro.imagem_url = str(plano["feed_url"])[:500]
            registro.detalhe = ""
            if registro.status == "falta_imagem":
                registro.status = "na_fila"
    await session.commit()
    await session.refresh(registro)
    return registro


def ids_em_uso(registros: list[Rascunho]) -> set[int]:
    """Fotos que os rascunhos abertos (e as versões deles) ainda mostram."""
    usados: set[int] = set()
    for registro in registros:
        planos = [registro.plano_arte, *(versao.get("plano_arte") for versao in _ler_versoes(registro))]
        for bruto in planos:
            plano = diretor.ler_plano(bruto) or {}
            for chave in ("base_url", "feed_url", "story_url"):
                ident = id_da_url(str(plano.get(chave) or ""))
                if ident is not None:
                    usados.add(ident)
    return usados


# ---------- leitura do pedido ----------


def _linha_foto(foto) -> str:
    tipo = tipo_efetivo(foto.tipo, foto.descricao, foto.tags)
    descricao = re.sub(r"\s+", " ", foto.descricao or "").strip()[:160] or "(sem descrição)"
    tags = f" (tags: {foto.tags[:80]})" if (foto.tags or "").strip() else ""
    return f"[{foto.id}] {NOMES_TIPOS.get(tipo, tipo)}: {descricao}{tags}"


def _descricao_base(plano: dict, fotos: dict[int, object]) -> str:
    base = plano.get("foto_base_id")
    if plano.get("editada") or (plano.get("base_url") and id_da_url(plano.get("base_url") or "") is not None):
        origem = "foto editada ou criada pela IA"
        if base in fotos:
            origem += " a partir de " + _linha_foto(fotos[base])
        return origem
    if base in fotos:
        return "foto da memória " + _linha_foto(fotos[base])
    return "sem foto ainda"


async def ler_intencao(
    mensagem: str,
    registro: Rascunho,
    plano: dict,
    fotos: dict[int, object],
    anexos: list[int],
    fatos: str,
) -> IntencaoAjuste:
    partes = [
        "RASCUNHO ATUAL",
        f"Formato: {NOMES_FORMATOS.get(registro.formato or 'feed', 'Feed')}",
        f"Foto: {_descricao_base(plano, fotos)}",
        (
            f"Arte: modelo {plano.get('modelo', '')} · título \"{plano.get('titulo_arte', '')}\" · "
            f"subtítulo \"{plano.get('subtitulo_arte', '')}\" · selo \"{plano.get('selo', '')}\" · "
            f"chamada \"{plano.get('chamada', '')}\""
        ),
        "Legenda:\n" + (registro.legenda or "(vazia)")[:2500],
    ]
    if fatos.strip():
        partes.append("FATOS DA MARCA (única fonte para a legenda):\n" + fatos.strip()[:2500])
    if fotos:
        partes.append("FOTOS DA MEMÓRIA (id, tipo: descrição):\n" + "\n".join(_linha_foto(foto) for foto in fotos.values()))
    if anexos:
        partes.append("FOTOS ANEXADAS PELA PESSOA NESTA MENSAGEM: " + ", ".join(f"[{ident}]" for ident in anexos))
    partes.append("MENSAGEM DA PESSOA:\n" + mensagem.strip()[:2000])
    resposta = await cliente_openai().with_options(timeout=60).chat.completions.parse(
        model=get_settings().openai_model,
        messages=[
            {"role": "system", "content": SISTEMA_AJUSTE},
            {"role": "user", "content": "\n\n".join(partes)},
        ],
        response_format=IntencaoAjuste,
    )
    resultado = resposta.choices[0].message
    if resultado.refusal or resultado.parsed is None:
        raise ValueError("A IA não entendeu o pedido agora. Tente dizer de outro jeito.")
    return resultado.parsed


PALAVRAS_PRINT = re.compile(r"\b(print|prints|captura|screenshot|tela do app|tela de|telinha)\b", re.IGNORECASE)


def _conferir_intencao(intencao: IntencaoAjuste, mensagem: str, fotos: dict[int, object], anexos: list[int]) -> dict:
    """Deixa a leitura da IA segura: ids da marca, anexos sempre entram e print sem foto vira pergunta."""
    tipos = list(dict.fromkeys(intencao.tipos))
    ids = [ident for ident in anexos if ident in fotos]
    ids += [ident for ident in intencao.fotos_ids if ident in fotos and ident not in ids]
    pergunta = intencao.pergunta.strip()
    tela = bool(intencao.tela_no_aparelho)
    if tela and "imagem" not in tipos:
        tipos.append("imagem")
    capturas = [ident for ident in ids if e_captura(fotos[ident])]
    if tela and not capturas:
        todas = [ident for ident, foto in fotos.items() if e_captura(foto)]
        if len(todas) == 1:
            ids.insert(0, todas[0])
        elif len(todas) > 1 and not pergunta:
            nomes = [re.sub(r"\s+", " ", fotos[ident].descricao or f"print {ident}").strip()[:60] for ident in todas[:3]]
            pergunta = "Qual print eu coloco no celular? " + " ou ".join(f"«{nome}»" for nome in nomes) + "? Você também pode escolher em Da memória."
        elif not todas and not pergunta:
            pergunta = "Não achei nenhum print na Memória da marca. Mande o print em Minha marca > Fotos da marca, ou escolha uma foto em Da memória."
    elif not tela and "imagem" in tipos and not ids and PALAVRAS_PRINT.search(_sem_acento(mensagem)):
        todas = [ident for ident, foto in fotos.items() if e_captura(foto)]
        if len(todas) == 1:
            ids.append(todas[0])
    if pergunta and tela and any(e_captura(fotos[ident]) for ident in ids):
        pergunta = ""
    return {"tipos": tipos, "ids": ids, "tela": tela, "pergunta": pergunta}


# ---------- foto ----------


def quanto_mudou(antes: bytes, depois: bytes, lado: int = 48) -> float:
    """Fração (0 a 1) da foto que mudou de verdade entre as duas versões (comparação em baixa resolução)."""
    a = arte.abrir(antes).convert("RGB")
    b = arte.abrir(depois).convert("RGB")
    altura = max(8, round(lado * a.height / a.width))
    a = a.resize((lado, altura), Image.LANCZOS).filter(ImageFilter.GaussianBlur(1))
    b = b.resize((lado, altura), Image.LANCZOS).filter(ImageFilter.GaussianBlur(1))
    valores = list(ImageChops.difference(a, b).convert("L").getdata())
    return sum(1 for valor in valores if valor > 40) / max(1, len(valores))


def prompt_tela(instrucao: str, alcool: bool) -> str:
    partes = [
        "Image 1 is the photo to edit. Image 2 is an app screenshot.",
        "Place image 2 exactly on the display of the phone (or device screen) that appears in image 1, filling the whole "
        "screen area, matching its perspective, angle, rounded corners and the glass reflections.",
        "Do NOT alter the screenshot content: keep its layout, text, numbers, colors and icons exactly as in image 2, "
        "only warped to fit the screen.",
        "Keep EVERYTHING else in image 1 identical: same composition, framing, camera angle, people, hands, objects, "
        "background, colors and lighting. Do not add or remove anything. No new text, logos or watermark. "
        "Change ONLY the device screen.",
    ]
    if instrucao.strip():
        partes.append(f"Request: {instrucao.strip()[:300]}")
    if alcool:
        partes.append("No people drinking, no glass raised to a mouth, no minors.")
    return " ".join(partes)


def prompt_ajuste(instrucao: str, referencias: int, alcool: bool) -> str:
    partes = [
        f"Edit image 1. Change ONLY this: {instrucao.strip()[:400] or 'make the photo slightly more attractive'}.",
        "Keep everything else identical: same composition, framing, camera angle, people, faces, hands, objects, "
        "background, colors and lighting. Do not restyle or redraw the photo. No text, letters, logos or watermark.",
    ]
    if referencias:
        partes.append(
            "The other images are real photos of the business given for this change: use them as the exact source of what "
            "must appear, without redrawing them, and copy nothing else from them."
        )
    if alcool:
        partes.append("No people drinking, no glass raised to a mouth, no minors.")
    return " ".join(partes)


def _quadro_do_print(plano: dict, foto) -> dict:
    """Plano B sem IA: a arte 'Imagem em destaque' com o print dentro da moldura de celular."""
    return {
        **plano,
        "foto_base_id": foto.id,
        "fotos_estilo_ids": [],
        "base_url": url_memoria(foto.id or 0),
        "feed_url": "",
        "story_url": "",
        "modelo": "tela_app",
        "tela_app": True,
        "edicao_foto": diretor.NENHUMA,
        "editada": False,
    }


def _quota(erro: Exception) -> bool:
    texto = str(erro)
    return "insufficient_quota" in texto or "credit_balance" in texto


async def _base_atual(session: AsyncSession, plano: dict, marca_id: int) -> bytes | None:
    base_url = plano.get("base_url") or ""
    if base_url and await existe(base_url):
        try:
            return await bytes_da_url(base_url)
        except Exception:
            pass
    foto = await session.get(MemoriaFoto, int(plano.get("foto_base_id") or 0)) if plano.get("foto_base_id") else None
    if foto is not None and foto.marca_id == marca_id:
        plano["base_url"] = url_memoria(foto.id or 0)
        return foto.arquivo
    return None


async def _guardar_base(dados: bytes, marca_id: int) -> str:
    figura = await asyncio.to_thread(arte.abrir, dados)
    return await guardar_figura(figura, marca_id, "ajuste-base")


async def editar_imagem(
    session: AsyncSession,
    marca: Ficha | None,
    plano: dict,
    instrucao: str,
    tela: bool,
    referencias: list[MemoriaFoto],
    cena: str = "",
    anexo: bytes | None = None,
) -> tuple[dict, str, bool]:
    """Muda só o que foi pedido na foto base atual. Devolve (plano, o que foi feito, se usou a IA de imagem)."""
    from app.services.midia import _modelo, editar_varias, tamanho_para

    marca_id = marca.id if marca and marca.id else 0
    plano = dict(plano)
    alcool = bool(plano.get("alcool")) or fala_de_alcool(instrucao)
    base = await _base_atual(session, plano, marca_id)
    capturas = [foto for foto in referencias if e_captura(foto)]
    print_bytes = capturas[0].arquivo if capturas else anexo
    if tela and print_bytes:
        print_foto = capturas[0] if capturas else None
        if base is None:
            if print_foto is None:
                return plano, "criada", False
            return _quadro_do_print(plano, print_foto), "quadro", False
        largura, altura = (await asyncio.to_thread(arte.abrir, base)).size
        try:
            dados = await editar_varias(
                prompt_tela(instrucao, alcool), [base, print_bytes], tamanho_para(largura, altura, _modelo())
            )
        except Exception as erro:
            if _quota(erro) or print_foto is None:
                raise
            log.warning("ajuste: edição do print falhou (%s); usei a moldura", str(erro)[:160])
            return _quadro_do_print(plano, print_foto), "quadro_falhou", True
        mudou = await asyncio.to_thread(quanto_mudou, base, dados)
        log.info("ajuste: print no celular mudou %.0f%% da foto", mudou * 100)
        if mudou > LIMITE_MUDANCA_TELA and print_foto is not None:
            return _quadro_do_print(plano, print_foto), "quadro_mudou", True
        plano.update(base_url=await _guardar_base(dados, marca_id), feed_url="", story_url="", editada=True)
        return plano, "tela", True
    if base is None:
        cena_nova = " ".join(parte for parte in (cena.strip(), instrucao.strip()) if parte)[:800]
        feito = await diretor.produzir(session, marca, plano, cena=cena_nova, forcar_ia=True, reusar_base=False)
        return feito, "criada", True
    largura, altura = (await asyncio.to_thread(arte.abrir, base)).size
    extras = [foto.arquivo for foto in referencias[:3]]
    if anexo:
        extras.append(anexo)
    dados = await editar_varias(
        prompt_ajuste(instrucao, len(extras), alcool), [base, *extras], tamanho_para(largura, altura, _modelo())
    )
    plano.update(base_url=await _guardar_base(dados, marca_id), feed_url="", story_url="", editada=True)
    return plano, "editada", True


_LUZES = (
    "Soft side light, natural and gentle.",
    "Warm late-day light, soft shadows.",
    "Even soft light, shallow background.",
)
_OUTRA_FOTO = re.compile(
    r"\b(de novo|outra vez|outra foto|outro angulo|cria outra|criar outra|faz outra|gera outra|"
    r"nao gostei|diferente|muda tudo|recomeca|cria de novo|fazer de novo)\b"
)
_PEDE_CRIAR = re.compile(r"\b(cria|criar|gera|gerar|faz|fazer)\b")
_AJUSTE_PEQUENO = re.compile(r"\b(fundo|luz|cor|cores|tira|remove|coloca|aproxima|afasta|escuro|claro|desfoca)\b")


def pede_outra_foto(mensagem: str) -> bool:
    """A pessoa pediu para criar de novo, não para um retoque."""
    simples = _sem_acento(mensagem).lower()
    if _OUTRA_FOTO.search(simples):
        return True
    return bool(_PEDE_CRIAR.search(simples) and not _AJUSTE_PEQUENO.search(simples))


def _foto_crua(plano: dict) -> bool:
    if plano.get("modelo") != "livre":
        return False
    return not any(str(plano.get(campo) or "").strip() for campo in ("titulo_arte", "subtitulo_arte", "selo", "chamada", "antes"))


def prompt_criar_imagem(
    descricao: str,
    referencias: int,
    alcool: bool,
    negocio: str = "",
    evitar: str = "",
    luz: str = "",
) -> str:
    """Foto limpa, sem letra. O enquadramento reserva as duas faixas em que a arte escreve depois."""
    if referencias:
        partes = [
            f"Use image 1{' and the other images' if referencias > 1 else ''} as the real material for this request: {descricao.strip()[:1200]}.",
            "Keep the real product, people and place from the images identical (same shape, colors, labels and details) "
            "unless the request asks to change them.",
        ]
    else:
        partes = [f"Create an image for an Instagram post of a small local business: {descricao.strip()[:1200]}."]
    if negocio.strip():
        partes.append(f"The business really does this: {negocio.strip()[:400]}. Photograph that, not a generic stock scene.")
    if evitar.strip():
        partes.append(
            f"Do not repeat this earlier scene: {evitar.strip()[:400]}. "
            "Change the moment, the angle and what is in the foreground."
        )
    partes.append(luz.strip() or random.choice(_LUZES))
    partes.append(diretor.frase_da_zona("dupla", preservar=bool(referencias)))
    partes.append("No text, letters, numbers, logos or watermark unless the request explicitly asks for them.")
    if alcool:
        partes.append("No people drinking, no glass raised to a mouth, no minors.")
    return " ".join(partes)


async def criar_imagem(
    session: AsyncSession,
    marca: Ficha | None,
    descricao: str,
    fotos_ids: list[int],
    anexo_url: str = "",
    formato: str = "feed",
) -> Rascunho:
    """Criar imagem: a IA cria a imagem pedida (ou usa as fotos escolhidas como base) e ela vira o rascunho."""
    from app.services.midia import _modelo, editar_varias, gerar_com_ia, tamanho_para

    marca_id = marca.id if marca and marca.id else 0
    referencias: list[bytes] = []
    if anexo_url.strip() and id_da_url(anexo_url) is not None:
        referencias.append(await bytes_da_url(anexo_url))
    for ident in fotos_ids[:4]:
        foto = await session.get(MemoriaFoto, ident)
        if foto is not None and foto.marca_id == marca_id:
            referencias.append(foto.arquivo)
    alcool = fala_de_alcool(descricao)
    from app.services.memoria import ler_fatos

    fatos = ler_fatos(marca)
    negocio = ". ".join(fatos[chave] for chave in ("oferta", "itens", "diferenciais") if fatos.get(chave))
    recentes = await session.exec(
        select(Rascunho.cena)
        .where(Rascunho.marca_id == marca_id, Rascunho.cena != "")
        .order_by(col(Rascunho.id).desc())
        .limit(3)
    )
    evitar = " | ".join(cena.strip() for cena in recentes.all() if cena and cena.strip() and cena.strip() != descricao.strip())
    prompt = prompt_criar_imagem(descricao, len(referencias), alcool, negocio, evitar, random.choice(_LUZES))
    if referencias:
        largura, altura = (await asyncio.to_thread(arte.abrir, referencias[0])).size
        dados = await editar_varias(prompt, referencias[:4], tamanho_para(largura, altura, _modelo()))
    else:
        dados = await gerar_com_ia(prompt)
    plano = {
        "ideia": descricao.strip()[:200],
        "foto_base_id": None,
        "fotos_estilo_ids": [],
        "edicao_foto": diretor.NENHUMA,
        "modelo": "livre",
        "titulo_arte": "",
        "subtitulo_arte": "",
        "selo": "",
        "chamada": "",
        "antes": "",
        "cor": "destaque",
        "paleta": "marca",
        "posicao_texto": "auto",
        "aviso": "",
        "base_url": await _guardar_base(dados, marca_id),
        "feed_url": "",
        "story_url": "",
        "editada": True,
    }
    return await criar(session, marca, "", plano, formato, descricao, "", "Imagem criada com IA")


# ---------- o ajuste completo ----------


def _instrucao_numerada(instrucao: str, ids: list[int]) -> str:
    """A IA às vezes cita a foto pelo id da memória ('image 17'); a IA de imagem só conhece a ordem (image 2, 3...)."""
    posicoes = {ident: numero for numero, ident in enumerate(ids, start=2)}

    def trocar(achado: re.Match) -> str:
        ident = int(achado.group(2))
        return f"{achado.group(1)} {posicoes[ident]}" if ident in posicoes else achado.group(0)

    texto = re.sub(r"\b(image|photo|picture)\s*#?\[?(\d+)\]?", trocar, instrucao or "", flags=re.IGNORECASE)
    return re.sub(r"\[(\d+)\]", lambda achado: f"image {posicoes[int(achado.group(1))]}" if int(achado.group(1)) in posicoes else achado.group(0), texto)


def _mudar_arte(plano: dict, campos: dict) -> list[str]:
    mudou = []
    cor_livre = str(campos.pop("cor_livre", None) or "").strip().upper()
    if diretor.HEX.match(cor_livre) and (plano.get("paleta") != "livre" or plano.get("cor_livre") != cor_livre):
        plano.update(paleta="livre", cor_livre=cor_livre)
        campos.pop("paleta", None)
        mudou.append("cor_livre")
    for campo, valor in campos.items():
        if valor is None:
            continue
        valor = str(valor).strip()
        if str(plano.get(campo) or "") != valor:
            plano[campo] = valor
            mudou.append(campo)
    if "paleta" in mudou:
        plano["cor_livre"] = ""
    if plano.get("modelo") == "livre" and "modelo" not in mudou and any(campo in ROTULOS_ARTE for campo in mudou):
        plano["modelo"] = "minimal"
        mudou.append("modelo")
    return mudou


def _frase_arte(plano: dict, mudou: list[str]) -> str:
    partes = []
    for campo in mudou:
        if campo in ROTULOS_ARTE:
            valor = plano.get(campo) or ""
            partes.append(f"troquei {ROTULOS_ARTE[campo]} para “{valor}”" if valor else f"tirei {ROTULOS_ARTE[campo]}")
        elif campo == "modelo":
            from app.services.molde import NOMES_MODELOS

            partes.append(f"mudei o modelo da arte para {NOMES_MODELOS.get(plano.get('modelo'), plano.get('modelo'))}")
        elif campo == "cor":
            partes.append("usei a cor " + ("principal" if plano.get("cor") == "principal" else "de destaque") + " da marca")
        elif campo == "paleta":
            from app.services.molde import NOMES_PALETAS

            partes.append(f"troquei as cores para {NOMES_PALETAS.get(plano.get('paleta'), plano.get('paleta'))}")
        elif campo == "cor_livre":
            partes.append(f"usei a cor {plano.get('cor_livre')} na arte")
        elif campo == "posicao_texto":
            lugar = {"topo": "em cima", "base": "embaixo"}.get(plano.get("posicao_texto"), "onde a foto tem espaço")
            partes.append(f"coloquei o texto {lugar}")
    return ", ".join(partes)


def _juntar(frases: list[str]) -> str:
    frases = [frase.strip().rstrip(".") for frase in frases if frase and frase.strip()]
    if not frases:
        return ""
    texto = frases[0][0].upper() + frases[0][1:]
    for frase in frases[1:]:
        texto += ". " + frase[0].upper() + frase[1:]
    return texto + "."


async def ajustar(
    session: AsyncSession,
    registro: Rascunho,
    marca: Ficha | None,
    mensagem: str,
    anexos: list[int],
    anexo_url: str = "",
) -> dict:
    """Aplica a mensagem do chat no rascunho. 'arte', 'legenda' e 'formato' não gastam IA de imagem."""
    marca_id = marca.id if marca and marca.id else 0
    contexto = await montar_contexto(session, marca)
    lista = await listar_fotos(session, marca_id)
    fotos = {foto.id: foto for foto in lista if foto.usar_em_posts or foto.id in anexos}
    plano = plano_de(registro) or diretor.plano_padrao(registro.legenda or "", registro.gancho or "")
    if _foto_crua(plano) and pede_outra_foto(mensagem):
        descricao = (registro.cena or plano.get("ideia") or mensagem).strip()
        descricao = f"{descricao}. Faça outra foto, diferente da anterior."[:1500]
        novo = await criar_imagem(session, marca, descricao, anexos, anexo_url, registro.formato or "feed")
        return {
            "tipos": ["imagem"],
            "novo": False,
            "pergunta": False,
            "custo_imagem": True,
            "resposta": "Fiz outra foto, com outro enquadramento.",
            "rascunho_id": novo.id,
        }
    intencao = await ler_intencao(mensagem, registro, plano, fotos, anexos, contexto.fatos)
    lido = _conferir_intencao(intencao, mensagem, fotos, anexos)
    tipos = lido["tipos"]
    anexo = b""
    if anexo_url.strip() and id_da_url(anexo_url) is not None:
        anexo = await bytes_da_url(anexo_url)
        if "imagem" not in tipos and "novo" not in tipos:
            tipos.append("imagem")
    resposta = {"tipos": tipos, "novo": False, "pergunta": False, "custo_imagem": False}
    if "novo" in tipos:
        return {**resposta, "novo": True, "resposta": "Certo, vou começar um post novo."}
    if lido["pergunta"] and ("imagem" in tipos or not tipos):
        return {**resposta, "pergunta": True, "resposta": lido["pergunta"]}
    if not tipos:
        return {
            **resposta,
            "pergunta": True,
            "resposta": "Não entendi o que mudar. É na foto, no texto da arte, na legenda ou no formato?",
        }

    frases: list[str] = []
    legenda = registro.legenda or ""
    formato = registro.formato or "feed"
    precisa_montar = not plano.get("story_url") or not plano.get("feed_url")
    avisos: list[str] = []

    if "arte" in tipos:
        mudou = _mudar_arte(plano, intencao.arte.model_dump())
        if mudou:
            conferido = diretor.do_cliente(plano, contexto) or plano
            if conferido.get("modelo") != plano.get("modelo") and "modelo" in mudou:
                avisos.append("Esse modelo não combina com esta foto, então mantive outro")
            plano = conferido
            frases.append(_frase_arte(plano, mudou))
            precisa_montar = True
    if "legenda" in tipos and intencao.legenda_nova.strip():
        legenda = com_moderacao(fechar_legenda(intencao.legenda_nova.strip()[:5000], contexto.mapa))
        frases.append("reescrevi a legenda")
    if "formato" in tipos and intencao.formato in FORMATOS and intencao.formato != formato:
        formato = intencao.formato
        frases.append(f"mudei o formato para {NOMES_FORMATOS[formato]}")
    if "imagem" in tipos:
        referencias = [foto for foto in [await session.get(MemoriaFoto, ident) for ident in lido["ids"]] if foto is not None]
        if lido["tela"]:
            referencias.sort(key=lambda foto: not e_captura(foto))
        instrucao = _instrucao_numerada(intencao.instrucao_imagem or mensagem, [foto.id for foto in referencias])
        plano, como, custo = await editar_imagem(
            session, marca, plano, instrucao, lido["tela"], referencias, registro.cena or "", anexo or None
        )
        resposta["custo_imagem"] = custo
        precisa_montar = True
        resumo = intencao.resumo_mudanca.strip().rstrip(".")
        if como == "tela":
            frases.insert(0, (resumo or "Coloquei o print na tela do celular") + " e mantive o resto")
        elif como in ("quadro", "quadro_mudou", "quadro_falhou"):
            motivo = {
                "quadro": "Esta arte ainda não tinha foto",
                "quadro_mudou": "A IA mexeu demais na cena",
                "quadro_falhou": "A IA de imagem não conseguiu agora",
            }[como]
            frases.insert(
                0,
                f"{motivo}, então montei a arte Imagem em destaque com o print dentro da moldura do celular, "
                "mantendo título, selo e chamada",
            )
        elif como == "criada":
            frases.insert(0, "Criei a foto com IA e montei a arte")
        else:
            frases.insert(0, (resumo or "Mudei a foto como você pediu") + " e mantive o resto")

    if precisa_montar and (plano.get("base_url") or plano.get("foto_base_id")):
        plano = await diretor.refazer(session, marca, plano, formato)
    elif precisa_montar and "arte" in tipos:
        avisos.append("A foto ainda não foi feita: peça uma mudança na foto ou clique em Criar foto com IA")

    if not frases:
        if lido["pergunta"]:
            return {**resposta, "pergunta": True, "resposta": lido["pergunta"]}
        return {**resposta, "resposta": "Já está assim no rascunho. Quer mudar mais alguma coisa?"}
    _gravar_estado(registro, legenda, plano, formato)
    descricao = _juntar(frases)
    _nova_versao(registro, descricao[:200])
    await session.commit()
    await session.refresh(registro)
    texto = _juntar([*frases, *avisos])
    if "imagem" not in tipos:
        texto += " Não gastei IA de imagem."
    return {**resposta, "resposta": texto}


_FRASE_VAZIA = re.compile(
    r"praticidade|facilidade|simplif|gerenc|controle total|sem estresse|suas maos|"
    r"descomplique|tecnologia|experimente|saiba mais|peca ja|comece ja|comece agora"
)
_PALAVRA_FRACA = {
    "para", "como", "pelo", "pela", "mais", "seus", "suas", "seu", "sua",
    "com", "sem", "uma", "que", "voce", "este", "esta", "isso", "aqui",
}
MOLDES_TELA = ("minimal", "selo", "livre")
# Na tela do produto, cada arte mostra um pedaço diferente e um jeito diferente de escrever.
# Sem faixa, sem bloco de cor e sem o botão repetido em cima do sistema.
TRIOS_TELA = (
    (("minimal", "base", "meio"), ("selo", "topo", "base"), ("livre", "auto", "topo")),
    (("livre", "auto", "meio"), ("minimal", "topo", "base"), ("selo", "base", "topo")),
    (("selo", "base", "meio"), ("livre", "auto", "topo"), ("minimal", "topo", "base")),
)
ANGULOS = ("recurso", "dia", "resultado")


def _palavras_fortes(texto: str) -> set[str]:
    simples = "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))
    return {p for p in re.findall(r"[a-z]{4,}", simples) if p not in _PALAVRA_FRACA}


def frases_repetidas(titulos: list[str], ja_usadas: list[str]) -> bool:
    """True se a frase é anúncio vazio ou repete a ideia de outra."""
    for titulo in titulos:
        simples = "".join(c for c in unicodedata.normalize("NFKD", titulo.lower()) if not unicodedata.combining(c))
        if _FRASE_VAZIA.search(simples):
            return True
    grupos = [_palavras_fortes(titulo) for titulo in titulos if titulo.strip()]
    usados = [_palavras_fortes(titulo) for titulo in ja_usadas if titulo.strip()]
    for indice, atual in enumerate(grupos):
        if len(atual) < 2:
            continue
        for outro in grupos[indice + 1 :] + usados:
            if len(atual & outro) >= 2:
                return True
    return False


def moldes_da_rodada(marca: Ficha | None, giro: int, e_tela: bool) -> tuple[str, ...]:
    """Cada rodada começa num molde diferente. Tela de produto não leva pôster por cima."""
    base = MOLDES_TELA if e_tela else diretor.moldes_da_marca(marca)
    giro = giro % len(base)
    return base[giro:] + base[:giro]


def _aplicar_tres(opcoes: list[dict], marca: Ficha | None, giro: int, e_tela: bool, leitura=None) -> list[dict]:
    moldes = moldes_da_rodada(marca, giro, e_tela)
    posicoes = ("base", "topo", "auto")
    trio = TRIOS_TELA[giro % len(TRIOS_TELA)]
    faixa = leitura.quieta if leitura is not None and leitura.quieta in ("topo", "base") else ""
    if leitura is not None:
        segundo = leitura.botao if leitura.botao[3] > leitura.botao[1] else leitura.lista
        if segundo[3] <= segundo[1]:
            segundo = (0.06, 0.62, 0.94, 0.9)
        alvos = (leitura.numero, segundo, (0.0, 0.0, 1.0, 1.0))
    else:
        alvos = ()
    for indice, plano in enumerate(opcoes):
        if leitura is not None:
            plano["modelo"] = "tela_limpa"
            plano["posicao_texto"] = faixa or "fixo"
            plano["recorte_caixa"] = [float(valor) for valor in alvos[indice % 3]]
            plano["chamada"] = ""
            plano["subtitulo_arte"] = ""
            plano["tela_app"] = False
            plano["edicao_foto"] = diretor.NENHUMA
            titulo = " ".join(str(plano.get("titulo_arte") or "").split()[:4])
            plano["titulo_arte"] = titulo if faixa else ""
        elif e_tela:
            modelo, posicao, recorte = trio[indice % 3]
            plano["modelo"] = modelo
            plano["posicao_texto"] = posicao
            plano["recorte"] = recorte
            plano["chamada"] = ""
            plano["subtitulo_arte"] = ""
            plano["tela_app"] = False
            plano["edicao_foto"] = diretor.NENHUMA
            if modelo == "selo" and not str(plano.get("selo") or "").strip():
                plano["selo"] = " ".join(str(plano.get("titulo_arte") or "").split()[:2])[:24]
        else:
            if plano.get("modelo") not in ("tela_app", "antes_depois", "livre"):
                plano["modelo"] = moldes[indice % len(moldes)]
            plano["posicao_texto"] = posicoes[indice % 3]
            plano["recorte"] = ""
        plano["paleta"] = "marca"
        plano["cor_livre"] = ""
        plano["angulo"] = ANGULOS[indice] if plano.get("angulo") not in ANGULOS else plano["angulo"]
    return opcoes


def _jpeg_leitura(dados: bytes) -> str:
    """Foto pequena para o modelo ler o que está na imagem, sem gastar geração."""
    import base64
    import io

    with Image.open(io.BytesIO(dados)) as aberta:
        foto = ImageOps.exif_transpose(aberta).convert("RGB")
    foto.thumbnail((768, 768))
    buf = io.BytesIO()
    foto.save(buf, "JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


async def _bytes_hero(session: AsyncSession, marca_id: int, contexto, foto_id: int | None) -> tuple[bytes, str, bool]:
    """A foto que a pessoa ou o diretor escolheu. Sem escolha, a peça nasce sem foto inventada."""
    if not foto_id or foto_id not in contexto.usaveis:
        return b"", "", False
    foto = await session.get(MemoriaFoto, foto_id)
    if foto is None or foto.marca_id != marca_id or not foto.arquivo:
        return b"", "", False
    return foto.arquivo, url_memoria(foto.id or 0), e_captura(foto)


def _png_branco() -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (255, 255, 255)).save(buf, "PNG")
    return buf.getvalue()


def _pecas_fracas(titulos: list[str]) -> bool:
    vistos: list[str] = []
    for titulo in titulos:
        simples = "".join(c for c in unicodedata.normalize("NFKD", titulo.lower()) if not unicodedata.combining(c))
        simples = " ".join(simples.split())
        if not simples or _FRASE_VAZIA.search(simples):
            return True
        if simples in vistos:
            return True
        vistos.append(simples)
    return False


def _titulo_da_foto(titulo: str) -> str:
    """No máximo 4 palavras, e nenhum slogan de anúncio genérico."""
    texto = " ".join((titulo or "").split()[:4])
    simples = "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))
    proibidos = (
        "aumente seus lucros",
        "suporte 24h",
        "suporte 24",
        "dose na conta",
        "praticidade",
        "facilidade",
        "gerencie",
        "saiba mais",
        "peca ja",
        "comece ja",
        "experimente",
        "fale conosco",
    )
    if any(frase in simples for frase in proibidos):
        return ""
    return texto


PERGUNTA_FOTO = "Sem uma foto real eu não invento a cena. Escolha uma foto da memória ou envie o print da tela."


async def _foto_guardada(session: AsyncSession, marca_id: int, contexto) -> MemoriaFoto | None:
    """Uma foto que pode aparecer no post. Captura de tela vem primeiro."""
    candidatas: list[MemoriaFoto] = []
    for ident in contexto.usaveis:
        foto = await session.get(MemoriaFoto, ident)
        if foto is None or foto.marca_id != marca_id or not foto.arquivo:
            continue
        if contexto.papeis.get(ident) == "estilo":
            continue
        candidatas.append(foto)
    capturas = [foto for foto in candidatas if e_captura(foto)]
    grupo = capturas or candidatas
    if not grupo:
        return None
    return max(grupo, key=lambda foto: foto.id or 0)


async def criar_tres_artes(
    session: AsyncSession,
    marca: Ficha | None,
    descricao: str,
    formato: str,
    fotos_ids: list[int],
    anexo_url: str = "",
    ja_usadas: list[str] | None = None,
) -> dict:
    """Três peças. Com foto real, usa essa foto. Sem foto, a IA cria uma foto limpa e a frase entra na faixa vazia."""
    from pydantic import BaseModel, Field

    class _Pacote(BaseModel):
        ideia: str
        legenda: str
        titulos: list[str] = Field(min_length=3, max_length=3)

    marca_id = marca.id if marca and marca.id else 0
    contexto = await montar_contexto(session, marca)
    formato = formato if formato in FORMATOS else "feed"
    alvo = "story" if formato == "story" else ("facebook" if formato == "facebook" else "feed")
    escolhidas: list[MemoriaFoto] = []
    for ident in fotos_ids[:4]:
        foto = await session.get(MemoriaFoto, ident)
        if foto is not None and foto.marca_id == marca_id and foto.arquivo:
            escolhidas.append(foto)
    foto_lida = b""
    url_base = ""
    hero_id = None
    if escolhidas:
        foto_lida = escolhidas[0].arquivo
        hero_id = escolhidas[0].id
        url_base = url_memoria(hero_id or 0)
    elif anexo_url.strip() and id_da_url(anexo_url) is not None:
        try:
            foto_lida = await bytes_da_url(anexo_url)
            url_base = anexo_url.strip()
        except Exception:
            foto_lida = b""
    gerada = False
    if not foto_lida:
        from app.services.memoria import ler_fatos
        from app.services.midia import gerar_com_ia

        fatos = ler_fatos(marca)
        negocio = ". ".join(fatos[chave] for chave in ("oferta", "itens", "diferenciais") if fatos.get(chave))
        prompt = prompt_criar_imagem(
            descricao,
            0,
            fala_de_alcool(f"{descricao} {contexto.fatos}"),
            negocio,
            "",
            random.choice(_LUZES),
        )
        foto_lida = await gerar_com_ia(prompt)
        url_base = await _guardar_base(foto_lida, marca_id)
        gerada = True

    from app.services.leitura_tela import LeituraTela, ler_tela_bytes

    try:
        leitura = await asyncio.to_thread(ler_tela_bytes, foto_lida)
    except Exception:
        leitura = None
    if leitura is None:
        leitura = LeituraTela(False, "", (0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0))
    if gerada and not leitura.quieta:
        leitura = LeituraTela(leitura.e_tela, "base", leitura.numero, leitura.botao, leitura.lista)
    recente = await artes_recentes(session, marca_id)
    ja = [titulo.strip()[:80] for titulo in (ja_usadas or []) if titulo and titulo.strip()][:12]
    sistema = (
        "Você escreve três títulos para a mesma foto, em português do Brasil. "
        "Cada título tem no máximo 4 palavras e nomeia algo que está visível: um número, um botão, um prato, um item da tela. "
        "Os três títulos são diferentes entre si. Sem subtítulo e sem chamada em cima da foto. "
        "Proibido: aumente seus lucros, suporte 24h, dose na conta, praticidade, facilidade, gerencie, "
        "saiba mais, peça já, comece já, experimente, fale conosco.\n"
        "Não invente preço, telefone, desconto nem recurso que não esteja na foto ou na memória.\n"
        "A legenda segue o contrato da memória: item concreto na primeira linha, um fato da marca, "
        "a chamada do canal e as hashtags fixas mais no máximo 2 do assunto.\n"
        + contexto.regras()
    )
    if fala_de_alcool(f"{descricao} {contexto.fatos}"):
        sistema += diretor.REGRAS_ARTE_ALCOOL
    partes = [f"Pedido: {descricao.strip()[:1500]}", f"Tamanho da arte: {NOMES_FORMATOS.get(formato, 'Feed')}."]
    if contexto.texto.strip():
        partes.append(contexto.texto.strip()[:12000])
    if recente:
        partes.append("Artes já feitas desta marca. Não repita o título nem a ideia:\n" + "\n".join(f"- {linha}" for linha in recente))
    if ja:
        partes.append("Frases já mostradas agora. Não repita e não troque por sinônimo:\n" + "\n".join(f"- {titulo}" for titulo in ja))
    if leitura.quieta:
        partes.append(leitura.texto())
    else:
        partes.append("Não há faixa livre na foto. Devolva os três títulos vazios.")

    async def pedir(extra: str = ""):
        usuario = "\n\n".join(partes)
        if extra:
            usuario += "\n\n" + extra
        try:
            resposta = await cliente_openai().with_options(timeout=90).chat.completions.parse(
                model=get_settings().openai_model,
                messages=[
                    {"role": "system", "content": sistema},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": usuario},
                            {"type": "image_url", "image_url": {"url": _jpeg_leitura(foto_lida)}},
                        ],
                    },
                ],
                response_format=_Pacote,
            )
        except Exception:
            resposta = await cliente_openai().with_options(timeout=90).chat.completions.parse(
                model=get_settings().openai_model,
                messages=[
                    {"role": "system", "content": sistema},
                    {"role": "user", "content": usuario},
                ],
                response_format=_Pacote,
            )
        mensagem = resposta.choices[0].message
        if mensagem.refusal:
            raise ValueError(mensagem.refusal)
        if mensagem.parsed is None or len(mensagem.parsed.titulos) < 3:
            raise ValueError("A IA não devolveu as artes. Tente descrever de novo.")
        return mensagem.parsed

    pacote = await pedir()
    titulos = [_titulo_da_foto(titulo) for titulo in list(pacote.titulos[:3]) + [""] * 3][:3]
    if leitura.quieta and _pecas_fracas(titulos):
        pacote = await pedir(
            "A tentativa anterior foi recusada. Títulos: "
            + "; ".join(titulos)
            + ". Escreva outros três, cada um com no máximo 4 palavras, nomeando algo visível na foto."
        )
        titulos = [_titulo_da_foto(titulo) for titulo in list(pacote.titulos[:3]) + [""] * 3][:3]
    angulos = ("recurso", "dia", "resultado")
    brutos = [
        {
            "ideia": pacote.ideia,
            "modelo": "tela_limpa",
            "titulo_arte": titulos[indice] if indice < len(titulos) else "",
            "subtitulo_arte": "",
            "chamada": "",
            "edicao_foto": diretor.NENHUMA,
            "angulo": angulos[indice],
            "foto_base_id": hero_id,
        }
        for indice in range(3)
    ]
    planos = []
    for bruto in brutos:
        plano, _legenda = diretor.validar_plano(bruto, contexto, descricao, pacote.legenda)
        plano["angulo"] = bruto["angulo"]
        planos.append(plano)
    planos = _aplicar_tres(planos, marca, len(ja) // 3, True, leitura)
    for plano in planos:
        plano["foto_base_id"] = hero_id
        plano["base_url"] = url_base
        plano["editada"] = False
        plano["sem_foto"] = False
        url = await diretor.desenhar_um(session, marca, plano, foto_lida, alvo)
        plano["feed_url"] = url
        plano["story_url"] = url if alvo == "story" else ""
    legenda = fechar_legenda(pacote.legenda.strip()[:5000], contexto.mapa)
    if fala_de_alcool(f"{descricao} {legenda}"):
        legenda = com_moderacao(legenda)
    return {"legenda": legenda, "formato": formato, "opcoes": planos, "precisa_foto": ""}



async def reabrir(session: AsyncSession, registro: Rascunho) -> Rascunho:
    await fechar_abertos(session, registro.marca_id)
    registro.status = "aberto"
    registro.atualizado_em = agora()
    await session.commit()
    await session.refresh(registro)
    return registro


def _miniatura(plano: dict | None, imagem_url: str = "") -> str:
    if plano and (plano.get("feed_url") or plano.get("story_url")):
        return str(plano.get("feed_url") or plano.get("story_url"))
    return imagem_url or ""


async def listar_prateleira(session: AsyncSession, marca_id: int) -> list[dict]:
    itens: list[dict] = []
    rascunhos = await session.exec(
        select(Rascunho)
        .where(Rascunho.marca_id == marca_id, col(Rascunho.status).in_(["aberto", "guardado"]))
        .order_by(col(Rascunho.atualizado_em).desc())
        .limit(30)
    )
    for registro in rascunhos.all():
        plano = plano_de(registro) or {}
        url = _miniatura(plano)
        if not url:
            continue
        itens.append(
            {
                "id": registro.id or 0,
                "tipo": "rascunho",
                "titulo": (plano.get("titulo_arte") or registro.titulo or "Rascunho")[:80],
                "quando": "Aberto no estúdio" if registro.status == "aberto" else "Rascunho",
                "miniatura": url,
                "formato": registro.formato or "feed",
                "legenda": (registro.legenda or "")[:180],
            }
        )
    agenda = await session.exec(
        select(Agendamento)
        .where(
            Agendamento.marca_id == marca_id,
            col(Agendamento.status).in_(["na_fila", "falta_imagem", "erro", "manual", "publicando", "publicado"]),
        )
        .order_by(col(Agendamento.id).desc())
        .limit(40)
    )
    for item in agenda.all():
        plano = diretor.ler_plano(item.plano_arte)
        url = _miniatura(plano, item.imagem_url or "")
        if not url:
            continue
        publicado = item.status == "publicado"
        itens.append(
            {
                "id": item.id or 0,
                "tipo": "publicado" if publicado else "agendado",
                "titulo": (item.titulo or (plano or {}).get("titulo_arte") or "Post")[:80],
                "quando": f"{item.dia} · {item.hora}" if item.dia else item.status,
                "miniatura": url,
                "formato": item.formato or "feed",
                "legenda": (item.legenda or "")[:180],
            }
        )
    return itens

