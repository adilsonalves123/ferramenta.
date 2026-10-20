from datetime import datetime, timezone

from sqlalchemy import DateTime, LargeBinary, Text, UniqueConstraint
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
    dia: str = Field(max_length=10, index=True)
    hora: str = Field(default="09:00", max_length=5)
    rede: str = Field(max_length=40)
    titulo: str = Field(sa_type=Text)
    legenda: str = Field(sa_type=Text)
    imagem_url: str = Field(default="", max_length=500)
    video_url: str = Field(default="", max_length=500)
    cena: str = Field(default="", sa_type=Text)
    tentativas: int = Field(default=0)
    tentativas_marca: int = Field(default=0)
    tentativas_publicar: int = Field(default=0)
    reservado_em: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    ok_instagram: bool = Field(default=False)
    ok_facebook: bool = Field(default=False)
    formato: str = Field(default="feed", max_length=20)
    tema: str = Field(default="", max_length=30)
    marca_id: int = Field(default=0)
    foto_id: int | None = Field(default=None)
    referencia_id: int | None = Field(default=None)
    gancho: str = Field(default="", max_length=80)
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
    usuario_meta: str = Field(default="", max_length=200)
    token: str = Field(default="", sa_type=Text)
    atualizado_em: datetime = Field(default_factory=agora, sa_type=DateTime(timezone=True))


class Publicacao(SQLModel, table=True):
    __tablename__ = "mkt_publicacoes"
    __table_args__ = (UniqueConstraint("rede", "externo_id", name="ux_mkt_publicacoes_rede_externo"),)

    id: int | None = Field(default=None, primary_key=True)
    agendamento_id: int | None = Field(default=None, index=True)
    marca_id: int = Field(default=0, index=True)
    rede: str = Field(max_length=20)
    formato: str = Field(default="feed", max_length=20)
    externo_id: str = Field(max_length=80)
    permalink: str = Field(default="", max_length=500)
    legenda: str = Field(default="", max_length=300)
    imagem_url: str = Field(default="", sa_type=Text)
    tema: str = Field(default="", max_length=30)
    hora: str = Field(default="", max_length=5)
    publicado_em: datetime = Field(default_factory=agora, sa_type=DateTime(timezone=True))
    metricas: str = Field(default="", sa_type=Text)
    medido_em: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    medicoes: int = Field(default=0)
    proxima_medicao: datetime | None = Field(default=None, sa_type=DateTime(timezone=True), index=True)
    detalhe: str = Field(default="", sa_type=Text)
    criado_em: datetime = Field(default_factory=agora, sa_type=DateTime(timezone=True))


class Ficha(SQLModel, table=True):
    __tablename__ = "mkt_ficha"

    id: int | None = Field(default=None, primary_key=True)
    nome: str = Field(default="", max_length=120)
    publico: str = Field(default="", max_length=200)
    site: str = Field(default="", max_length=200)
    marca_dagua: str = Field(default="", max_length=60)
    abrangencia: str = Field(default="cidade", max_length=10)
    cidade: str = Field(default="", max_length=120)
    uf: str = Field(default="", max_length=2)
    latitude: float | None = Field(default=None)
    longitude: float | None = Field(default=None)
    clima_status: str = Field(default="", max_length=300)
    texto: str = Field(sa_type=Text)
    memoria: str = Field(default="", sa_type=Text)
    atualizado_em: datetime = Field(default_factory=agora, sa_type=DateTime(timezone=True))


class MemoriaFoto(SQLModel, table=True):
    __tablename__ = "mkt_memoria_fotos"

    id: int | None = Field(default=None, primary_key=True)
    marca_id: int = Field(index=True)
    arquivo: bytes = Field(sa_type=LargeBinary)
    miniatura: bytes = Field(default=b"", sa_type=LargeBinary)
    largura: int = Field(default=0)
    altura: int = Field(default=0)
    descricao: str = Field(default="", sa_type=Text)
    tags: str = Field(default="", sa_type=Text)
    tipo: str = Field(default="outro", max_length=30)
    usar_em_posts: bool = Field(default=True)
    criado_em: datetime = Field(default_factory=agora, sa_type=DateTime(timezone=True))


class Imagem(SQLModel, table=True):
    """Foto de post guardada no banco: o disco do servidor é apagado a cada deploy."""

    __tablename__ = "mkt_imagens"

    id: int | None = Field(default=None, primary_key=True)
    marca_id: int = Field(default=0, index=True)
    arquivo: bytes = Field(sa_type=LargeBinary)
    largura: int = Field(default=0)
    altura: int = Field(default=0)
    origem: str = Field(default="", max_length=20)
    base_id: int | None = Field(default=None, index=True)
    variante: str = Field(default="", max_length=80)
    criado_em: datetime = Field(default_factory=agora, sa_type=DateTime(timezone=True))


class Sugestao(SQLModel, table=True):
    __tablename__ = "mkt_sugestoes"
    __table_args__ = (
        UniqueConstraint("marca_id", "dia_sugerido", "tipo", "titulo", name="ux_mkt_sugestoes_chave"),
    )

    id: int | None = Field(default=None, primary_key=True)
    marca_id: int = Field(default=0, index=True)
    dia_sugerido: str = Field(max_length=10, index=True)
    tipo: str = Field(max_length=20)
    titulo: str = Field(max_length=160)
    detalhe: str = Field(default="", sa_type=Text)
    data_evento: str = Field(max_length=10)
    status: str = Field(default="nova", max_length=20)
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
