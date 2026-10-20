import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Agendamento, Publicacao, agora
from app.services.calendario import (
    ZONA,
    chave_clima,
    clima_guardado,
    clima_volta_sozinho,
    garantir_sugestoes,
    listar_sugestoes,
    previsao,
)
from app.services.publicar import texto_do_post
from app.services.resultados import _contas, _engajamento, ler_metricas
from app.services.semana import marca_por_id

DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]
NOMES_TEMA = {
    "bastidores": "Bastidores",
    "oferta": "Oferta",
    "dica": "Dica",
    "história": "História",
    "prova": "Prova",
    "interação": "Interação",
}
NA_FILA = ("na_fila", "falta_imagem", "publicando")
ESPERA_CLIMA = 4
MEDINDO = "Ainda estamos medindo. Os números aparecem uns dias depois de cada post."
SEM_CONTA = "Ligue o Instagram ou o Facebook desta marca para ver os números."


def saudacao(momento: datetime) -> str:
    if 5 <= momento.hour < 12:
        return "Bom dia"
    if 12 <= momento.hour < 18:
        return "Boa tarde"
    return "Boa noite"


def data_extenso(dia: date) -> str:
    return f"{DIAS[dia.weekday()].capitalize()}, {dia.day} de {MESES[dia.month - 1]}"


def _primeira_linha(titulo: str, legenda: str) -> str:
    texto = texto_do_post(titulo or "", legenda or "")
    linha = next((parte.strip() for parte in texto.splitlines() if parte.strip()), "")
    return linha[:140]


def _post(registro: Agendamento) -> dict:
    return {
        "id": registro.id or 0,
        "dia": registro.dia,
        "hora": registro.hora,
        "rede": registro.rede,
        "formato": registro.formato or "feed",
        "tema": registro.tema or "",
        "status": registro.status,
        "detalhe": registro.detalhe or "",
        "linha": _primeira_linha(registro.titulo, registro.legenda),
        "imagem_url": registro.imagem_url or "",
        "aguarda_foto": bool((registro.cena or "").strip()) and not registro.imagem_url and not registro.video_url,
        "tem_video": bool(registro.video_url),
    }


async def _fila(session: AsyncSession, marca_id: int, hoje: date) -> tuple[dict | None, list[dict]]:
    resultado = await session.exec(
        select(Agendamento)
        .where(
            Agendamento.marca_id == marca_id,
            Agendamento.dia >= hoje.isoformat(),
            col(Agendamento.status).in_([*NA_FILA, "erro"]),
        )
        .order_by(Agendamento.dia, Agendamento.hora, Agendamento.id)
    )
    itens = list(resultado.all())
    proximo = next((item for item in itens if item.status in NA_FILA), None)
    erros = [item for item in itens if item.status == "erro"][:3]
    return (_post(proximo) if proximo else None), [_post(item) for item in erros]


def nome_tema(tema: str) -> str:
    return NOMES_TEMA.get(tema, tema[:1].upper() + tema[1:])


def dica_da_semana(medidos: list[tuple[int, Publicacao]]) -> str:
    """Uma frase simples, sem IA: o tipo de post (ou o formato) com o maior alcance médio."""
    por_tema: dict[str, list[int]] = defaultdict(list)
    por_formato: dict[str, list[int]] = defaultdict(list)
    for alcance, pub in medidos:
        if (pub.tema or "").strip():
            por_tema[pub.tema.strip()].append(alcance)
        por_formato["story" if pub.formato == "story" else "feed"].append(alcance)
    if len(por_tema) >= 2:
        melhor = max(por_tema, key=lambda tema: sum(por_tema[tema]) / len(por_tema[tema]))
        return f"Seus posts de {nome_tema(melhor)} alcançaram mais nesta semana."
    if len(por_formato) == 2:
        media = {nome: sum(valores) / len(valores) for nome, valores in por_formato.items()}
        if media["story"] > media["feed"]:
            return "Seus Stories alcançaram mais que os posts do Feed nesta semana."
        if media["feed"] > media["story"]:
            return "Seus posts do Feed alcançaram mais que os Stories nesta semana."
    if medidos:
        media_geral = round(sum(alcance for alcance, _pub in medidos) / len(medidos))
        pessoas = "pessoa" if media_geral == 1 else "pessoas"
        return f"Cada post alcançou {media_geral} {pessoas} em média. Marque o Tipo de post para ver qual funciona melhor."
    return MEDINDO


async def _semana(session: AsyncSession, marca_id: int) -> dict:
    resultado = await session.exec(
        select(Publicacao)
        .where(Publicacao.marca_id == marca_id, Publicacao.publicado_em >= agora() - timedelta(days=7))
        .order_by(col(Publicacao.publicado_em).desc())
    )
    pubs = list(resultado.all())
    medidos: list[tuple[int, Publicacao]] = []
    for pub in pubs:
        alcance = ler_metricas(pub.metricas).get("alcance")
        if isinstance(alcance, int):
            medidos.append((alcance, pub))
    melhor = None
    if medidos:
        numero, pub = max(medidos, key=lambda par: par[0])
        melhor = {"rede": pub.rede, "legenda": (pub.legenda or "")[:120], "permalink": pub.permalink or "", "numero": numero, "rotulo": "pessoas alcançadas"}
    elif pubs:
        pub = max(pubs, key=lambda item: _engajamento(ler_metricas(item.metricas)))
        numero = _engajamento(ler_metricas(pub.metricas))
        if numero > 0:
            melhor = {"rede": pub.rede, "legenda": (pub.legenda or "")[:120], "permalink": pub.permalink or "", "numero": numero, "rotulo": "interações"}
    contas_ligadas = bool(await _contas(session, marca_id))
    dica = dica_da_semana(medidos)
    if dica == MEDINDO and not pubs and not contas_ligadas:
        dica = SEM_CONTA
    return {
        "posts": len(pubs),
        "alcance_total": sum(valor for valor, _pub in medidos) if medidos else None,
        "medidos": len(medidos),
        "melhor": melhor,
        "dica": dica,
        "contas_ligadas": contas_ligadas,
    }


async def _clima_hoje(latitude: float | None, longitude: float | None, status: str, hoje: date) -> dict | None:
    """Lê a previsão guardada. Só busca no Google quando não há nada guardado, com espera curta."""
    if latitude is None or longitude is None:
        return None
    dias = clima_guardado(latitude, longitude)
    if dias is None:
        if not chave_clima() or (status or "ok") != "ok":
            return None
        try:
            dias, _status = await asyncio.wait_for(previsao(latitude, longitude), ESPERA_CLIMA)
        except Exception:
            return None
    for dia in dias:
        if dia.data == hoje:
            return {
                "maxima": round(dia.maxima) if dia.maxima is not None else None,
                "minima": round(dia.minima) if dia.minima is not None else None,
                "chuva": dia.chuva,
            }
    return None


async def montar_resumo(session: AsyncSession, marca_id: int) -> dict:
    """Junta o que o resumo do dia mostra. Cada parte falha sozinha, sem derrubar o resto."""
    momento = datetime.now(ZONA)
    hoje = momento.date()
    marca = await marca_por_id(session, marca_id)
    ident = (marca.id or 0) if marca else 0
    resposta: dict = {
        "marca_id": ident,
        "nome": ((marca.nome if marca else "") or "").strip() or "Risca Aí",
        "cidade": (marca.cidade or "").strip() if marca else "",
        "uf": (marca.uf or "").strip().upper() if marca else "",
        "hoje": hoje.isoformat(),
        "amanha": (hoje + timedelta(days=1)).isoformat(),
        "saudacao": saudacao(momento),
        "data_extenso": data_extenso(hoje),
    }
    if marca is None:
        return resposta
    if clima_volta_sozinho(marca.clima_status or ""):
        try:
            await garantir_sugestoes(session, marca)
        except Exception:
            await session.rollback()
            marca = await marca_por_id(session, marca_id)
            if marca is None:
                return resposta
    local = (marca.latitude, marca.longitude, marca.clima_status or "")
    try:
        resposta["proximo"], resposta["erros"] = await _fila(session, ident, hoje)
    except Exception:
        await session.rollback()
    try:
        resposta["semana"] = await _semana(session, ident)
    except Exception:
        await session.rollback()
    try:
        resposta["sugestoes"] = [
            {
                "id": item.id or 0,
                "dia_sugerido": item.dia_sugerido,
                "tipo": item.tipo,
                "titulo": item.titulo,
                "detalhe": item.detalhe,
                "data_evento": item.data_evento,
            }
            for item in (await listar_sugestoes(session, ident, hoje))[:4]
        ]
    except Exception:
        await session.rollback()
    try:
        resposta["clima"] = await _clima_hoje(*local, hoje)
    except Exception:
        pass
    return resposta
