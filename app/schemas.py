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
        max_length=4000,
        validation_alias=AliasChoices("produto", "produto_servico"),
        examples=["combo de cerveja gelada no happy hour"],
    )
    acao: Literal["texto", "foto"] = "texto"
    foto_base64: str = Field(default="", max_length=6_000_000)
    brief_foto: str = Field(default="", max_length=800)
    tema: str = Field(default="", max_length=30)


class PedidoFotoLegenda(BaseModel):
    legenda: str = Field(min_length=2, max_length=5000)
    marca_id: int = 0


class RespostaFoto(BaseModel):
    foto_url: str


class RoteiroVideo(BaseModel):
    gancho: str = ""
    problema: str = ""
    solucao: str = ""
    cta: str = ""


class GoogleMeuNegocio(BaseModel):
    nome_otimizado: str = ""
    categoria: str = ""
    descricao_seo: str = ""


class CampanhaGerada(BaseModel):
    titulo_anuncio: str
    legenda_redes: str


class CampanhaCompleta(CampanhaGerada):
    roteiro_video: RoteiroVideo = Field(default_factory=RoteiroVideo)
    google_meu_negocio: GoogleMeuNegocio = Field(default_factory=GoogleMeuNegocio)
    foto_url: str | None = None
    audio_url: str | None = None
    video_url: str | None = None
    aviso_midia: str | None = None


class MensagemCriacao(BaseModel):
    papel: Literal["pessoa", "assistente"]
    texto: str = Field(min_length=1, max_length=2000)


class PedidoSugestao(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    nicho: str = Field(min_length=2, max_length=120, validation_alias=AliasChoices("nicho", "nicho_negocio"))
    produto: str = Field(min_length=2, max_length=4000, validation_alias=AliasChoices("produto", "produto_servico"))
    origem: Literal["foto"] = "foto"
    transcricao: str = Field(default="", max_length=8000)
    link: str = Field(default="", max_length=500)
    mensagens: list[MensagemCriacao] = Field(default_factory=list, max_length=12)


class CenaHistoria(BaseModel):
    narracao: str = Field(min_length=2, max_length=800)
    imagem: str = Field(min_length=2, max_length=800)


class OpcaoCriacao(BaseModel):
    titulo: str = Field(min_length=2, max_length=80)
    detalhe: str = Field(default="", max_length=240)


class SugestaoVideo(BaseModel):
    mensagem: str
    titulo: str
    cena: str
    legenda: str
    transcricao: str = ""
    roteiro: str = ""
    cenas: list[CenaHistoria] = Field(default_factory=list)
    opcoes: list[OpcaoCriacao] = Field(default_factory=list)


class PedidoFotoConfirmada(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    nicho: str = Field(min_length=2, max_length=120, validation_alias=AliasChoices("nicho", "nicho_negocio"))
    produto: str = Field(min_length=2, max_length=4000, validation_alias=AliasChoices("produto", "produto_servico"))
    titulo: str = Field(min_length=2, max_length=200)
    legenda: str = Field(default="", max_length=1000)
    cena: str = Field(min_length=2, max_length=800)
    foto_base64: str = Field(default="", max_length=6_000_000)


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


class PedidoPublicarAgora(BaseModel):
    rede: str = Field(min_length=2, max_length=20)
    formato: str = "feed"
    tema: str = Field(default="", max_length=30)
    marca_id: int = 0
    titulo: str = Field(min_length=1, max_length=300)
    legenda: str = Field(min_length=1)
    imagem_url: str = Field(default="", max_length=500)
    video_url: str = Field(default="", max_length=500)


class RespostaPublicacao(BaseModel):
    status: str
    detalhe: str = ""


class PedidoMarca(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    publico: str = Field(default="", max_length=200)
    site: str = Field(default="", max_length=200)
    marca_dagua: str = Field(default="", max_length=60)
    cidade: str = Field(default="", max_length=120)
    uf: str = Field(default="", max_length=2)
    texto: str = Field(min_length=20, max_length=8000)


class ItemMarca(BaseModel):
    id: int
    nome: str
    publico: str
    site: str = ""
    marca_dagua: str = ""
    cidade: str = ""
    uf: str = ""
    latitude: float | None = None
    longitude: float | None = None
    aviso_local: str = ""
    texto: str


class RespostaMarcas(BaseModel):
    itens: list[ItemMarca]


class PosteSemana(BaseModel):
    dia: str
    hora: str
    titulo: str
    legenda: str


class RespostaSemana(BaseModel):
    itens: list[PosteSemana]
    aviso: str = ""


class DiaPedido(BaseModel):
    dia: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    hora: str = "09:00"

    def hora_normalizada(self) -> str:
        partes = self.hora.strip().split(":")
        if len(partes) < 2 or not partes[0].isdigit() or not partes[1].isdigit():
            raise ValueError("Escolha um horário válido.")
        horas, minutos = int(partes[0]), int(partes[1])
        if not (0 <= horas <= 23 and 0 <= minutos <= 59):
            raise ValueError("Escolha um horário válido.")
        return f"{horas:02d}:{minutos:02d}"


class PedidoSemana(BaseModel):
    dias: list[DiaPedido] = Field(min_length=1, max_length=14)
    rede: str = "Ambos"
    formato: str = "feed"
    tema: str = Field(default="", max_length=30)
    marca_id: int = 0
    contexto: str = Field(default="", max_length=1500)
    sugestao_id: int = 0


class ItemSugestao(BaseModel):
    id: int
    dia_sugerido: str
    tipo: str
    titulo: str
    detalhe: str
    data_evento: str


class RespostaSugestoes(BaseModel):
    marca_id: int
    cidade: str = ""
    uf: str = ""
    clima_status: str = ""
    itens: list[ItemSugestao]


class PedidoAgenda(BaseModel):
    dia: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    hora: str = "09:00"
    rede: str = Field(min_length=2, max_length=20)
    formato: str = "feed"
    tema: str = Field(default="", max_length=30)
    marca_id: int = 0
    titulo: str = Field(min_length=1, max_length=300)
    legenda: str = Field(min_length=1)
    imagem_url: str = Field(default="", max_length=500)
    video_url: str = Field(default="", max_length=500)

    def hora_normalizada(self) -> str:
        partes = self.hora.strip().split(":")
        if len(partes) < 2 or not partes[0].isdigit() or not partes[1].isdigit():
            raise ValueError("Escolha um horário válido.")
        horas, minutos = int(partes[0]), int(partes[1])
        if not (0 <= horas <= 23 and 0 <= minutos <= 59):
            raise ValueError("Escolha um horário válido.")
        return f"{horas:02d}:{minutos:02d}"


class PedidoFormato(BaseModel):
    formato: str = Field(min_length=4, max_length=20)


class ItemAgenda(BaseModel):
    id: int
    dia: str
    hora: str
    rede: str
    formato: str = "feed"
    tema: str = ""
    marca_id: int = 0
    titulo: str
    status: str
    detalhe: str
    aguarda_foto: bool = False
    imagem_url: str = ""
    pode_trocar_foto: bool = False


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
    marca_id: int = 0


class PedidoResultados(BaseModel):
    marca_id: int = 0


class ItemResultado(BaseModel):
    id: int
    rede: str
    formato: str
    tema: str = ""
    dia: str
    hora: str
    legenda: str = ""
    imagem_url: str = ""
    permalink: str = ""
    alcance: int | None = None
    visualizacoes: int | None = None
    curtidas: int | None = None
    comentarios: int | None = None
    salvos: int | None = None
    compartilhamentos: int | None = None
    estado: str
    detalhe: str = ""
    medido_em: str = ""


class MelhorResultado(ItemResultado):
    numero: int
    rotulo: str


class ResumoResultados(BaseModel):
    posts_30_dias: int
    alcance_medio: int | None = None
    melhor: MelhorResultado | None = None


class RespostaResultados(BaseModel):
    marca_id: int
    itens: list[ItemResultado]
    resumo: ResumoResultados
    contas_ligadas: bool
    falta_permissao: list[str] = Field(default_factory=list)
    aviso: str = ""


class PostResumo(BaseModel):
    id: int
    dia: str
    hora: str
    rede: str
    formato: str = "feed"
    tema: str = ""
    status: str
    detalhe: str = ""
    linha: str = ""
    imagem_url: str = ""
    aguarda_foto: bool = False
    tem_video: bool = False


class MelhorSemana(BaseModel):
    rede: str
    legenda: str = ""
    permalink: str = ""
    numero: int
    rotulo: str


class SemanaResumo(BaseModel):
    posts: int = 0
    alcance_total: int | None = None
    medidos: int = 0
    melhor: MelhorSemana | None = None
    dica: str = ""
    contas_ligadas: bool = False


class ClimaHoje(BaseModel):
    maxima: int | None = None
    minima: int | None = None
    chuva: int | None = None


class RespostaResumo(BaseModel):
    marca_id: int
    nome: str
    cidade: str = ""
    uf: str = ""
    hoje: str
    amanha: str
    saudacao: str
    data_extenso: str
    clima: ClimaHoje | None = None
    proximo: PostResumo | None = None
    erros: list[PostResumo] = Field(default_factory=list)
    semana: SemanaResumo = Field(default_factory=SemanaResumo)
    sugestoes: list[ItemSugestao] = Field(default_factory=list)
