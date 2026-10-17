from datetime import datetime, timezone

from sqlalchemy import DateTime, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def agora() -> datetime:
    return datetime.now(timezone.utc)


class Campanha(SQLModel, table=True):
    __tablename__ = "mkt_campanhas"

    id: int | None = Field(default=None, primary_key=True)
    nicho: str = Field(max_length=120, index=True)
    produto: str = Field(sa_type=Text)
    titulo_anuncio: str = Field(sa_type=Text)
    legenda_redes: str = Field(sa_type=Text)
    gmn_nome: str = Field(sa_type=Text)
    gmn_categoria: str = Field(sa_type=Text)
    gmn_descricao: str = Field(sa_type=Text)
    criado_em: datetime = Field(default_factory=agora, sa_type=DateTime(timezone=True))


class Agendamento(SQLModel, table=True):
    __tablename__ = "mkt_agendamentos"

    id: int | None = Field(default=None, primary_key=True)
    dia: str = Field(max_length=10, unique=True, index=True)
    hora: str = Field(default="09:00", max_length=5)
    rede: str = Field(max_length=40)
    titulo: str = Field(sa_type=Text)
    legenda: str = Field(sa_type=Text)
    imagem_url: str = Field(default="", max_length=500)
    video_url: str = Field(default="", max_length=500)
    cena: str = Field(default="", sa_type=Text)
    tentativas: int = Field(default=0)
    ok_instagram: bool = Field(default=False)
    ok_facebook: bool = Field(default=False)
    formato: str = Field(default="feed", max_length=20)
    tema: str = Field(default="", max_length=30)
    marca_id: int = Field(default=0)
    feito: str = Field(default="", max_length=80)
    status: str = Field(default="na_fila", max_length=20)
    detalhe: str = Field(default="", sa_type=Text)
    criado_em: datetime = Field(default_factory=agora, sa_type=DateTime(timezone=True))


class Projeto(SQLModel, table=True):
    __tablename__ = "mkt_projetos"

    id: int | None = Field(default=None, primary_key=True)
    tipo: str = Field(max_length=20, index=True)
    titulo: str = Field(max_length=200)
    legenda: str = Field(default="", sa_type=Text)
    imagem_url: str = Field(default="", max_length=500)
    video_url: str = Field(default="", max_length=500)
    criado_em: datetime = Field(default_factory=agora, sa_type=DateTime(timezone=True))


class ContaRede(SQLModel, table=True):
    __tablename__ = "mkt_contas"
    __table_args__ = (UniqueConstraint("marca_id", "rede", name="ux_mkt_contas_marca_rede"),)

    id: int | None = Field(default=None, primary_key=True)
    marca_id: int = Field(default=0, index=True)
    rede: str = Field(max_length=20, index=True)
    nome: str = Field(default="", max_length=120)
    externo_id: str = Field(default="", max_length=80)
    token: str = Field(default="", sa_type=Text)
    atualizado_em: datetime = Field(default_factory=agora, sa_type=DateTime(timezone=True))


class Ficha(SQLModel, table=True):
    __tablename__ = "mkt_ficha"

    id: int | None = Field(default=None, primary_key=True)
    nome: str = Field(default="", max_length=120)
    publico: str = Field(default="", max_length=200)
    texto: str = Field(sa_type=Text)
    atualizado_em: datetime = Field(default_factory=agora, sa_type=DateTime(timezone=True))


class VideoTrabalho(SQLModel, table=True):
    __tablename__ = "mkt_video_trabalhos"

    id: str = Field(primary_key=True, max_length=32)
    status: str = Field(default="montando", max_length=20, index=True)
    tentativas: int = Field(default=0)
    pedido: str = Field(default="", sa_type=Text)
    resultado: str = Field(default="", sa_type=Text)
    detalhe: str = Field(default="", sa_type=Text)
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
