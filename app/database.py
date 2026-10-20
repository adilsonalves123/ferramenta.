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
        await conexao.execute(
            text("ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS tema VARCHAR(30) NOT NULL DEFAULT ''")
        )
        await conexao.execute(
            text("ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS marca_id INTEGER NOT NULL DEFAULT 0")
        )
        await conexao.execute(text("ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS foto_id INTEGER NULL"))
        await conexao.execute(
            text("ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS referencia_id INTEGER NULL")
        )
        await conexao.execute(
            text("ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS gancho VARCHAR(80) NOT NULL DEFAULT ''")
        )
        await conexao.execute(text("ALTER TABLE mkt_ficha ADD COLUMN IF NOT EXISTS memoria TEXT NOT NULL DEFAULT ''"))
        await conexao.execute(text("ALTER TABLE mkt_ficha ADD COLUMN IF NOT EXISTS nome VARCHAR(120) NOT NULL DEFAULT ''"))
        await conexao.execute(
            text("ALTER TABLE mkt_ficha ADD COLUMN IF NOT EXISTS publico VARCHAR(200) NOT NULL DEFAULT ''")
        )
        ja_tinha_site = (
            await conexao.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'mkt_ficha' AND column_name = 'site'"
                )
            )
        ).first() is not None
        await conexao.execute(
            text("ALTER TABLE mkt_ficha ADD COLUMN IF NOT EXISTS site VARCHAR(200) NOT NULL DEFAULT ''")
        )
        await conexao.execute(
            text("ALTER TABLE mkt_ficha ADD COLUMN IF NOT EXISTS marca_dagua VARCHAR(60) NOT NULL DEFAULT ''")
        )
        await conexao.execute(
            text("ALTER TABLE mkt_ficha ADD COLUMN IF NOT EXISTS cidade VARCHAR(120) NOT NULL DEFAULT ''")
        )
        await conexao.execute(text("ALTER TABLE mkt_ficha ADD COLUMN IF NOT EXISTS uf VARCHAR(2) NOT NULL DEFAULT ''"))
        await conexao.execute(text("ALTER TABLE mkt_ficha ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION NULL"))
        await conexao.execute(text("ALTER TABLE mkt_ficha ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION NULL"))
        await conexao.execute(
            text("ALTER TABLE mkt_ficha ADD COLUMN IF NOT EXISTS clima_status VARCHAR(300) NOT NULL DEFAULT ''")
        )
        if not ja_tinha_site:
            await conexao.execute(
                text(
                    "UPDATE mkt_ficha SET site = 'https://www.riscaai.com.br', marca_dagua = 'Risca Aí' "
                    "WHERE site = '' AND marca_dagua = '' AND lower(nome) IN ('risca aí', 'risca ai')"
                )
            )
        await conexao.execute(
            text(
                "ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS tentativas_marca INTEGER NOT NULL DEFAULT 0"
            )
        )
        await conexao.execute(
            text(
                "ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS tentativas_publicar INTEGER NOT NULL DEFAULT 0"
            )
        )
        await conexao.execute(
            text("ALTER TABLE mkt_agendamentos ADD COLUMN IF NOT EXISTS reservado_em TIMESTAMPTZ NULL")
        )
        await conexao.execute(
            text(
                "DO $$ DECLARE r record; BEGIN "
                "FOR r IN SELECT i.relname AS indice, c.conname AS restricao "
                "FROM pg_index x "
                "JOIN pg_class t ON t.oid = x.indrelid "
                "JOIN pg_class i ON i.oid = x.indexrelid "
                "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = x.indkey[0] "
                "LEFT JOIN pg_constraint c ON c.conindid = x.indexrelid AND c.contype = 'u' "
                "WHERE t.relname = 'mkt_agendamentos' AND pg_table_is_visible(t.oid) "
                "AND x.indisunique AND NOT x.indisprimary AND x.indnatts = 1 AND a.attname = 'dia' "
                "LOOP "
                "IF r.restricao IS NOT NULL THEN "
                "EXECUTE format('ALTER TABLE mkt_agendamentos DROP CONSTRAINT %I', r.restricao); "
                "ELSE EXECUTE format('DROP INDEX %I', r.indice); END IF; "
                "END LOOP; END $$"
            )
        )
        await conexao.execute(
            text("CREATE INDEX IF NOT EXISTS ix_mkt_agendamentos_dia ON mkt_agendamentos (dia)")
        )
        await conexao.execute(
            text("ALTER TABLE mkt_contas ADD COLUMN IF NOT EXISTS marca_id INTEGER NOT NULL DEFAULT 0")
        )
        await conexao.execute(
            text("ALTER TABLE mkt_contas ADD COLUMN IF NOT EXISTS usuario_meta VARCHAR(200) NOT NULL DEFAULT ''")
        )
        await conexao.execute(
            text(
                "DO $$ BEGIN "
                "IF EXISTS (SELECT 1 FROM pg_indexes WHERE tablename = 'mkt_contas' "
                "AND indexname = 'ix_mkt_contas_rede' AND indexdef LIKE 'CREATE UNIQUE%') THEN "
                "DROP INDEX ix_mkt_contas_rede; "
                "CREATE INDEX ix_mkt_contas_rede ON mkt_contas (rede); "
                "END IF; END $$"
            )
        )
        await conexao.execute(
            text(
                "UPDATE mkt_contas SET marca_id = (SELECT min(id) FROM mkt_ficha) "
                "WHERE marca_id = 0 AND EXISTS (SELECT 1 FROM mkt_ficha)"
            )
        )
        await conexao.execute(
            text(
                "UPDATE mkt_agendamentos SET marca_id = (SELECT min(id) FROM mkt_ficha) "
                "WHERE marca_id = 0 AND EXISTS (SELECT 1 FROM mkt_ficha)"
            )
        )
        await conexao.execute(text("CREATE INDEX IF NOT EXISTS ix_mkt_contas_marca_id ON mkt_contas (marca_id)"))
        await conexao.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ux_mkt_contas_marca_rede ON mkt_contas (marca_id, rede)")
        )


async def fechar_engine() -> None:
    await get_engine().dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
