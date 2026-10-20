"""Fotos dos posts guardadas no Postgres, servidas em /img/{id}.jpg (o disco do Render some a cada deploy)."""

import asyncio
import json
import re
import urllib.request
from datetime import timedelta

from sqlalchemy import delete
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.database import get_sessionmaker
from app.models import Agendamento, Imagem, MemoriaFoto, Projeto, agora
from app.services import arte

PADRAO_IMG = re.compile(r"^/img/(\d+)\.jpg$")
PADRAO_MEMORIA = re.compile(r"^/memoria/foto/(\d+)\.jpg$")
DIAS_GUARDADAS = 30


def url_imagem(ident: int) -> str:
    return f"/img/{ident}.jpg"


def url_memoria(ident: int, mini: bool = False) -> str:
    return f"/memoria/foto/{ident}.jpg" + ("?mini=1" if mini else "")


def _caminho(url: str) -> str:
    caminho = url.strip().split("?", 1)[0]
    base = get_settings().app_url.rstrip("/")
    if base and caminho.startswith(base + "/"):
        caminho = caminho[len(base) :]
    return caminho


def id_da_url(url: str) -> int | None:
    achado = PADRAO_IMG.match(_caminho(url))
    return int(achado.group(1)) if achado else None


def id_memoria_da_url(url: str) -> int | None:
    achado = PADRAO_MEMORIA.match(_caminho(url))
    return int(achado.group(1)) if achado else None


async def guardar(
    dados: bytes,
    largura: int,
    altura: int,
    marca_id: int = 0,
    origem: str = "",
    base_id: int | None = None,
    variante: str = "",
) -> int:
    async with get_sessionmaker()() as session:
        registro = Imagem(
            marca_id=marca_id,
            arquivo=dados,
            largura=largura,
            altura=altura,
            origem=origem[:20],
            base_id=base_id,
            variante=variante[:80],
        )
        session.add(registro)
        await session.commit()
        await session.refresh(registro)
        return registro.id or 0


async def guardar_figura(figura, marca_id: int = 0, origem: str = "", base_id: int | None = None, variante: str = "") -> str:
    dados = await asyncio.to_thread(arte.para_jpeg, figura, 85)
    ident = await guardar(dados, figura.width, figura.height, marca_id, origem, base_id, variante)
    return url_imagem(ident)


async def achar_variante(base_id: int, variante: str) -> str:
    async with get_sessionmaker()() as session:
        resultado = await session.exec(
            select(Imagem.id).where(Imagem.base_id == base_id, Imagem.variante == variante[:80]).order_by(col(Imagem.id).desc())
        )
        ident = resultado.first()
    return url_imagem(ident) if ident else ""


async def ler(ident: int) -> bytes | None:
    async with get_sessionmaker()() as session:
        resultado = await session.exec(select(Imagem.arquivo).where(Imagem.id == ident))
        return resultado.first()


async def ler_memoria(ident: int, mini: bool = False) -> bytes | None:
    coluna = MemoriaFoto.miniatura if mini else MemoriaFoto.arquivo
    async with get_sessionmaker()() as session:
        resultado = await session.exec(select(coluna).where(MemoriaFoto.id == ident))
        dados = resultado.first()
    if mini and not dados:
        return await ler_memoria(ident)
    return dados


def _arquivo_midia(caminho: str):
    from app.services.midia import PASTA_MIDIA

    partes = caminho.strip("/").split("/")
    if len(partes) != 3 or partes[0] != "midia" or not partes[1] or not partes[2] or ".." in partes:
        return None
    return PASTA_MIDIA / partes[1] / partes[2]


def _baixar(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resposta:
        dados = resposta.read(8_000_001)
    if len(dados) > 8_000_000:
        raise RuntimeError("A foto do post passa de 8 MB.")
    return dados


async def bytes_da_url(url: str) -> bytes:
    """Os bytes da foto do post: do banco (/img, /memoria), do disco antigo (/midia) ou da internet."""
    caminho = _caminho(url)
    ident = id_da_url(caminho)
    if ident is not None:
        dados = await ler(ident)
        if not dados:
            raise RuntimeError("A foto do post não está mais guardada. Anexe de novo.")
        return dados
    ident = id_memoria_da_url(caminho)
    if ident is not None:
        dados = await ler_memoria(ident)
        if not dados:
            raise RuntimeError("Essa foto saiu da memória da marca. Escolha outra.")
        return dados
    if caminho.startswith("/midia/"):
        arquivo = _arquivo_midia(caminho)
        if arquivo is None or not arquivo.is_file():
            raise RuntimeError("A foto do post não está mais no servidor. Anexe de novo.")
        return await asyncio.to_thread(arquivo.read_bytes)
    if caminho.startswith("http://") or caminho.startswith("https://"):
        return await asyncio.to_thread(_baixar, caminho)
    raise RuntimeError("A foto do post não foi achada. Anexe de novo.")


async def existe(url: str) -> bool:
    caminho = _caminho(url)
    ident = id_da_url(caminho)
    if ident is not None:
        async with get_sessionmaker()() as session:
            return (await session.exec(select(Imagem.id).where(Imagem.id == ident))).first() is not None
    ident = id_memoria_da_url(caminho)
    if ident is not None:
        async with get_sessionmaker()() as session:
            return (await session.exec(select(MemoriaFoto.id).where(MemoriaFoto.id == ident))).first() is not None
    if caminho.startswith("/midia/"):
        arquivo = _arquivo_midia(caminho)
        return arquivo is not None and arquivo.is_file()
    return True


async def limpar_antigas(session: AsyncSession, dias: int = DIAS_GUARDADAS) -> int:
    """Apaga fotos com mais de 'dias' que nenhum post da fila nem projeto usa (cabe no plano grátis)."""
    limite = agora() - timedelta(days=dias)
    velhas = list((await session.exec(select(Imagem.id).where(Imagem.criado_em < limite))).all())
    if not velhas:
        return 0
    usadas: set[int] = set()
    fila = await session.exec(
        select(Agendamento.imagem_url, Agendamento.plano_arte).where(Agendamento.status != "publicado")
    )
    projetos = await session.exec(select(Projeto.imagem_url))
    urls = list(projetos.all())
    for imagem_url, plano_arte in fila.all():
        urls.append(imagem_url)
        try:
            plano = json.loads(plano_arte or "{}")
        except ValueError:
            plano = {}
        if isinstance(plano, dict):
            urls.extend(str(plano.get(chave) or "") for chave in ("base_url", "feed_url", "story_url"))
    for url in urls:
        ident = id_da_url(url or "")
        if ident is not None:
            usadas.add(ident)
    apagar = [ident for ident in velhas if ident not in usadas]
    for inicio in range(0, len(apagar), 500):
        await session.exec(delete(Imagem).where(col(Imagem.id).in_(apagar[inicio : inicio + 500])))
    await session.commit()
    return len(apagar)
