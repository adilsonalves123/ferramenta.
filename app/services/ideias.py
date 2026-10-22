"""Ideias rápidas do resumo do dia, montadas sem IA: calendário, clima e resultados da própria marca."""
import re
from collections import defaultdict
from datetime import date, timedelta

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Ficha, Publicacao, Sugestao, agora
from app.services.calendario import (
    SEMANA,
    ZONA,
    Perfil,
    aviso_da_marca,
    chave_perfil,
    cidade_da_marca,
    no_dia,
    perfil_da_marca,
)
from app.services.memoria import nomes_de_item
from app.services.resultados import ler_metricas

NOMES_TEMA = {
    "bastidores": "Bastidores",
    "oferta": "Oferta",
    "dica": "Dica",
    "história": "História",
    "prova": "Prova",
    "interação": "Interação",
}
MAX_IDEIAS = 4
MAX_CALENDARIO = 3
MIN_IDEIAS = 3
SEM_POST_DIAS = 7


def nome_tema(tema: str) -> str:
    return NOMES_TEMA.get(tema, tema[:1].upper() + tema[1:])


def _dm(data: date) -> str:
    return data.strftime("%d/%m")


def _quando(data: date, hoje: date) -> tuple[str, str]:
    """Devolve ('de sábado', 'no sábado'), trocando por hoje/amanhã quando for o caso."""
    if data == hoje:
        return "de hoje", "hoje"
    if data == hoje + timedelta(days=1):
        return "de amanhã", "amanhã"
    return f"de {SEMANA[data.weekday()]}", no_dia(data)


def rotulo_clima(sinal: str, numero: int | None, data: date, hoje: date, item: str) -> str:
    de, na = _quando(data, hoje)
    if sinal == "calor":
        graus = f" ({numero}°C)" if numero is not None else ""
        return f"Calor {de}{graus}: {item}"
    if sinal == "frio":
        return f"Frio {de}: {item}"
    return f"Chuva {na}: {item}"


def rotulo_evento(tipo: str, nome: str, data: date, item: str) -> str:
    if tipo != "feriado":
        return f"{nome} ({_dm(data)}): {item}"
    titulo = nome if sem_feriado(nome) else f"Feriado de {nome}"
    return f"{titulo} ({_dm(data)}): {item}"


def sem_feriado(nome: str) -> bool:
    return nome.lower().startswith(("dia ", "feriado"))


def _nome_evento(titulo: str) -> str:
    partes = re.sub(r"^Vem aí · ", "", titulo).split(" · ")
    return partes[1] if len(partes) >= 3 else partes[0]


def _numero(titulo: str) -> int | None:
    achado = re.search(r"(\d+)\s*(°C|%)", titulo)
    return int(achado.group(1)) if achado else None


def _motivo(detalhe: str, dia_sugerido: date, hoje: date) -> str:
    frase = detalhe.split(". ")[0].rstrip(".")
    quando = "hoje" if dia_sugerido == hoje else f"{SEMANA[dia_sugerido.weekday()]} {_dm(dia_sugerido)}"
    return f"{frase}. Post sugerido para {quando}."


def ideias_do_calendario(sugestoes: list[Sugestao], hoje: date, nomes: list[str]) -> list[dict]:
    """Uma ideia por data (o aviso 'Vem aí' e o próprio dia viram uma só), na ordem do dia do post."""
    if not nomes:
        return []
    grupos: dict[tuple[str, str, str], list[Sugestao]] = {}
    for item in sorted(sugestoes, key=lambda s: (s.dia_sugerido, s.data_evento, s.id or 0)):
        nome = item.titulo.split(" · ")[0].split(" ")[0] if item.tipo == "clima" else _nome_evento(item.titulo)
        grupos.setdefault((item.tipo, item.data_evento, nome), []).append(item)
    ideias: list[dict] = []
    for (tipo, data_evento, nome), grupo in grupos.items():
        primeiro = grupo[0]
        try:
            data = date.fromisoformat(data_evento)
            dia = date.fromisoformat(primeiro.dia_sugerido)
        except ValueError:
            continue
        item = nomes[len(ideias) % len(nomes)]
        if tipo == "clima":
            sinal = nome.lower()
            if sinal not in {"calor", "frio", "chuva"}:
                continue
            rotulo = rotulo_clima(sinal, _numero(primeiro.titulo), data, hoje, item)
            tipo_ideia = sinal
        else:
            rotulo = rotulo_evento(tipo, nome, data, item)
            tipo_ideia = tipo
        ideias.append(
            {
                "tipo": tipo_ideia,
                "rotulo": rotulo,
                "motivo": _motivo(primeiro.detalhe or primeiro.titulo, dia, hoje),
                "pedido": f"{primeiro.detalhe or primeiro.titulo} O post nomeia {item}.",
                "tema": "",
                "dia": primeiro.dia_sugerido,
                "sugestoes": [registro.id or 0 for registro in grupo],
            }
        )
    return ideias[:MAX_CALENDARIO]


def ideia_dos_resultados(pubs: list[Publicacao], hoje: date, aviso: str, item: str) -> dict | None:
    """Tipo de post com o maior alcance médio em 30 dias; sem isso, o tipo mais usado que está parado há dias."""
    desde = agora() - timedelta(days=30)
    alcances: dict[str, list[int]] = defaultdict(list)
    usos: dict[str, list[date]] = defaultdict(list)
    for pub in pubs:
        tema = (pub.tema or "").strip()
        if not tema:
            continue
        usos[tema].append(pub.publicado_em.astimezone(ZONA).date())
        alcance = ler_metricas(pub.metricas).get("alcance")
        if isinstance(alcance, int) and pub.publicado_em >= desde:
            alcances[tema].append(alcance)
    medias = sorted(((sum(v) / len(v), tema) for tema, v in alcances.items()), reverse=True)
    if len(medias) >= 2 and medias[0][0] > medias[1][0]:
        media, tema = medias[0]
        nome = nome_tema(tema)
        motivo = f"Nos últimos 30 dias, os posts de {nome} alcançaram em média {round(media)} pessoas, mais que os outros tipos."
        return {
            "tipo": "resultado",
            "rotulo": f"{item}: mais um post de {nome}",
            "motivo": motivo,
            "pedido": f"Crie um post do tipo {nome} sobre {item}. {motivo} {aviso}",
            "tema": tema,
            "dia": "",
            "sugestoes": [],
        }
    frequentes = sorted(((len(dias), max(dias), tema) for tema, dias in usos.items() if len(dias) >= 2), reverse=True)
    if frequentes:
        _quantos, ultimo, tema = frequentes[0]
        parado = (hoje - ultimo).days
        if parado >= SEM_POST_DIAS:
            nome = nome_tema(tema)
            motivo = f"{nome} é o tipo de post que você mais usa, e o último saiu em {_dm(ultimo)}."
            return {
                "tipo": "resultado",
                "rotulo": f"{item}: faz {parado} dias sem post de {nome}",
                "motivo": motivo,
                "pedido": f"Crie um post do tipo {nome} sobre {item}. {motivo} {aviso}",
                "tema": tema,
                "dia": "",
                "sugestoes": [],
            }
    return None


def ideia_de_convite(marca: Ficha, perfil: Perfil, hoje: date, aviso: str, item: str) -> dict:
    """Reserva quando não há data nem resultado: o próximo dia de mais movimento, com um item real."""
    alvo = hoje if hoje.weekday() in (4, 5) else hoje + timedelta(days=(4 - hoje.weekday()) % 7)
    quando = f"{no_dia(alvo)} {_dm(alvo)}"
    chave = chave_perfil(perfil)
    if chave == "casa":
        cidade = cidade_da_marca(marca)
        onde = f" de {cidade}" if cidade else ""
        artigo = "o" if alvo.weekday() >= 5 else "a"
        rotulo = f"{item} hoje à noite" if alvo == hoje else f"{item} n{artigo} {SEMANA[alvo.weekday()]} ({_dm(alvo)})"
        motivo = f"Sem data especial nem resultado para comparar ainda. Convite com {item} para {quando}."
        pedido = (
            f"Crie um post convidando os clientes{onde} para {quando}, falando de {item}. "
            f"Se precisar de preço, deixe [preço] para eu completar. {aviso}"
        )
        tema = "oferta"
    elif chave == "fornecedor":
        rotulo = f"{item} n{('o' if alvo.weekday() >= 5 else 'a')} {SEMANA[alvo.weekday()]} ({_dm(alvo)})"
        motivo = f"O fim de semana costuma ter mais movimento. O post fala de {item} para {quando}."
        pedido = f"Crie um post sobre {item} para o movimento {quando}, mostrando como a marca ajuda nesse dia. {aviso}"
        tema = "dica"
    else:
        rotulo = f"Pergunta sobre {item}"
        motivo = f"Sem data especial nem resultado para comparar ainda. A pergunta é sobre {item}."
        pedido = f"Crie um post com uma pergunta simples sobre {item} para os clientes responderem nos comentários. {aviso}"
        tema = "interação"
    return {
        "tipo": "convite",
        "rotulo": rotulo,
        "motivo": motivo,
        "pedido": pedido,
        "tema": tema,
        "dia": alvo.isoformat(),
        "sugestoes": [],
    }


def pergunta_de_item() -> dict:
    return {
        "tipo": "pergunta",
        "rotulo": "Qual produto entra no post de hoje?",
        "motivo": "A memória ainda não tem um item. Sem isso, a ideia sai genérica.",
        "pedido": "",
        "tema": "",
        "dia": "",
        "sugestoes": [],
    }


async def publicacoes_recentes(session: AsyncSession, marca_id: int) -> list[Publicacao]:
    resultado = await session.exec(
        select(Publicacao).where(Publicacao.marca_id == marca_id, Publicacao.publicado_em >= agora() - timedelta(days=90))
    )
    return list(resultado.all())


def montar_ideias(marca: Ficha, sugestoes: list[Sugestao], pubs: list[Publicacao], hoje: date) -> list[dict]:
    nomes = nomes_de_item(marca)
    if not nomes:
        return [pergunta_de_item()]
    perfil = perfil_da_marca(marca)
    aviso = aviso_da_marca(perfil)
    ideias = ideias_do_calendario(sugestoes, hoje, nomes)
    dado = ideia_dos_resultados(pubs, hoje, aviso, nomes[len(ideias) % len(nomes)])
    if dado:
        ideias.append(dado)
    if len(ideias) < MIN_IDEIAS:
        ideias.append(ideia_de_convite(marca, perfil, hoje, aviso, nomes[len(ideias) % len(nomes)]))
    return ideias[:MAX_IDEIAS]
