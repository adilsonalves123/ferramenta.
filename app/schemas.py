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
    foto_base64: str = Field(default="", max_length=6_000_000)


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


class ClonagemTexto(BaseModel):
    titulo: str
    referencia: str
    gancho: str
    retencao: str
    desenvolvimento: str
    cta: str
    legenda: str


class ClonagemGerada(ClonagemTexto):
    foto_url: str | None = None
    video_url: str | None = None
    aviso_midia: str | None = None


class PedidoRadar(BaseModel):
    cidade: str = Field(min_length=2, max_length=80)
    redes: list[Literal["instagram", "tiktok", "facebook", "google"]] = Field(min_length=1, max_length=4)
    tipo_busca: Literal["geral", "determinada"] = "geral"
    tags: str = Field(default="", max_length=200)
    categoria: str = Field(default="", max_length=80)


class EmpresaRadar(BaseModel):
    nome: str
    categoria: str
    bairro: str
    endereco: str
    telefone: str
    porque: str


class PedidoProjeto(BaseModel):
    tipo: str = Field(min_length=2, max_length=20)
    titulo: str = Field(min_length=1, max_length=200)
    legenda: str = ""
    imagem_url: str = Field(default="", max_length=500)
    video_url: str = Field(default="", max_length=500)


class ItemProjeto(BaseModel):
    id: int
    tipo: str
    titulo: str
    legenda: str
    imagem_url: str
    video_url: str


class PedidoAgenda(BaseModel):
    dia: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    hora: str = "09:00"
    rede: str = Field(min_length=2, max_length=20)
    titulo: str = Field(min_length=1, max_length=300)
    legenda: str = Field(min_length=1)
    imagem_url: str = Field(default="", max_length=500)

    def hora_normalizada(self) -> str:
        partes = self.hora.strip().split(":")
        if len(partes) < 2 or not partes[0].isdigit() or not partes[1].isdigit():
            raise ValueError("Escolha um horário válido.")
        horas, minutos = int(partes[0]), int(partes[1])
        if not (0 <= horas <= 23 and 0 <= minutos <= 59):
            raise ValueError("Escolha um horário válido.")
        return f"{horas:02d}:{minutos:02d}"


class ItemAgenda(BaseModel):
    id: int
    dia: str
    hora: str
    rede: str
    titulo: str
    status: str
    detalhe: str


class EstadoRede(BaseModel):
    ligado: bool
    nome: str = ""


class RespostaAgenda(BaseModel):
    itens: list[ItemAgenda]
    instagram: EstadoRede
    facebook: EstadoRede
    meta_pronta: bool


class PaginaFacebook(BaseModel):
    id: str
    nome: str


class RespostaPaginas(BaseModel):
    paginas: list[PaginaFacebook]
    escolhida: str = ""


class PedidoPagina(BaseModel):
    pagina_id: str = Field(min_length=2, max_length=40)


class RadarResposta(BaseModel):
    cidade: str
    categoria: str
    redes: list[str]
    tipo_busca: str
    tags: str
    aviso: str
    empresas: list[EmpresaRadar]
