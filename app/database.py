import ssl
from collections.abc import AsyncIterator
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings


def para_asyncpg(url: str) -> tuple[str, dict]:
    """Aceita a URI do Postgres (inclusive a do Supabase) e devolve o formato do asyncpg."""
    bruto = url.strip()
    if bruto.startswith("postgres://"):
        bruto = "postgresql://" + bruto[len("postgres://") :]
    parsed = urlparse(bruto)
    if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
        raise ValueError("DATABASE_URL precisa começar com postgresql://")

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)

    connect_args: dict = {}
    host = parsed.hostname or ""
    if (sslmode and sslmode != "disable") or "supabase.co" in host or "supabase.com" in host:
        contexto = ssl.create_default_context()
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = contexto
    if parsed.port == 6543 or "pooler.supabase.com" in host:
        connect_args["statement_cache_size"] = 0

    nova = parsed._replace(scheme="postgresql+asyncpg", query=urlencode(query))
    return urlunparse(nova), connect_args


@lru_cache
def get_engine() -> AsyncEngine:
    url, connect_args = para_asyncpg(get_settings().database_url)
    return create_async_engine(url, connect_args=connect_args, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)


async def criar_tabelas() -> None:
    from app import models  # registra mkt_campanhas e mkt_roteiros

    del models
    async with get_engine().begin() as conexao:
        await conexao.run_sync(SQLModel.metadata.create_all)
        await conexao.execute(
            text(
                "ALTER TABLE mkt_agendamentos "
                "ADD COLUMN IF NOT EXISTS video_url VARCHAR(500) NOT NULL DEFAULT ''"
            )
        )
        await conexao.execute(text("ALTER TABLE mkt_campanhas ALTER COLUMN produto TYPE TEXT"))
        await conexao.execute(text("ALTER TABLE mkt_agendamentos ALTER COLUMN rede TYPE VARCHAR(40)"))
        await conexao.execute(text("ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS cena TEXT NOT NULL DEFAULT ''"))
        await conexao.execute(
            text("ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS tentativas INTEGER NOT NULL DEFAULT 0")
        )
        await conexao.execute(
            text("ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS ok_instagram BOOLEAN NOT NULL DEFAULT FALSE")
        )
        await conexao.execute(
            text("ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS ok_facebook BOOLEAN NOT NULL DEFAULT FALSE")
        )
        await conexao.execute(
            text("ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS formato VARCHAR(20) NOT NULL DEFAULT 'feed'")
        )
        await conexao.execute(
            text("ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS feito VARCHAR(80) NOT NULL DEFAULT ''")
        )


async def fechar_engine() -> None:
    await get_engine().dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
