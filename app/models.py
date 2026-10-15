from datetime import datetime, timezone

from sqlalchemy import DateTime, Text
from sqlmodel import Field, SQLModel


def agora() -> datetime:
    return datetime.now(timezone.utc)


class Campanha(SQLModel, table=True):
    __tablename__ = "mkt_campanhas"

    id: int | None = Field(default=None, primary_key=True)
    nicho: str = Field(max_length=120, index=True)
    produto: str = Field(max_length=200)
    titulo_anuncio: str = Field(sa_type=Text)
    legenda_redes: str = Field(sa_type=Text)
    gmn_nome: str = Field(sa_type=Text)
    gmn_categoria: str = Field(sa_type=Text)
    gmn_descricao: str = Field(sa_type=Text)
    criado_em: datetime = Field(default_factory=agora, sa_type=DateTime(timezone=True))


class Roteiro(SQLModel, table=True):
    __tablename__ = "mkt_roteiros"

    id: int | None = Field(default=None, primary_key=True)
    campanha_id: int = Field(foreign_key="mkt_campanhas.id", index=True)
    gancho: str = Field(sa_type=Text)
    problema: str = Field(sa_type=Text)
    solucao: str = Field(sa_type=Text)
    cta: str = Field(sa_type=Text)
    criado_em: datetime = Field(default_factory=agora, sa_type=DateTime(timezone=True))
