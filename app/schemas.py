from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class PedidoCampanha(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    nicho: str = Field(
        min_length=2,
        max_length=120,
        validation_alias=AliasChoices("nicho", "nicho_negocio"),
        examples=["bar e petiscaria"],
    )
    produto: str = Field(
        min_length=2,
        max_length=200,
        validation_alias=AliasChoices("produto", "produto_servico"),
        examples=["combo de cerveja gelada no happy hour"],
    )
    acao: Literal["texto", "foto", "video", "google"] = "texto"


class RoteiroVideo(BaseModel):
    gancho: str
    problema: str
    solucao: str
    cta: str


class GoogleMeuNegocio(BaseModel):
    nome_otimizado: str
    categoria: str
    descricao_seo: str


class CampanhaGerada(BaseModel):
    titulo_anuncio: str
    legenda_redes: str
    roteiro_video: RoteiroVideo
    google_meu_negocio: GoogleMeuNegocio


class CampanhaCompleta(CampanhaGerada):
    foto_url: str | None = None
    audio_url: str | None = None
    video_url: str | None = None
    aviso_midia: str | None = None


class PedidoClonador(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    nicho: str = Field(min_length=2, max_length=120, validation_alias=AliasChoices("nicho", "nicho_negocio"))
    produto: str = Field(min_length=2, max_length=200, validation_alias=AliasChoices("produto", "produto_servico"))
    link_video: str = Field(default="", max_length=500)
    nome_arquivo: str = Field(default="", max_length=200)


class ClonagemGerada(BaseModel):
    titulo: str
    referencia: str
    gancho: str
    retencao: str
    desenvolvimento: str
    cta: str
    legenda: str


class PedidoRadar(BaseModel):
    cidade: str = Field(min_length=2, max_length=80)
    categoria: str = Field(min_length=2, max_length=80)


class EmpresaRadar(BaseModel):
    nome: str
    categoria: str
    bairro: str
    endereco: str
    telefone: str
    porque: str


class RadarResposta(BaseModel):
    cidade: str
    categoria: str
    aviso: str
    empresas: list[EmpresaRadar]
