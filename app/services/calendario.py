import asyncio
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.models import Ficha, Sugestao, agora
from app.services.calendario_dados import (
    ANIVERSARIO_CAPITAIS,
    COMEMORATIVAS,
    FERIADOS_ESTADUAIS,
    NACIONAIS_RESERVA,
    UFS,
)

ZONA = timezone(timedelta(hours=-3))
ALCANCES = ("cidade", "estado", "brasil")
UF_DO = {"AC", "AP", "AM", "CE", "ES", "MA", "PA", "PR", "PI", "RJ", "RN", "RS", "TO"}
UF_DA = {"BA", "PB"}
JANELA = 14
SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
CLIMA_BLOQUEADO = "Clima desligado: ative a Weather API no Google Cloud e libere na chave"
CLIMA_FORA = "Clima indisponível agora. Tento de novo mais tarde."
REPETIR_CLIMA_ROBO = 30 * 60
REPETIR_CLIMA_TELA = 10 * 60
AVISO_FIM = "Não invente horário de funcionamento, preço nem promoção; se precisar, peça para o dono confirmar."
AVISO_CONAR = (
    "Se citar bebida alcoólica, siga o CONAR: sem incentivar consumo excessivo, sem ligar a bebida a dirigir "
    "nem a menores de idade, e termine com \"Beba com moderação\"."
)
HORA_SEM_HOJE = 15
CALOR = 30
FRIO_MAXIMA = 18
CHUVA_CHANCE = 70
CHUVA_MM = 2
CHUVA_FORTE = {
    "RAIN",
    "LIGHT_TO_MODERATE_RAIN",
    "MODERATE_TO_HEAVY_RAIN",
    "HEAVY_RAIN",
    "RAIN_PERIODICALLY_HEAVY",
    "RAIN_SHOWERS",
    "HEAVY_RAIN_SHOWERS",
    "WIND_AND_RAIN",
    "THUNDERSTORM",
    "THUNDERSHOWER",
    "HEAVY_THUNDERSTORM",
    "LIGHT_THUNDERSTORM_RAIN",
    "SCATTERED_THUNDERSTORMS",
}
# Peso de segunda a domingo: para bar, sexta e sábado contam mais, depois quinta e domingo.
PESO_DIA = [1, 1, 1, 2, 3, 3, 2]

_feriados_ano: dict[int, list[tuple[date, str]]] = {}
_ibge: dict[tuple[str, str], str] = {}
_municipais: dict[tuple[str, int], list[tuple[date, str]]] = {}
_clima: dict[tuple[float, float], tuple[float, list["DiaClima"]]] = {}
_gerado: dict[int, str] = {}
_tentado: dict[int, float] = {}


class ErroHttp(Exception):
    def __init__(self, status: int, corpo: str) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.corpo = corpo


@dataclass
class Evento:
    data: date
    tipo: str
    nome: str
    antecipar: bool = True


@dataclass
class DiaClima:
    data: date
    maxima: float | None
    minima: float | None
    chuva: int | None
    chuva_noite: int | None = None
    mm: float | None = None
    condicao: str = ""


@dataclass
class Perfil:
    fornecedor: bool
    bar: bool
    comida: bool
    delivery: bool
    bebida: str


def hoje_local() -> date:
    return datetime.now(ZONA).date()


def alcance(marca: Ficha) -> str:
    """Até onde a marca vende: só a cidade dela, o estado todo ou o Brasil todo."""
    valor = (marca.abrangencia or "").strip().lower()
    return valor if valor in ALCANCES else "cidade"


def cidade_da_marca(marca: Ficha) -> str:
    return (marca.cidade or "").strip() if alcance(marca) == "cidade" else ""


def uf_da_marca(marca: Ficha) -> str:
    return (marca.uf or "").strip().upper() if alcance(marca) != "brasil" else ""


def no_estado(uf: str) -> str:
    if uf == "DF":
        return "no Distrito Federal"
    artigo = "do" if uf in UF_DO else ("da" if uf in UF_DA else "de")
    return f"no estado {artigo} {UFS.get(uf, uf)}"


def local_da_marca(marca: Ficha) -> str:
    modo = alcance(marca)
    if modo == "brasil":
        return "Brasil"
    uf = uf_da_marca(marca)
    if modo == "estado":
        return UFS.get(uf, uf)
    cidade = cidade_da_marca(marca)
    if cidade and uf:
        return f"{cidade} ({uf})"
    return cidade


def _pedir_json(url: str, metodo: str = "GET", corpo: dict | None = None, cabecalhos: dict | None = None) -> object:
    dados = json.dumps(corpo).encode() if corpo is not None else None
    pedido = urllib.request.Request(url, data=dados, method=metodo, headers={"User-Agent": "RiscaAiPosts/1.0"})
    if dados is not None:
        pedido.add_header("Content-Type", "application/json")
    for nome, valor in (cabecalhos or {}).items():
        pedido.add_header(nome, valor)
    try:
        with urllib.request.urlopen(pedido, timeout=20) as resposta:
            return json.loads(resposta.read().decode() or "null")
    except urllib.error.HTTPError as erro:
        raise ErroHttp(erro.code, erro.read().decode(errors="replace")) from erro


async def _json(url: str, metodo: str = "GET", corpo: dict | None = None, cabecalhos: dict | None = None) -> object:
    return await asyncio.to_thread(_pedir_json, url, metodo, corpo, cabecalhos)


def sem_acento(texto: str) -> str:
    base = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in base if unicodedata.category(c) != "Mn").lower().strip()


def pascoa(ano: int) -> date:
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = (h + l - 7 * m + 114) % 31 + 1
    return date(ano, mes, dia)


def _enesimo(ano: int, mes: int, dia_semana: int, n: int) -> date:
    primeiro = date(ano, mes, 1)
    return primeiro + timedelta(days=(dia_semana - primeiro.weekday()) % 7 + 7 * (n - 1))


def data_da_regra(regra: str, ano: int) -> date:
    if regra == "black_friday":
        return _enesimo(ano, 11, 3, 4) + timedelta(days=1)
    if regra.startswith("pascoa"):
        return pascoa(ano) + timedelta(days=int(regra[len("pascoa"):] or 0))
    partes = regra.split("-")
    if len(partes) == 3 and partes[1] in {"dom", "sex"}:
        return _enesimo(ano, int(partes[2]), 6 if partes[1] == "dom" else 4, int(partes[0]))
    base, _, extra = regra.partition("+")
    mes, dia = (int(x) for x in base.split("-"))
    data = date(ano, mes, dia)
    if extra == "domingo_seguinte" and data.weekday() != 6:
        data += timedelta(days=6 - data.weekday())
    return data


async def feriados_nacionais(ano: int) -> list[tuple[date, str]]:
    if ano in _feriados_ano:
        return _feriados_ano[ano]
    try:
        dados = await _json(f"https://brasilapi.com.br/api/feriados/v1/{ano}")
        lista = [
            (date.fromisoformat(str(item["date"])[:10]), str(item.get("name") or "Feriado"))
            for item in (dados if isinstance(dados, list) else [])
            if item.get("date")
        ]
    except Exception:
        lista = []
    if not lista:
        return [(data_da_regra(regra, ano), nome) for regra, nome in NACIONAIS_RESERVA]
    _feriados_ano[ano] = sorted(lista)
    return _feriados_ano[ano]


def feriados_estaduais(uf: str, ano: int) -> list[tuple[date, str]]:
    return [(data_da_regra(regra, ano), nome) for regra, nome in FERIADOS_ESTADUAIS.get(uf.upper(), [])]


def aniversario_capital(cidade: str, uf: str, ano: int) -> list[tuple[date, str]]:
    capital = ANIVERSARIO_CAPITAIS.get(uf.upper())
    if not capital or sem_acento(capital[0]) != sem_acento(cidade):
        return []
    return [(data_da_regra(capital[1], ano), f"Aniversário de {capital[0]}")]


async def codigo_ibge(cidade: str, uf: str) -> str:
    chave = (sem_acento(cidade), uf.upper())
    if chave in _ibge:
        return _ibge[chave]
    dados = await _json(
        f"https://brasilapi.com.br/api/ibge/municipios/v1/{uf.upper()}?providers=dados-abertos-br,gov,wikipedia"
    )
    for item in dados if isinstance(dados, list) else []:
        if sem_acento(str(item.get("nome") or "")) == chave[0]:
            _ibge[chave] = str(item.get("codigo_ibge") or "")
            return _ibge[chave]
    _ibge[chave] = ""
    return ""


def _data_br(valor: str) -> date | None:
    valor = (valor or "").strip()
    try:
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", valor):
            dia, mes, ano = (int(x) for x in valor.split("/"))
            return date(ano, mes, dia)
        return date.fromisoformat(valor[:10])
    except ValueError:
        return None


async def feriados_municipais(cidade: str, uf: str, ano: int) -> list[tuple[date, str]]:
    token = get_settings().feriados_api_token.strip()
    if not token or not cidade or not uf:
        return []
    try:
        ibge = await codigo_ibge(cidade, uf)
        if not ibge:
            return []
        if (ibge, ano) in _municipais:
            return _municipais[(ibge, ano)]
        dados = await _json(
            f"https://feriadosapi.com/api/v1/feriados/cidade/{ibge}?ano={ano}&limit=100",
            cabecalhos={"Authorization": f"Bearer {token}"},
        )
    except Exception:
        return []
    lista: list[tuple[date, str]] = []
    for item in (dados or {}).get("feriados", []) if isinstance(dados, dict) else []:
        if str(item.get("tipo") or "").upper() != "MUNICIPAL":
            continue
        data = _data_br(str(item.get("data") or ""))
        if data:
            lista.append((data, str(item.get("nome") or "Feriado municipal")))
    _municipais[(ibge, ano)] = lista
    return lista


def segmentos_da_marca(marca: Ficha) -> set[str]:
    texto = sem_acento(f"{marca.nome} {marca.publico} {marca.texto}")
    segmentos = {"geral"}
    if re.search(r"\b(bar|bares|boteco|botequim|adega|cerveja|chopp?|chope|drinks?|drinque|pub|cachaca|petiscaria)\b", texto):
        segmentos.add("bar")
    if re.search(r"\b(restaurante|pizzaria|pizza|hamburguer\w*|lanchonete|lanches?|comida|cozinha|marmita|petiscos?|petiscaria)\b", texto):
        segmentos.add("restaurante")
    return segmentos


def perfil_da_marca(marca: Ficha) -> Perfil:
    """Diz o ângulo dos posts: casa que atende o cliente final ou empresa que vende para o dono de bar."""
    texto = sem_acento(f"{marca.nome} {marca.publico} {marca.texto}")
    segmentos = segmentos_da_marca(marca)
    fornecedor = bool(
        re.search(r"\b(sistema|software|aplicativo|plataforma|app)\b", texto)
        and re.search(r"\bdonos? (de|do|da|dos|das) (bar|bares|adega|restaurante|petiscaria|lanchonete|boteco)", texto)
    )
    if re.search(r"\b(cervejas?|chopp?|chope|adega|boteco|botequim|bar|bares|pub)\b", texto):
        bebida = "Cerveja gelada"
    elif re.search(r"\b(drinks?|drinque|coquetel)\b", texto):
        bebida = "Drink gelado"
    else:
        bebida = ""
    return Perfil(
        fornecedor=fornecedor,
        bar="bar" in segmentos,
        comida="restaurante" in segmentos,
        delivery=bool(re.search(r"\b(delivery|entregas?|ifood|tele-?entrega|motoboy)\b", texto)),
        bebida=bebida,
    )


def aviso_da_marca(perfil: Perfil) -> str:
    return f"{AVISO_FIM} {AVISO_CONAR}" if perfil.bar or perfil.bebida else AVISO_FIM


async def eventos_do_periodo(marca: Ficha, inicio: date, fim: date) -> list[Evento]:
    cidade = cidade_da_marca(marca)
    uf = uf_da_marca(marca)
    segmentos = segmentos_da_marca(marca)
    eventos: list[Evento] = []
    for ano in sorted({inicio.year, fim.year}):
        for data, nome in await feriados_nacionais(ano):
            eventos.append(Evento(data, "feriado", nome))
        if uf:
            for data, nome in feriados_estaduais(uf, ano):
                eventos.append(Evento(data, "feriado", nome))
        if cidade and uf:
            for data, nome in aniversario_capital(cidade, uf, ano):
                eventos.append(Evento(data, "cidade", nome))
            for data, nome in await feriados_municipais(cidade, uf, ano):
                eventos.append(Evento(data, "feriado", nome))
        for item in COMEMORATIVAS:
            if not segmentos.intersection(item["segmentos"]):
                continue
            eventos.append(Evento(data_da_regra(item["regra"], ano), "data", item["nome"], item["antecipar"]))
    vistos: set[tuple[date, str]] = set()
    unicos: list[Evento] = []
    for evento in sorted(eventos, key=lambda e: (e.data, e.tipo != "feriado")):
        chave = (evento.data, sem_acento(evento.nome))
        if chave in vistos or not (inicio <= evento.data <= fim):
            continue
        vistos.add(chave)
        unicos.append(evento)
    return unicos


def chave_clima() -> str:
    ajustes = get_settings()
    return (ajustes.google_weather_api_key or ajustes.google_places_api_key).strip()


def _graus(bloco: dict | None) -> float | None:
    if not isinstance(bloco, dict) or bloco.get("degrees") is None:
        return None
    valor = float(bloco["degrees"])
    if str(bloco.get("unit") or "CELSIUS").upper() == "FAHRENHEIT":
        valor = (valor - 32) * 5 / 9
    return round(valor, 1)


def _chance(parte: dict | None) -> int | None:
    try:
        return int(parte["precipitation"]["probability"]["percent"])  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        return None


def _milimetros(parte: dict | None) -> float | None:
    try:
        qpf = parte["precipitation"]["qpf"]  # type: ignore[index]
        valor = float(qpf["quantity"])
    except (KeyError, TypeError, ValueError):
        return None
    if str(qpf.get("unit") or "MILLIMETERS").upper() == "INCHES":
        valor *= 25.4
    return round(valor, 1)


def _condicao(parte: dict | None) -> str:
    try:
        return str(parte["weatherCondition"]["type"] or "").upper()  # type: ignore[index]
    except (KeyError, TypeError):
        return ""


def ler_previsao(dados: dict) -> list[DiaClima]:
    """Usa a data local (displayDate) e a parte do dia (7h às 19h) para chuva; a noite vai até as 7h do dia seguinte."""
    dias: list[DiaClima] = []
    for item in dados.get("forecastDays", []) if isinstance(dados, dict) else []:
        exibida = item.get("displayDate") or {}
        try:
            data = date(int(exibida["year"]), int(exibida["month"]), int(exibida["day"]))
        except (KeyError, TypeError, ValueError):
            continue
        dia = item.get("daytimeForecast")
        dias.append(
            DiaClima(
                data=data,
                maxima=_graus(item.get("maxTemperature")),
                minima=_graus(item.get("minTemperature")),
                chuva=_chance(dia),
                chuva_noite=_chance(item.get("nighttimeForecast")),
                mm=_milimetros(dia),
                condicao=_condicao(dia),
            )
        )
    return dias


async def previsao(latitude: float, longitude: float) -> tuple[list[DiaClima], str]:
    chave = chave_clima()
    if not chave:
        return [], "Clima desligado: falta a chave do Google no servidor."
    lugar = (round(latitude, 3), round(longitude, 3))
    guardado = _clima.get(lugar)
    if guardado and time.time() - guardado[0] < 3 * 3600:
        return guardado[1], "ok"
    consulta = urllib.parse.urlencode(
        {
            "key": chave,
            "location.latitude": f"{latitude:.5f}",
            "location.longitude": f"{longitude:.5f}",
            "days": 10,
            "pageSize": 10,
        }
    )
    try:
        dados = await _json(f"https://weather.googleapis.com/v1/forecast/days:lookup?{consulta}")
    except ErroHttp as erro:
        corpo = erro.corpo.upper()
        bloqueado = erro.status in {401, 403} or any(
            marca in corpo for marca in ("PERMISSION_DENIED", "SERVICE_DISABLED", "API_KEY_SERVICE_BLOCKED", "NOT BEEN USED")
        )
        if bloqueado:
            return [], CLIMA_BLOQUEADO
        if erro.status in {400, 404}:
            return [], "Clima indisponível para essa cidade."
        return [], CLIMA_FORA
    except Exception:
        return [], CLIMA_FORA
    dias = ler_previsao(dados if isinstance(dados, dict) else {})
    _clima[lugar] = (time.time(), dias)
    return dias, "ok"


def clima_guardado(latitude: float, longitude: float) -> list[DiaClima] | None:
    guardado = _clima.get((round(latitude, 3), round(longitude, 3)))
    return guardado[1] if guardado else None


def sinal_do_dia(dia: DiaClima) -> tuple[str, str] | None:
    """Um sinal por dia, só o que muda o movimento: chuva de dia de verdade, calor ou dia frio (máxima baixa)."""
    if (
        dia.chuva is not None
        and dia.chuva >= CHUVA_CHANCE
        and ((dia.mm or 0) >= CHUVA_MM or dia.condicao in CHUVA_FORTE)
    ):
        return "chuva", f"Chuva {dia.chuva}%"
    if dia.maxima is not None and dia.maxima >= CALOR:
        return "calor", f"Calor {round(dia.maxima)} °C"
    if dia.maxima is not None and dia.maxima <= FRIO_MAXIMA:
        return "frio", f"Frio máx. {round(dia.maxima)} °C"
    return None


def sinais_do_clima(dias: list[DiaClima]) -> list[tuple[DiaClima, str, str]]:
    """Dias seguidos com o mesmo sinal viram uma sugestão só, no dia que mais pesa para bar (sexta e sábado primeiro)."""
    blocos: list[list[tuple[DiaClima, str, str]]] = []
    for dia in sorted(dias, key=lambda d: d.data):
        achado = sinal_do_dia(dia)
        if achado is None:
            continue
        anterior = blocos[-1][-1] if blocos else None
        if anterior and anterior[1] == achado[0] and (dia.data - anterior[0].data).days == 1:
            blocos[-1].append((dia, *achado))
        else:
            blocos.append([(dia, *achado)])
    return [
        max(bloco, key=lambda item: (PESO_DIA[item[0].data.weekday()], -item[0].data.toordinal()))
        for bloco in blocos
    ]


def _dm(data: date) -> str:
    return data.strftime("%d/%m")


def _onde(marca: Ficha, tipo: str = "", nome: str = "") -> str:
    modo = alcance(marca)
    if modo == "brasil":
        return " em todo o Brasil" if tipo == "feriado" and "brasil" not in sem_acento(nome) else ""
    uf = uf_da_marca(marca)
    if modo == "estado":
        return f" {no_estado(uf)}" if uf else ""
    cidade = cidade_da_marca(marca)
    if cidade and uf:
        return f" em {cidade} ({uf})"
    if cidade:
        return f" em {cidade}"
    return ""


def no_dia(data: date) -> str:
    semana = SEMANA[data.weekday()]
    return f"no {semana}" if data.weekday() >= 5 else f"na {semana}"


def chave_perfil(perfil: Perfil) -> str:
    if perfil.fornecedor:
        return "fornecedor"
    return "casa" if perfil.bar or perfil.comida else "geral"


ROTULO_TIPO = {"feriado": "Feriado", "data": "Data", "cidade": "Cidade"}
ANGULO = {
    ("calor", "fornecedor"): "dica para o dono de bar se preparar para o calor (bebida gelada no estoque, gelo, movimento maior), mostrando como a marca ajuda",
    ("calor", "geral"): "algo refrescante ligado ao que a marca faz",
    ("frio", "fornecedor"): "dica para o dono de bar aproveitar o frio (petiscos e pratos quentes, caldos), mostrando como a marca ajuda",
    ("frio", "casa"): "petisco ou prato quentinho e ambiente aconchegante",
    ("frio", "geral"): "aconchego para o dia frio",
    ("chuva", "fornecedor"): "dica para o dono de bar manter o movimento num dia de chuva, sem prometer resultado",
    ("chuva", "delivery"): "chamar para pedir no delivery e ficar em casa",
    ("chuva", "casa"): "salão aconchegante, um bom lugar para esperar a chuva passar",
    ("chuva", "geral"): "conforto e aconchego no dia de chuva",
    ("feriado", "fornecedor"): "dica para o dono de bar se preparar para o feriado (estoque, equipe, caixa)",
    ("feriado", "casa"): "avisar se a casa abre ou fecha no feriado; deixe [horário] para o dono completar",
    ("feriado", "geral"): "avisar se a marca atende no feriado; deixe [horário] para o dono completar",
    ("data", "fornecedor"): "ideia para o dono de bar aproveitar a data (decoração, drink ou petisco temático), mostrando como a marca ajuda",
    ("data", "casa"): "como a casa entra no clima da data, com um produto real do cardápio",
}


def angulo(assunto: str, perfil: Perfil) -> str:
    chave = chave_perfil(perfil)
    if assunto == "chuva" and perfil.delivery and chave != "fornecedor":
        chave = "delivery"
    if assunto == "calor" and chave == "casa":
        bebida = perfil.bebida.lower() if perfil.bebida else "algo gelado"
        return f"{bebida} ou algo refrescante do cardápio para enfrentar o calor"
    return ANGULO.get((assunto, chave), "")


def frase_clima(dia: DiaClima, sinal: str, onde: str) -> str:
    quando = f"{no_dia(dia.data)} {_dm(dia.data)}"
    if sinal == "calor":
        return f"Previsão do tempo{onde}: máxima de {round(dia.maxima or 0)} °C {quando}."
    if sinal == "frio":
        return f"Previsão do tempo{onde}: dia frio {quando}, máxima de só {round(dia.maxima or 0)} °C."
    volume = f", cerca de {dia.mm:g} mm".replace(".", ",") if dia.mm else ""
    return f"Previsão do tempo{onde}: {dia.chuva}% de chance de chuva durante o dia {quando}{volume}."


def montar_sugestoes(
    marca: Ficha, eventos: list[Evento], clima: list[DiaClima], hoje: date, hora: int = 0
) -> list[dict]:
    ultimo = hoje + timedelta(days=JANELA - 1)
    onde = _onde(marca)
    perfil = perfil_da_marca(marca)
    aviso = aviso_da_marca(perfil)
    itens: list[dict] = []

    def adicionar(dia: date, tipo: str, titulo: str, detalhe: str, evento: date) -> None:
        if hoje <= dia <= ultimo:
            itens.append(
                {
                    "dia_sugerido": dia.isoformat(),
                    "tipo": tipo,
                    "titulo": titulo[:160],
                    "detalhe": detalhe,
                    "data_evento": evento.isoformat(),
                }
            )

    for evento in eventos:
        nome_tipo = "Feriado" if evento.tipo == "feriado" else ("Aniversário da cidade" if evento.tipo == "cidade" else "Data")
        titulo = f"{ROTULO_TIPO[evento.tipo]} · {evento.nome} · {_dm(evento.data)}"
        semana_evento = SEMANA[evento.data.weekday()]
        base = f"{nome_tipo}: {evento.nome}{_onde(marca, evento.tipo, evento.nome)}, {semana_evento} {_dm(evento.data)}."
        jeito = angulo(evento.tipo, perfil)
        extra = f" Ângulo: {jeito}." if jeito else ""
        if evento.antecipar or evento.tipo == "feriado":
            antes = evento.data - timedelta(days=3)
            if antes < hoje and (evento.data - hoje).days >= 2:
                antes = hoje
            if antes < evento.data:
                adicionar(
                    antes,
                    evento.tipo,
                    "Vem aí · " + titulo,
                    f"{base} Faça um post para o dia {_dm(antes)} avisando que a data está chegando.{extra} {aviso}",
                    evento.data,
                )
        adicionar(
            evento.data,
            evento.tipo,
            titulo,
            f"{base} Faça um post para o próprio dia {_dm(evento.data)}.{extra} {aviso}",
            evento.data,
        )
    primeiro = hoje if hora < HORA_SEM_HOJE else hoje + timedelta(days=1)
    for dia, sinal, rotulo in sinais_do_clima([d for d in clima if d.data >= primeiro]):
        quando = max(hoje, dia.data - timedelta(days=1))
        adicionar(
            quando,
            "clima",
            f"{rotulo} · {SEMANA[dia.data.weekday()]}",
            (
                f"{frase_clima(dia, sinal, onde)} Faça um post para o dia {_dm(quando)}. "
                f"Ângulo: {angulo(sinal, perfil)}. Não prometa o tempo como certo. {aviso}"
            ),
            dia.data,
        )
    return itens


async def calcular(marca: Ficha, hoje: date | None = None) -> tuple[list[dict], str]:
    momento = datetime.now(ZONA)
    hoje = hoje or momento.date()
    hora = momento.hour if hoje == momento.date() else 0
    eventos = await eventos_do_periodo(marca, hoje, hoje + timedelta(days=JANELA + 5))
    clima: list[DiaClima] = []
    if alcance(marca) != "cidade":
        status = ""
    elif marca.latitude is not None and marca.longitude is not None:
        clima, status = await previsao(marca.latitude, marca.longitude)
    elif (marca.cidade or "").strip():
        status = "Clima desligado: a localização da cidade não foi encontrada. Salve a marca de novo com Cidade e Estado."
    else:
        status = "Clima desligado: preencha Cidade e Estado em Minha marca."
    return montar_sugestoes(marca, eventos, clima, hoje, hora), status


async def gerar_sugestoes(session: AsyncSession, marca: Ficha, hoje: date | None = None) -> int:
    hoje = hoje or hoje_local()
    marca_id = marca.id or 0
    _tentado[marca_id] = time.time()
    itens, status = await calcular(marca, hoje)
    if (marca.clima_status or "") != status[:300]:
        marca.clima_status = status[:300]
        session.add(marca)
    if itens:
        novas = insert(Sugestao).values(
            [{**item, "marca_id": marca_id, "status": "nova", "criado_em": agora()} for item in itens]
        )
        await session.exec(
            novas.on_conflict_do_update(
                constraint="ux_mkt_sugestoes_chave",
                set_={"detalhe": novas.excluded.detalhe, "data_evento": novas.excluded.data_evento},
            )
        )
    chaves = {(item["dia_sugerido"], item["tipo"], item["titulo"]) for item in itens}
    abertas = await session.exec(
        select(Sugestao).where(
            Sugestao.marca_id == marca_id,
            Sugestao.status == "nova",
            Sugestao.dia_sugerido >= hoje.isoformat(),
        )
    )
    for velha in abertas.all():
        if (velha.dia_sugerido, velha.tipo, velha.titulo) not in chaves:
            await session.delete(velha)
    await session.exec(
        delete(Sugestao).where(
            Sugestao.marca_id == marca_id,
            Sugestao.dia_sugerido < (hoje - timedelta(days=30)).isoformat(),
        )
    )
    await session.commit()
    _gerado[marca_id] = hoje.isoformat()
    return len(itens)


def clima_volta_sozinho(status: str) -> bool:
    """Erros que somem sem o dono mexer na marca (API recém-ligada no Google, Google fora do ar)."""
    return status.startswith(CLIMA_BLOQUEADO) or status.startswith(CLIMA_FORA)


def precisa_gerar(marca: Ficha, espera_clima: float) -> bool:
    marca_id = marca.id or 0
    if _gerado.get(marca_id) != hoje_local().isoformat():
        return True
    if not clima_volta_sozinho(marca.clima_status or ""):
        return False
    return time.time() - _tentado.get(marca_id, 0.0) >= espera_clima


async def garantir_sugestoes(session: AsyncSession, marca: Ficha, forcar: bool = False) -> None:
    if forcar or precisa_gerar(marca, REPETIR_CLIMA_TELA):
        await gerar_sugestoes(session, marca)


async def sugestoes_de_todas(session: AsyncSession) -> None:
    ids = await session.exec(select(Ficha.id).order_by(Ficha.id))
    for marca_id in [item for item in ids.all() if item]:
        try:
            marca = await session.get(Ficha, marca_id)
            if marca is not None and precisa_gerar(marca, REPETIR_CLIMA_ROBO):
                await gerar_sugestoes(session, marca)
        except asyncio.CancelledError:
            raise
        except Exception:
            await session.rollback()


async def listar_sugestoes(session: AsyncSession, marca_id: int, hoje: date | None = None) -> list[Sugestao]:
    momento = datetime.now(ZONA)
    hoje = hoje or momento.date()
    tarde = hoje == momento.date() and momento.hour >= HORA_SEM_HOJE
    resultado = await session.exec(
        select(Sugestao)
        .where(
            Sugestao.marca_id == marca_id,
            Sugestao.status == "nova",
            Sugestao.dia_sugerido >= hoje.isoformat(),
            Sugestao.dia_sugerido <= (hoje + timedelta(days=JANELA - 1)).isoformat(),
        )
        .order_by(col(Sugestao.dia_sugerido), col(Sugestao.data_evento), col(Sugestao.id))
    )
    return [
        item
        for item in resultado.all()
        if not (tarde and item.tipo == "clima" and item.data_evento == hoje.isoformat())
    ]


async def mudar_status(session: AsyncSession, sugestao_id: int, marca_id: int, status: str) -> bool:
    registro = await session.get(Sugestao, sugestao_id)
    if registro is None or (marca_id and registro.marca_id != marca_id):
        return False
    registro.status = status
    await session.commit()
    return True


def _pedir_local(cidade: str, uf: str, chave: str) -> object:
    return _pedir_json(
        "https://places.googleapis.com/v1/places:searchText",
        "POST",
        {"textQuery": f"{cidade}, {uf}, Brasil", "languageCode": "pt-BR", "regionCode": "BR"},
        {"X-Goog-Api-Key": chave, "X-Goog-FieldMask": "places.location"},
    )


async def localizar(cidade: str, uf: str) -> tuple[float | None, float | None, str]:
    cidade = cidade.strip()
    uf = uf.strip().upper()
    if not cidade:
        return None, None, ""
    chave = get_settings().google_places_api_key.strip()
    if not chave:
        return None, None, "Cidade salva. A localização não foi buscada porque falta a chave do Google no servidor; o clima fica desligado."
    try:
        dados = await asyncio.to_thread(_pedir_local, cidade, uf, chave)
        local = (dados or {}).get("places", [{}])[0].get("location") or {}  # type: ignore[union-attr]
        return float(local["latitude"]), float(local["longitude"]), ""
    except Exception:
        return None, None, "Cidade salva, mas não achei a localização dela no Google. Confira o nome e o estado; o clima fica desligado até lá."


async def ajustar_local(
    session: AsyncSession, marca: Ficha, cidade: str, uf: str, abrangencia: str = "cidade"
) -> str:
    """No alcance estado ou Brasil a cidade guardada fica para quando o dono voltar para Minha cidade."""
    modo = abrangencia if abrangencia in ALCANCES else "cidade"
    cidade = " ".join((cidade or "").split())[:120]
    uf = uf_valida(uf)
    mudou = modo != alcance(marca)
    marca.abrangencia = modo
    aviso = ""
    if modo == "cidade":
        mudou_local = cidade != (marca.cidade or "") or uf != (marca.uf or "")
        mudou = mudou or mudou_local
        marca.cidade = cidade
        marca.uf = uf
        if not cidade:
            marca.latitude = None
            marca.longitude = None
        elif mudou_local or marca.latitude is None or marca.longitude is None:
            marca.latitude, marca.longitude, aviso = await localizar(cidade, uf)
    elif modo == "estado" and uf != (marca.uf or ""):
        marca.uf = uf
        marca.cidade = ""
        marca.latitude = None
        marca.longitude = None
        mudou = True
    if mudou:
        marca.clima_status = ""
    session.add(marca)
    await session.commit()
    await session.refresh(marca)
    _gerado.pop(marca.id or 0, None)
    return aviso


def aviso_local(marca: Ficha) -> str:
    if cidade_da_marca(marca) and (marca.latitude is None or marca.longitude is None):
        return "A localização desta cidade ainda não foi encontrada; o clima fica desligado para esta marca."
    return ""


def uf_valida(uf: str) -> str:
    sigla = (uf or "").strip().upper()
    if sigla and sigla not in UFS:
        raise ValueError("Escreva a sigla do estado com 2 letras, por exemplo SP.")
    return sigla
