"""Memória da marca: os fatos que o dono escreveu e as fotos dele, lidas pela IA e usadas nos posts."""

import asyncio
import base64
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import update
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Agendamento, Ficha, MemoriaFoto
from app.services import arte
from app.services.imagens import achar_variante, guardar_figura, url_memoria
from app.services.openai_campanha import cliente_openai

LIMITE_FOTOS = 60
TIPOS = ("auto", "produto", "servico", "local", "pessoas", "bastidores", "captura", "arte", "outro")
TIPOS_IA = ("produto", "servico", "local", "pessoas", "bastidores", "captura", "arte", "outro")
NOMES_TIPOS = {
    "auto": "não definido",
    "produto": "produto",
    "servico": "serviço",
    "local": "local / ambiente",
    "pessoas": "pessoas / equipe",
    "bastidores": "bastidores",
    "captura": "captura de tela",
    "arte": "logo ou arte",
    "outro": "outro",
}
TIPOS_ANTIGOS = {"print do app": "captura", "prato": "produto", "fachada": "local", "ambiente": "local", "equipe": "pessoas"}
TIPOS_LUGAR = {"auto", "produto", "servico", "local", "pessoas", "bastidores"}
CAPTURA = re.compile(r"\b(captura de tela|print de tela|screenshot)\b", re.IGNORECASE)
PAPEIS = ("auto", "postar", "base", "estilo")
NOMES_PAPEIS = {
    "auto": "a IA decide (postar como está, editar ou só estilo)",
    "postar": "pode postar como está",
    "base": "base para editar",
    "estilo": "só inspiração de estilo",
}
CTA_STORY = "Responda QUERO"
DIAS_SEMANA = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")

CAMPOS_FATOS = (
    ("oferta", "O que você vende ou faz"),
    ("itens", "Produtos ou serviços (com dia e horário quando houver)"),
    ("horarios", "Horários de funcionamento"),
    ("diferenciais", "Diferenciais"),
    ("promocoes", "Promoções fixas"),
    ("tom", "Tom de voz"),
    ("nunca", "Nunca dizer"),
)

REGRAS_MEMORIA = """

Memória da marca: leia a MEMÓRIA DA MARCA antes de escrever. O dono escreveu esses fatos e é a única fonte de verdade.
- Use só fatos da memória e da ficha. Não invente produto, serviço, preço, desconto, horário, endereço, telefone nem depoimento.
- Não copie as descrições ao pé da letra: transforme o fato em benefício concreto para o cliente, com palavras suas. Nada genérico ("qualidade e excelência", "o melhor da cidade").
- angulo: diga em poucas palavras o ângulo concreto do post (um produto, um serviço, um diferencial, um momento do dia). Varie o ângulo entre os posts e em relação aos posts recentes.
- Itens com dia ou horário (ex.: "Produto da semana — sábado") só aparecem como oferta do dia no dia certo do post. Em outro dia não prometa esse item para hoje.
- A foto do post sai do plano_arte (foto_base_id e fotos_estilo_ids). Se o post fala de algo que tem foto na memória, use essa foto como base. Use só ids da lista.
- Se a foto escolhida for captura de tela, a legenda explica o que aparece nela e por que importa para o cliente, sem citar valores de exemplo da tela.
- Respeite o "Tom de voz" e tudo que está em "Nunca dizer"."""

CAMPOS_SEM_MEMORIA = """
angulo: o ângulo concreto do post, em poucas palavras.
gancho: de 3 a 7 palavras em português, fortes e claras, para escrever em cima da imagem. Sem hashtag, sem emoji, sem preço inventado.
Esta marca ainda não tem fotos na memória: no plano_arte, foto_base_id é null e fotos_estilo_ids é []."""

CAMPO_GANCHO = """
gancho: de 3 a 7 palavras em português, fortes e claras, para escrever em cima da imagem. Sem hashtag, sem emoji, sem preço inventado."""

SISTEMA_VISAO = """Você descreve uma foto que o dono de um negócio guardou na memória da marca. Uma IA de posts vai usar a descrição depois. Português do Brasil.
descricao: 2 ou 3 frases concretas e objetivas sobre o que aparece.
- Descreva só o que está visível: o assunto principal, o lugar, as pessoas (o que estão fazendo, sem inventar nome), as cores, a luz e o enquadramento.
- Não suponha o ramo do negócio nem para que a coisa serve se isso não estiver claro na imagem.
- Texto legível (placa, embalagem, cartaz, tela): diga do que se trata, sem copiar valores em dinheiro.
- Captura de tela: diga qual tela ou página é, pelos títulos e rótulos visíveis.
O texto sobre a marca serve apenas para entender o assunto; nunca use esse texto para inventar objeto, pessoa ou detalhe que não aparece.
Se a imagem for lisa, borrada, escura ou sem nada reconhecível, diga isso em uma frase (ex.: "Imagem de cor laranja lisa, sem objetos nem texto.") e use tipo outro.
Valores e nomes de exemplo numa tela ou etiqueta não são preço nem cliente real: não repita valores em dinheiro.
tags: de 3 a 8 palavras-chave curtas, em minúsculas, só do que aparece.
tipo, um destes:
- produto: um item à venda em destaque;
- servico: um serviço sendo feito ou o resultado dele;
- local: fachada ou ambiente, sem foco em pessoas;
- pessoas: pessoas ou equipe em foco;
- bastidores: preparo, rotina de trabalho, ferramentas ou materiais;
- captura: só se a imagem for mesmo uma captura de tela com interface visível;
- arte: logo, cartaz ou arte gráfica pronta;
- outro: quando nada disso combina."""


class AnaliseFoto(BaseModel):
    descricao: str
    tags: list[str]
    tipo: Literal["produto", "servico", "local", "pessoas", "bastidores", "captura", "arte", "outro"]


def normalizar_tipo(valor: str | None) -> str:
    """Tipo guardado (inclusive os nomes antigos) no tipo atual; vazio ou desconhecido vira 'auto'."""
    tipo = (valor or "").strip().lower()
    tipo = TIPOS_ANTIGOS.get(tipo, tipo)
    return tipo if tipo in TIPOS else "auto"


def normalizar_papel(valor: str | None) -> str:
    papel = (valor or "").strip().lower()
    return papel if papel in PAPEIS else "auto"


def tipo_efetivo(tipo: str | None, descricao: str | None = "", tags: str | None = "") -> str:
    """O tipo que a IA usa: em 'auto', reconhece captura de tela pela descrição."""
    atual = normalizar_tipo(tipo)
    if atual == "auto" and CAPTURA.search(f"{descricao or ''} {tags or ''}"):
        return "captura"
    return atual


def e_captura(foto) -> bool:
    return tipo_efetivo(foto.tipo, foto.descricao, foto.tags) == "captura"


@dataclass
class ContextoMemoria:
    """O que a IA lê da marca. 'usaveis' são as fotos que podem aparecer no post (postar e base);
    'estilo' são as que só inspiram (nunca aparecem)."""

    texto: str = ""
    fatos: str = ""
    usaveis: set[int] = field(default_factory=set)
    lugar: set[int] = field(default_factory=set)
    estilo: set[int] = field(default_factory=set)
    tipos: dict[int, str] = field(default_factory=dict)
    papeis: dict[int, str] = field(default_factory=dict)

    @property
    def tem_memoria(self) -> bool:
        return bool(self.texto.strip())

    def regras(self) -> str:
        return REGRAS_MEMORIA + CAMPO_GANCHO if self.tem_memoria else CAMPOS_SEM_MEMORIA


def ler_fatos(ficha: Ficha | None) -> dict[str, str]:
    vazio = {chave: "" for chave, _rotulo in CAMPOS_FATOS}
    if ficha is None or not (ficha.memoria or "").strip():
        return vazio
    try:
        dados = json.loads(ficha.memoria)
    except ValueError:
        return vazio
    if not isinstance(dados, dict):
        return vazio
    return {chave: str(dados.get(chave) or "").strip() for chave in vazio}


def gravar_fatos(ficha: Ficha, fatos: dict[str, str]) -> None:
    limpos = {chave: str(fatos.get(chave) or "").strip() for chave, _rotulo in CAMPOS_FATOS}
    ficha.memoria = json.dumps(limpos, ensure_ascii=False) if any(limpos.values()) else ""


def _colunas_lista():
    return (
        MemoriaFoto.id,
        MemoriaFoto.marca_id,
        MemoriaFoto.largura,
        MemoriaFoto.altura,
        MemoriaFoto.descricao,
        MemoriaFoto.tags,
        MemoriaFoto.tipo,
        MemoriaFoto.papel,
        MemoriaFoto.usar_em_posts,
    )


async def listar_fotos(session: AsyncSession, marca_id: int, so_usaveis: bool = False) -> list:
    consulta = select(*_colunas_lista()).where(MemoriaFoto.marca_id == marca_id)
    if so_usaveis:
        consulta = consulta.where(MemoriaFoto.usar_em_posts == True)  # noqa: E712
    resultado = await session.exec(consulta.order_by(MemoriaFoto.id))
    return list(resultado.all())


async def contar_fotos(session: AsyncSession, marca_id: int) -> int:
    resultado = await session.exec(select(func.count()).select_from(MemoriaFoto).where(MemoriaFoto.marca_id == marca_id))
    return int(resultado.one() or 0)


def _dados_foto(dados: bytes) -> None:
    from app.services.midia import tipo_da_foto

    if not dados:
        raise ValueError("O arquivo veio vazio.")
    if len(dados) > 8_000_000:
        raise ValueError("Cada foto precisa ter até 8 MB.")
    if tipo_da_foto(dados) is None:
        raise ValueError("Envie a foto em JPG, PNG ou WebP.")


async def analisar_foto(jpeg: bytes, ficha: Ficha | None = None) -> AnaliseFoto:
    """Lê a foto com o gpt-4o-mini (visão, detalhe baixo)."""
    pequeno = await asyncio.to_thread(arte.para_visao, jpeg)
    endereco = "data:image/jpeg;base64," + base64.b64encode(pequeno).decode()
    sobre = ""
    if ficha is not None:
        fatos = ler_fatos(ficha)
        sobre = f"Marca: {ficha.nome}. " + (f"O que faz: {fatos['oferta'][:400]}" if fatos["oferta"] else (ficha.texto or "")[:400])
    resposta = await cliente_openai().with_options(timeout=60).chat.completions.parse(
        model="gpt-4o-mini",
        max_tokens=400,
        messages=[
            {"role": "system", "content": SISTEMA_VISAO},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            (f"Contexto da marca (só para entender, não descreva isso): {sobre}\n\n" if sobre else "")
                            + "Descreva esta imagem."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": endereco, "detail": "low"}},
                ],
            },
        ],
        response_format=AnaliseFoto,
    )
    mensagem = resposta.choices[0].message
    if mensagem.refusal or mensagem.parsed is None:
        raise ValueError("A IA não conseguiu ler a foto.")
    return mensagem.parsed


def _aplicar_analise(foto: MemoriaFoto, analise: AnaliseFoto) -> None:
    foto.descricao = analise.descricao.strip()[:1500]
    foto.tags = ", ".join(tag.strip().lower() for tag in analise.tags if tag.strip())[:500]
    foto.tipo = analise.tipo if analise.tipo in TIPOS_IA else "outro"


AVISO_SEM_LEITURA = "A foto ficou guardada, mas a IA não conseguiu ler agora. Escreva a descrição ou clique em Ler de novo."


async def adicionar_foto(session: AsyncSession, ficha: Ficha, dados: bytes) -> tuple[MemoriaFoto, str]:
    _dados_foto(dados)
    if await contar_fotos(session, ficha.id or 0) >= LIMITE_FOTOS:
        raise ValueError(f"Esta marca já tem {LIMITE_FOTOS} fotos na memória. Apague alguma para enviar outra.")
    jpeg, miniatura, largura, altura = await asyncio.to_thread(arte.normalizar_memoria, dados)
    foto = MemoriaFoto(
        marca_id=ficha.id or 0,
        arquivo=jpeg,
        miniatura=miniatura,
        largura=largura,
        altura=altura,
        tipo="auto",
        papel="auto",
    )
    session.add(foto)
    await session.commit()
    await session.refresh(foto)
    aviso = ""
    try:
        _aplicar_analise(foto, await analisar_foto(jpeg, ficha))
        await session.commit()
    except Exception:
        await session.rollback()
        await session.refresh(foto)
        aviso = AVISO_SEM_LEITURA
    return foto, aviso


async def reanalisar(session: AsyncSession, foto: MemoriaFoto, ficha: Ficha | None) -> str:
    try:
        _aplicar_analise(foto, await analisar_foto(foto.arquivo, ficha))
        await session.commit()
    except Exception:
        await session.rollback()
        await session.refresh(foto)
        return "A IA não conseguiu ler a foto agora. Tente de novo em instantes ou escreva a descrição."
    return ""


async def apagar_foto(session: AsyncSession, foto: MemoriaFoto) -> None:
    ident = foto.id
    await session.exec(update(Agendamento).where(Agendamento.foto_id == ident).values(foto_id=None))
    await session.exec(update(Agendamento).where(Agendamento.referencia_id == ident).values(referencia_id=None))
    await session.delete(foto)
    await session.commit()


def nome_do_dia(dia: str) -> str:
    try:
        ano, mes, dia_mes = (int(parte) for parte in dia.split("-"))
        data = date(ano, mes, dia_mes)
    except ValueError:
        return ""
    return f"{DIAS_SEMANA[data.weekday()]}, {data.day:02d}/{data.month:02d}"


async def montar_contexto(session: AsyncSession, ficha: Ficha | None) -> ContextoMemoria:
    if ficha is None or not ficha.id:
        return ContextoMemoria()
    fatos = ler_fatos(ficha)
    fotos = await listar_fotos(session, ficha.id, so_usaveis=True)
    partes: list[str] = []
    linhas_fatos = [f"{rotulo}:\n{fatos[chave]}" for chave, rotulo in CAMPOS_FATOS if fatos[chave]]
    if linhas_fatos:
        partes.append("MEMÓRIA DA MARCA (fatos escritos pelo dono):\n" + "\n\n".join(linhas_fatos))
    contexto = ContextoMemoria(fatos="\n".join(fatos.values()))
    linhas_fotos = []
    linhas_estilo = []
    for foto in fotos:
        descricao = re.sub(r"\s+", " ", foto.descricao or "").strip()[:260] or "(sem descrição)"
        tags = f" (tags: {foto.tags[:120]})" if (foto.tags or "").strip() else ""
        tipo = tipo_efetivo(foto.tipo, foto.descricao, foto.tags)
        papel = normalizar_papel(foto.papel)
        if tipo == "captura" and papel in ("base", "auto"):
            papel = "postar"
        contexto.tipos[foto.id] = tipo
        contexto.papeis[foto.id] = papel
        if papel == "estilo":
            linhas_estilo.append(f"[{foto.id}] {NOMES_TIPOS[tipo]}: {descricao}{tags}")
            contexto.estilo.add(foto.id)
            continue
        linhas_fotos.append(f"[{foto.id}] {NOMES_TIPOS[tipo]} · {NOMES_PAPEIS[papel]}: {descricao}{tags}")
        contexto.usaveis.add(foto.id)
        if tipo in TIPOS_LUGAR:
            contexto.lugar.add(foto.id)
    if linhas_fotos:
        partes.append("FOTOS DA MEMÓRIA (id, tipo · papel, o que mostra):\n" + "\n".join(linhas_fotos))
    if linhas_estilo:
        partes.append(
            "FOTOS DE ESTILO (só inspiração de luz, cores e enquadramento; nunca aparecem no post):\n"
            + "\n".join(linhas_estilo)
        )
    contexto.texto = "\n\n".join(partes)
    return contexto


async def artes_recentes(session: AsyncSession, marca_id: int, quantos: int = 5) -> list[str]:
    """As últimas artes da marca (modelo, título e ideia), para a IA variar."""
    resultado = await session.exec(
        select(Agendamento.plano_arte)
        .where(Agendamento.marca_id == marca_id, Agendamento.plano_arte != "")
        .order_by(col(Agendamento.id).desc())
        .limit(quantos)
    )
    linhas = []
    for bruto in resultado.all():
        try:
            plano = json.loads(bruto or "")
        except ValueError:
            continue
        if not isinstance(plano, dict):
            continue
        linha = f"modelo {plano.get('modelo', '')} · título \"{plano.get('titulo_arte', '')}\""
        if plano.get("ideia"):
            linha += f" · ideia: {str(plano['ideia'])[:120]}"
        linhas.append(linha)
    return linhas


async def posts_recentes(session: AsyncSession, marca_id: int, quantos: int = 8) -> list[str]:
    resultado = await session.exec(
        select(Agendamento.titulo, Agendamento.gancho)
        .where(Agendamento.marca_id == marca_id)
        .order_by(col(Agendamento.id).desc())
        .limit(quantos)
    )
    linhas = []
    for titulo, gancho in resultado.all():
        linha = (titulo or "").strip()[:120]
        if (gancho or "").strip():
            linha += f" (gancho: {gancho.strip()})"
        if linha:
            linhas.append(linha)
    return linhas


def limpar_gancho(texto: str) -> str:
    texto = re.sub(r"[#\"“”«»]", "", texto or "").strip().strip(".!").strip()
    palavras = texto.split()
    return " ".join(palavras[:7])[:80]


def _rodape(ficha: Ficha | None) -> str:
    if ficha is None:
        return ""
    site = (ficha.site or "").strip()
    if site:
        return site.split("://", 1)[-1].removeprefix("www.").rstrip("/")
    return (ficha.nome or "").strip()


async def imagem_da_memoria(session: AsyncSession, foto_id: int, gancho: str, ficha: Ficha | None) -> str:
    """Foto do post feita da foto real (sem IA, sem custo). Captura de tela vira cartão com a frase no alto."""
    foto = await session.get(MemoriaFoto, foto_id)
    if foto is None or (ficha is not None and foto.marca_id != ficha.id):
        raise RuntimeError("Essa foto saiu da memória da marca. Escolha outra.")
    gancho = limpar_gancho(gancho)
    rodape = _rodape(ficha)
    cartao = e_captura(foto)
    chave = hashlib.sha1(f"{gancho}|{rodape}".encode()).hexdigest()[:16] if cartao else "foto"
    variante = f"memoria:{foto_id}:{chave}"
    pronta = await achar_variante(foto_id, variante)
    if pronta:
        return pronta
    dados = foto.arquivo

    def desenhar():
        figura = arte.abrir(dados)
        return arte.cartao_print(figura, gancho, rodape) if cartao else arte.instagram(figura)

    figura = await asyncio.to_thread(desenhar)
    return await guardar_figura(figura, foto.marca_id, "memoria", foto_id, variante)


def _de_lugar(foto) -> bool:
    return tipo_efetivo(foto.tipo, foto.descricao, foto.tags) in TIPOS_LUGAR


def _palavras(texto: str) -> set[str]:
    sem = "".join(c for c in unicodedata.normalize("NFKD", (texto or "").lower()) if not unicodedata.combining(c))
    return {palavra for palavra in re.findall(r"[a-z0-9]{4,}", sem)}


async def bytes_referencia(
    session: AsyncSession, marca_id: int, referencia_id: int | None, texto: str
) -> bytes | None:
    """Foto real do lugar para a IA manter o estilo: a escolhida pelo texto ou a de mais palavras em comum."""
    if marca_id <= 0:
        return None
    if referencia_id:
        foto = await session.get(MemoriaFoto, referencia_id)
        if foto is not None and foto.marca_id == marca_id and foto.usar_em_posts and _de_lugar(foto):
            return foto.arquivo
    fotos = [foto for foto in await listar_fotos(session, marca_id, so_usaveis=True) if _de_lugar(foto)]
    alvo = _palavras(texto)
    melhor, pontos = None, 1
    for foto in fotos:
        comum = len(alvo & _palavras(f"{foto.descricao} {foto.tags}"))
        if comum > pontos:
            melhor, pontos = foto.id, comum
    if melhor is None:
        return None
    escolhida = await session.get(MemoriaFoto, melhor)
    return escolhida.arquivo if escolhida else None


async def base_do_story(session: AsyncSession, foto_id: int | None) -> str:
    """Para captura de tela, o Story usa a imagem original (o cartão do feed já tem a frase)."""
    if not foto_id:
        return ""
    resultado = await session.exec(
        select(MemoriaFoto.tipo, MemoriaFoto.descricao, MemoriaFoto.tags).where(MemoriaFoto.id == foto_id)
    )
    linha = resultado.first()
    return url_memoria(foto_id) if linha and tipo_efetivo(*linha) == "captura" else ""


async def miniaturas(session: AsyncSession, ids: set[int]) -> set[int]:
    """Quais desses ids ainda existem na memória."""
    if not ids:
        return set()
    resultado = await session.exec(select(MemoriaFoto.id).where(col(MemoriaFoto.id).in_(list(ids))))
    return set(resultado.all())
