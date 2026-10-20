import asyncio
import json
import time
import urllib.parse
from datetime import date, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_sessionmaker
from app.models import Agendamento, ContaRede, Publicacao, agora
from app.services.publicar import VERSAO, ZONA, _pedir, marca_efetiva, texto_do_post

IG = f"https://graph.instagram.com/{VERSAO}"
FB = f"https://graph.facebook.com/{VERSAO}"
REDES = {"instagram": "Instagram", "facebook": "Facebook"}

FALTA_PERMISSAO = "Falta permissão de métricas: ligue a conta de novo."
STORY_SAIU = "O Story saiu do ar antes da medição."

ETAPAS_STORY = (timedelta(hours=2), timedelta(hours=22))
ETAPAS_FEED = (timedelta(hours=48), timedelta(days=7))
VIDA_STORY = timedelta(hours=24)

LIMITE_PEDIDOS = 20
TEMPO_RODADA = 60
TIMEOUT = 20
BUSCA_ANTIGOS_A_CADA = 6 * 3600
ESPERA_ATUALIZAR = 60
MEDIDO_HA_POUCO = timedelta(minutes=30)

CAMPOS_IG_LISTA = "id,caption,media_type,media_product_type,timestamp,permalink,media_url,thumbnail_url"
CAMPOS_FB_LISTA = "id,message,created_time,permalink_url,full_picture"
METRICAS_IG_STORY = "views,reach,replies,shares,total_interactions"
METRICAS_IG_FEED = "views,reach,likes,comments,saved,shares,total_interactions"
METRICAS_FB = "post_media_view,post_total_media_view_unique,post_reactions_by_type_total"
NOMES_IG = {
    "views": "visualizacoes",
    "reach": "alcance",
    "likes": "curtidas",
    "comments": "comentarios",
    "saved": "salvos",
    "shares": "compartilhamentos",
    "total_interactions": "interacoes",
    "replies": "respostas",
}
NOMES_FB = {
    "post_media_view": "visualizacoes",
    "post_total_media_view_unique": "alcance",
    "post_reactions_by_type_total": "curtidas",
}

trava_medicao = asyncio.Lock()
_ultima_busca: dict[tuple[int, str], float] = {}
_ultimo_atualizar: dict[int, float] = {}


class Orcamento:
    """Limite de pedidos à Meta e de tempo por rodada, para não atrasar a publicação."""

    def __init__(self, pedidos: int = LIMITE_PEDIDOS, segundos: int = TEMPO_RODADA) -> None:
        self.pedidos = pedidos
        self.fim = time.monotonic() + segundos

    def pode(self, quantos: int = 1) -> bool:
        return self.pedidos >= quantos and time.monotonic() < self.fim

    def gastar(self, quantos: int) -> None:
        self.pedidos -= quantos


def _ler(base: str, caminho: str, token: str, **params) -> dict:
    query = urllib.parse.urlencode({**params, "access_token": token})
    return _pedir(f"{base}/{caminho}?{query}", timeout=TIMEOUT)


def etapas(formato: str) -> tuple[timedelta, ...]:
    return ETAPAS_STORY if formato == "story" else ETAPAS_FEED


def proxima(publicado_em: datetime, formato: str, depois: datetime) -> datetime | None:
    for passo in etapas(formato):
        if publicado_em + passo > depois:
            return publicado_em + passo
    return None


def _quando(texto: str) -> datetime:
    try:
        return datetime.strptime(str(texto), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return agora()


def _curta(texto: str, tamanho: int = 300) -> str:
    return " ".join(str(texto or "").split())[:tamanho]


def _link_facebook(link: object) -> str:
    texto = str(link or "")
    if texto.startswith("/"):
        texto = "https://www.facebook.com" + texto
    return texto[:500]


def _falta_permissao(mensagem: str) -> bool:
    texto = mensagem.lower()
    if "not enough viewers" in texto:
        return False
    return "permission" in texto or "permiss" in texto or "scope" in texto


def _explicar(mensagem: str) -> str:
    texto = mensagem.lower()
    if "not enough viewers" in texto:
        return "Poucas visualizações ainda. O número aparece depois."
    if "does not exist" in texto or "unsupported get request" in texto:
        return "O post não foi encontrado na rede (pode ter sido apagado)."
    return _curta(mensagem, 160)


def _linha(
    marca_id: int,
    rede: str,
    formato: str,
    externo_id: str,
    publicado_em: datetime,
    legenda: str = "",
    permalink: str = "",
    imagem_url: str = "",
    tema: str = "",
    agendamento_id: int | None = None,
) -> dict:
    return {
        "agendamento_id": agendamento_id,
        "marca_id": marca_id,
        "rede": rede,
        "formato": formato,
        "externo_id": externo_id[:80],
        "permalink": permalink[:500],
        "legenda": _curta(legenda),
        "imagem_url": imagem_url or "",
        "tema": (tema or "")[:30],
        "hora": publicado_em.astimezone(ZONA).strftime("%H:%M"),
        "publicado_em": publicado_em,
        "metricas": "",
        "medido_em": None,
        "medicoes": 0,
        "proxima_medicao": publicado_em + etapas(formato)[0],
        "detalhe": "",
        "criado_em": agora(),
    }


async def _inserir(session: AsyncSession, linha: dict) -> bool:
    resultado = await session.exec(
        insert(Publicacao).values(**linha).on_conflict_do_nothing(index_elements=["rede", "externo_id"])
    )
    return bool(resultado.rowcount)


def _link_publicado(rede: str, externo_id: str, token: str, pagina_id: str) -> tuple[str, str]:
    """Busca o link do post logo depois de publicar. No vídeo do Facebook troca o id do vídeo pelo do post."""
    if rede == "Instagram":
        dados = _ler(IG, externo_id, token, fields="permalink")
        return externo_id, str(dados.get("permalink") or "")[:500]
    if "_" in externo_id:
        dados = _ler(FB, externo_id, token, fields="permalink_url")
        return externo_id, _link_facebook(dados.get("permalink_url"))
    try:
        dados = _ler(FB, externo_id, token, fields="permalink_url,post_id")
    except RuntimeError:
        dados = _ler(FB, externo_id, token, fields="permalink_url")
    post = str(dados.get("post_id") or "")
    if post and "_" not in post and pagina_id:
        post = f"{pagina_id}_{post}"
    return post or externo_id, _link_facebook(dados.get("permalink_url"))


async def anotar_publicacao(
    marca_id: int,
    rede: str,
    formato: str,
    externo_id: str,
    token: str,
    texto: str,
    imagem_url: str,
    tema: str = "",
    agendamento_id: int | None = None,
    pagina_id: str = "",
) -> None:
    """Grava o post que acabou de sair. Nunca levanta erro: o post já foi publicado."""
    if not externo_id:
        return
    try:
        externo_id, permalink = await asyncio.to_thread(_link_publicado, rede, externo_id, token, pagina_id)
    except Exception:
        permalink = ""
    try:
        linha = _linha(marca_id, rede, formato, externo_id, agora(), texto, permalink, imagem_url, tema, agendamento_id)
        async with get_sessionmaker()() as session:
            await _inserir(session, linha)
            await session.commit()
    except Exception:
        pass


def _formato_ig(item: dict) -> str:
    produto = str(item.get("media_product_type") or "").upper()
    if produto == "STORY":
        return "story"
    if produto == "REELS":
        return "reels"
    if str(item.get("media_type") or "").upper() == "CAROUSEL_ALBUM":
        return "carrossel"
    return "feed"


def _item_ig(item: dict) -> dict:
    return {
        "externo_id": str(item.get("id") or ""),
        "formato": _formato_ig(item),
        "legenda": str(item.get("caption") or ""),
        "quando": _quando(item.get("timestamp")),
        "permalink": str(item.get("permalink") or ""),
        "imagem": str(item.get("thumbnail_url") or item.get("media_url") or ""),
    }


def _item_fb(item: dict) -> dict:
    return {
        "externo_id": str(item.get("id") or ""),
        "formato": "feed",
        "legenda": str(item.get("message") or ""),
        "quando": _quando(item.get("created_time")),
        "permalink": _link_facebook(item.get("permalink_url")),
        "imagem": str(item.get("full_picture") or ""),
    }


def _chave(texto: str) -> str:
    return " ".join(str(texto or "").lower().split())[:40]


def _casar(legenda: str, quando: datetime, agendas: list[Agendamento]) -> Agendamento | None:
    chave = _chave(legenda)
    if len(chave) < 10:
        return None
    dia = quando.astimezone(ZONA).date()
    for agenda in agendas:
        try:
            dia_agenda = date.fromisoformat(agenda.dia)
        except ValueError:
            continue
        if abs((dia - dia_agenda).days) <= 1 and _chave(texto_do_post(agenda.titulo, agenda.legenda)) == chave:
            return agenda
    return None


async def buscar_antigos(session: AsyncSession, conta: ContaRede, orcamento: Orcamento) -> tuple[int, int]:
    """Lê os posts recentes da conta (só GET) e grava os que faltam. Devolve (encontrados, novos)."""
    rede = REDES[conta.rede]
    itens: list[dict] = []
    if rede == "Instagram":
        orcamento.gastar(1)
        dados = await asyncio.to_thread(
            _ler, IG, f"{conta.externo_id}/media", conta.token, fields=CAMPOS_IG_LISTA, limit=25
        )
        itens += [_item_ig(item) for item in dados.get("data") or []]
        if orcamento.pode():
            orcamento.gastar(1)
            try:
                stories = await asyncio.to_thread(
                    _ler, IG, f"{conta.externo_id}/stories", conta.token, fields=CAMPOS_IG_LISTA
                )
                itens += [{**_item_ig(item), "formato": "story"} for item in stories.get("data") or []]
            except RuntimeError:
                pass
    else:
        orcamento.gastar(1)
        dados = await asyncio.to_thread(
            _ler, FB, f"{conta.externo_id}/published_posts", conta.token, fields=CAMPOS_FB_LISTA, limit=25
        )
        itens += [_item_fb(item) for item in dados.get("data") or []]
    itens = [item for item in itens if item["externo_id"]]
    if not itens:
        return 0, 0
    existentes = await session.exec(
        select(Publicacao.externo_id).where(
            Publicacao.rede == rede, col(Publicacao.externo_id).in_([item["externo_id"] for item in itens])
        )
    )
    ja_tem = set(existentes.all())
    agendas = list(
        (
            await session.exec(
                select(Agendamento).where(
                    Agendamento.marca_id == conta.marca_id,
                    or_(
                        Agendamento.status == "publicado",
                        Agendamento.feito != "",
                        Agendamento.ok_instagram,
                        Agendamento.ok_facebook,
                    ),
                )
            )
        ).all()
    )
    novos = 0
    for item in itens:
        if item["externo_id"] in ja_tem:
            continue
        agenda = _casar(item["legenda"], item["quando"], agendas)
        linha = _linha(
            conta.marca_id,
            rede,
            item["formato"],
            item["externo_id"],
            item["quando"],
            item["legenda"],
            item["permalink"],
            item["imagem"],
            agenda.tema if agenda else "",
            agenda.id if agenda else None,
        )
        if await _inserir(session, linha):
            novos += 1
    await session.commit()
    return len(itens), novos


def _valores(resposta: dict) -> dict[str, int]:
    saida: dict[str, int] = {}
    for item in resposta.get("data") or []:
        valores = item.get("values") or []
        valor = valores[0].get("value") if valores else (item.get("total_value") or {}).get("value")
        if isinstance(valor, dict):
            valor = sum(parte for parte in valor.values() if isinstance(parte, (int, float)))
        if isinstance(valor, (int, float)):
            saida[str(item.get("name") or "")] = int(valor)
    return saida


def _medida() -> dict:
    return {"metricas": {}, "detalhe": "", "pedidos": 0, "sem_permissao": False, "imagem": "", "permalink": ""}


def _medir_instagram(externo_id: str, formato: str, token: str, com_insights: bool) -> dict:
    medida = _medida()
    story = formato == "story"
    campos = "permalink,media_url,thumbnail_url" + ("" if story else ",like_count,comments_count")
    medida["pedidos"] += 1
    try:
        dados = _ler(IG, externo_id, token, fields=campos)
    except RuntimeError as erro:
        medida["detalhe"] = _explicar(str(erro))
        return medida
    medida["permalink"] = str(dados.get("permalink") or "")
    medida["imagem"] = str(dados.get("thumbnail_url") or dados.get("media_url") or "")
    if isinstance(dados.get("like_count"), int):
        medida["metricas"]["curtidas"] = dados["like_count"]
    if isinstance(dados.get("comments_count"), int):
        medida["metricas"]["comentarios"] = dados["comments_count"]
    if not com_insights:
        medida["sem_permissao"] = True
        return medida
    medida["pedidos"] += 1
    try:
        resposta = _ler(IG, f"{externo_id}/insights", token, metric=METRICAS_IG_STORY if story else METRICAS_IG_FEED)
    except RuntimeError as erro:
        if _falta_permissao(str(erro)):
            medida["sem_permissao"] = True
        else:
            medida["detalhe"] = _explicar(str(erro))
        return medida
    for nome, valor in _valores(resposta).items():
        if nome in NOMES_IG:
            medida["metricas"][NOMES_IG[nome]] = valor
    return medida


def _medir_facebook(externo_id: str, formato: str, token: str, com_insights: bool) -> dict:
    """Reações e comentários direto no post pedem pages_read_user_content; as reações vêm das métricas."""
    medida = _medida()
    post = "_" in externo_id
    medida["pedidos"] += 1
    try:
        dados = _ler(FB, externo_id, token, fields="permalink_url,full_picture,shares" if post else "permalink_url,picture")
    except RuntimeError as erro:
        medida["detalhe"] = _explicar(str(erro))
        return medida
    medida["permalink"] = _link_facebook(dados.get("permalink_url"))
    medida["imagem"] = str(dados.get("full_picture") or dados.get("picture") or "")
    if not post:
        return medida
    medida["metricas"]["compartilhamentos"] = int((dados.get("shares") or {}).get("count") or 0)
    if not com_insights:
        medida["sem_permissao"] = True
        return medida
    medida["pedidos"] += 1
    try:
        resposta = _ler(FB, f"{externo_id}/insights", token, metric=METRICAS_FB)
    except RuntimeError as erro:
        if _falta_permissao(str(erro)):
            medida["sem_permissao"] = True
        else:
            medida["detalhe"] = _explicar(str(erro))
        return medida
    if not resposta.get("data"):
        # Sem read_insights a Meta responde 200 com a lista vazia, não com erro.
        medida["sem_permissao"] = True
        return medida
    for nome, valor in _valores(resposta).items():
        if nome in NOMES_FB:
            medida["metricas"][NOMES_FB[nome]] = valor
    return medida


def ler_metricas(texto: str) -> dict:
    try:
        dados = json.loads(texto or "{}")
    except json.JSONDecodeError:
        return {}
    return dados if isinstance(dados, dict) else {}


def _story_fora_do_ar(pub: Publicacao, momento: datetime) -> bool:
    return pub.formato == "story" and momento > pub.publicado_em + VIDA_STORY


async def _medir(session: AsyncSession, pub: Publicacao, conta: ContaRede, com_insights: bool, orcamento: Orcamento) -> None:
    momento = agora()
    if _story_fora_do_ar(pub, momento):
        pub.proxima_medicao = None
        if not pub.metricas or pub.detalhe == FALTA_PERMISSAO:
            pub.detalhe = STORY_SAIU
        await session.commit()
        return
    medir = _medir_instagram if pub.rede == "Instagram" else _medir_facebook
    try:
        medida = await asyncio.to_thread(medir, pub.externo_id, pub.formato, conta.token, com_insights)
    except Exception as erro:
        medida = {**_medida(), "pedidos": 1, "detalhe": _curta(str(erro), 160)}
    orcamento.gastar(medida["pedidos"])
    metricas = ler_metricas(pub.metricas)
    metricas.update(medida["metricas"])
    pub.metricas = json.dumps(metricas) if metricas else ""
    if medida["permalink"]:
        pub.permalink = medida["permalink"][:500]
    if medida["imagem"]:
        pub.imagem_url = medida["imagem"]
    pub.detalhe = FALTA_PERMISSAO if medida["sem_permissao"] else medida["detalhe"]
    pub.medido_em = agora()
    pub.medicoes = (pub.medicoes or 0) + 1
    pub.proxima_medicao = proxima(pub.publicado_em, pub.formato, pub.medido_em)
    await session.commit()


async def _contas(session: AsyncSession, marca_id: int = 0) -> list[ContaRede]:
    consulta = select(ContaRede).where(col(ContaRede.rede).in_(list(REDES)))
    if marca_id:
        consulta = consulta.where(ContaRede.marca_id == marca_id)
    resultado = await session.exec(consulta.order_by(ContaRede.marca_id, ContaRede.rede))
    return [conta for conta in resultado.all() if conta.token and conta.externo_id]


async def sem_permissao(session: AsyncSession, conta: ContaRede) -> bool:
    """True quando a métrica já foi recusada depois da última vez que a conta foi ligada."""
    ligada_em = conta.atualizado_em
    if conta.rede == "facebook":
        acesso = await session.exec(
            select(ContaRede.atualizado_em).where(
                ContaRede.marca_id == conta.marca_id, ContaRede.rede == "facebook_acesso"
            )
        )
        ligada_em = max([ligada_em, *[quando for quando in acesso.all() if quando]])
    resultado = await session.exec(
        select(Publicacao.id)
        .where(
            Publicacao.marca_id == conta.marca_id,
            Publicacao.rede == REDES[conta.rede],
            Publicacao.detalhe == FALTA_PERMISSAO,
            Publicacao.medido_em >= ligada_em,
        )
        .limit(1)
    )
    return resultado.first() is not None


async def _medir_conta(session: AsyncSession, conta: ContaRede, orcamento: Orcamento, agora_mesmo: bool) -> int:
    com_insights = not await sem_permissao(session, conta)
    momento = agora()
    consulta = select(Publicacao).where(
        Publicacao.marca_id == conta.marca_id, Publicacao.rede == REDES[conta.rede]
    )
    if agora_mesmo:
        consulta = consulta.where(
            or_(Publicacao.medido_em.is_(None), Publicacao.medido_em < momento - MEDIDO_HA_POUCO)
        ).order_by(col(Publicacao.medido_em).asc().nulls_first(), col(Publicacao.publicado_em).desc())
    else:
        vencidos = Publicacao.proxima_medicao <= momento
        if com_insights:
            vencidos = or_(vencidos, Publicacao.detalhe == FALTA_PERMISSAO)
        consulta = consulta.where(vencidos).order_by(col(Publicacao.proxima_medicao).asc().nulls_last())
    resultado = await session.exec(consulta.limit(40))
    medidos = 0
    for pub in resultado.all():
        if agora_mesmo and _story_fora_do_ar(pub, momento) and pub.medido_em is not None:
            continue
        if not orcamento.pode(2):
            break
        await _medir(session, pub, conta, com_insights, orcamento)
        medidos += 1
    return medidos


async def _buscar_se_precisa(session: AsyncSession, conta: ContaRede, orcamento: Orcamento, sempre: bool) -> None:
    chave = (conta.marca_id, conta.rede)
    ultima = _ultima_busca.get(chave)
    if not sempre and ultima is not None and time.monotonic() - ultima < BUSCA_ANTIGOS_A_CADA:
        return
    if not orcamento.pode(2):
        return
    _ultima_busca[chave] = time.monotonic()
    try:
        await buscar_antigos(session, conta, orcamento)
    except Exception:
        await session.rollback()


async def medir_rodada(session: AsyncSession) -> None:
    """Parte leve do robô: busca posts novos de vez em quando e mede o que venceu (2h/22h no Story, 48h/7 dias no resto)."""
    if trava_medicao.locked():
        return
    async with trava_medicao:
        orcamento = Orcamento()
        contas = await _contas(session)
        for conta in contas:
            await _buscar_se_precisa(session, conta, orcamento, sempre=False)
        for conta in contas:
            if not orcamento.pode(2):
                break
            try:
                await _medir_conta(session, conta, orcamento, agora_mesmo=False)
            except Exception:
                await session.rollback()


async def atualizar_agora(session: AsyncSession, marca_id: int) -> str:
    """Mede já os posts da marca (com limite). Devolve um aviso, ou '' quando mediu."""
    marca = await marca_efetiva(session, marca_id)
    passou = time.monotonic() - _ultimo_atualizar.get(marca, -1e9)
    if passou < ESPERA_ATUALIZAR:
        return f"Atualizei há pouco. Tente de novo em {int(ESPERA_ATUALIZAR - passou) + 1} segundos."
    if trava_medicao.locked():
        return "Já estou medindo agora. Tente de novo em instantes."
    async with trava_medicao:
        contas = await _contas(session, marca)
        if not contas:
            return "Ligue o Instagram ou o Facebook desta marca para ver os resultados."
        _ultimo_atualizar[marca] = time.monotonic()
        orcamento = Orcamento()
        for conta in contas:
            await _buscar_se_precisa(session, conta, orcamento, sempre=True)
        for conta in contas:
            if not orcamento.pode(2):
                break
            try:
                await _medir_conta(session, conta, orcamento, agora_mesmo=True)
            except Exception:
                await session.rollback()
    return ""


def _numero(metricas: dict, nome: str) -> int | None:
    valor = metricas.get(nome)
    return valor if isinstance(valor, int) else None


def _engajamento(metricas: dict) -> int:
    return sum(_numero(metricas, nome) or 0 for nome in ("curtidas", "comentarios", "salvos", "compartilhamentos"))


def _estado(pub: Publicacao, metricas: dict) -> str:
    if pub.detalhe == FALTA_PERMISSAO:
        return "sem_permissao"
    if metricas:
        return "medido"
    if pub.detalhe:
        return "aviso"
    return "medindo"


def _item(pub: Publicacao) -> dict:
    metricas = ler_metricas(pub.metricas)
    local = pub.publicado_em.astimezone(ZONA)
    return {
        "id": pub.id or 0,
        "rede": pub.rede,
        "formato": pub.formato,
        "tema": pub.tema or "",
        "dia": local.strftime("%d/%m/%Y"),
        "hora": pub.hora or local.strftime("%H:%M"),
        "legenda": _curta(pub.legenda, 140),
        "imagem_url": pub.imagem_url or "",
        "permalink": pub.permalink or "",
        "alcance": _numero(metricas, "alcance"),
        "visualizacoes": _numero(metricas, "visualizacoes"),
        "curtidas": _numero(metricas, "curtidas"),
        "comentarios": _numero(metricas, "comentarios"),
        "salvos": _numero(metricas, "salvos"),
        "compartilhamentos": _numero(metricas, "compartilhamentos"),
        "estado": _estado(pub, metricas),
        "detalhe": pub.detalhe or "",
        "medido_em": pub.medido_em.astimezone(ZONA).strftime("%d/%m %H:%M") if pub.medido_em else "",
    }


async def listar_resultados(session: AsyncSession, marca_id: int) -> dict:
    marca = await marca_efetiva(session, marca_id)
    resultado = await session.exec(
        select(Publicacao)
        .where(Publicacao.marca_id == marca)
        .order_by(col(Publicacao.publicado_em).desc())
        .limit(100)
    )
    pubs = list(resultado.all())
    desde = agora() - timedelta(days=30)
    recentes = [pub for pub in pubs if pub.publicado_em >= desde]
    alcances = [(ler_metricas(pub.metricas).get("alcance"), pub) for pub in recentes]
    com_alcance = [(valor, pub) for valor, pub in alcances if isinstance(valor, int)]
    melhor = None
    if com_alcance:
        valor, pub = max(com_alcance, key=lambda par: par[0])
        melhor = {**_item(pub), "numero": valor, "rotulo": "pessoas alcançadas"}
    elif recentes:
        pub = max(recentes, key=lambda item: _engajamento(ler_metricas(item.metricas)))
        valor = _engajamento(ler_metricas(pub.metricas))
        if valor > 0:
            melhor = {**_item(pub), "numero": valor, "rotulo": "interações"}
    contas = await _contas(session, marca)
    faltam = [REDES[conta.rede] for conta in contas if await sem_permissao(session, conta)]
    return {
        "marca_id": marca,
        "itens": [_item(pub) for pub in pubs],
        "resumo": {
            "posts_30_dias": len(recentes),
            "alcance_medio": round(sum(valor for valor, _pub in com_alcance) / len(com_alcance)) if com_alcance else None,
            "melhor": melhor,
        },
        "contas_ligadas": bool(contas),
        "falta_permissao": faltam,
    }
