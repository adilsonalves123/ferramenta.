from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class PedidoCampanha(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    nicho: str = Field(
        min_length=2,
        max_length=120,
        validation_alias=AliasChoices("nicho", "nicho_negocio"),
        examples=["loja de roupas"],
    )
    produto: str = Field(
        min_length=2,
        max_length=4000,
        validation_alias=AliasChoices("produto", "produto_servico"),
        examples=["produto da semana com entrega no mesmo dia"],
    )
    acao: Literal["texto", "foto"] = "texto"
    foto_base64: str = Field(default="", max_length=6_000_000)
    brief_foto: str = Field(default="", max_length=800)
    tema: str = Field(default="", max_length=30)
    marca_id: int = 0
    dia: str = Field(default="", max_length=10)
    hora: str = Field(default="", max_length=5)


class PedidoFotoLegenda(BaseModel):
    legenda: str = Field(min_length=2, max_length=5000)
    marca_id: int = 0
    cena: str = Field(default="", max_length=800)
    referencia_id: int | None = None
    plano_arte: dict | None = None


class RespostaFoto(BaseModel):
    foto_url: str
    story_url: str = ""
    plano_arte: dict | None = None
    aviso: str = ""


class PlanoArte(BaseModel):
    """Plano do diretor de arte, devolvido pela IA junto com o texto do post."""

    ideia: str
    foto_base_id: int | None
    fotos_estilo_ids: list[int]
    edicao_foto: str
    modelo: Literal[
        "faixa", "selo", "destaque", "antes_depois", "tela_app", "story_chamada",
        "alerta", "etiqueta", "carimbo", "neon", "poster", "minimal",
    ]
    paleta: Literal[
        "marca", "urgencia", "quente", "fresca", "fria", "elegante", "doce", "natural", "vibrante", "neutra", "vermelha"
    ]
    posicao_texto: Literal["auto", "topo", "base"]
    titulo_arte: str
    subtitulo_arte: str
    selo: str
    chamada: str
    antes: str
    cor: Literal["principal", "destaque"]
    aviso: str


class PedidoArte(BaseModel):
    marca_id: int = 0
    plano_arte: dict
    formato: str = Field(default="feed", max_length=20)


class PedidoArteFoto(BaseModel):
    marca_id: int = 0
    foto_id: int
    plano_arte: dict | None = None
    gancho: str = Field(default="", max_length=80)


class PedidoTresArtes(BaseModel):
    marca_id: int = 0
    descricao: str = Field(min_length=2, max_length=1500)
    formato: str = Field(default="feed", max_length=20)
    fotos_ids: list[int] = Field(default_factory=list, max_length=4)
    anexo_url: str = ""
    ja_usadas: list[str] = Field(default_factory=list, max_length=12)


class OpcaoArtePronta(BaseModel):
    plano_arte: dict
    foto_url: str
    modelo: str = ""
    angulo: str = ""


class RespostaTresArtes(BaseModel):
    legenda: str
    formato: str
    opcoes: list[OpcaoArtePronta]
    precisa_foto: str = ""


class ItemPrateleira(BaseModel):
    id: int
    tipo: str
    titulo: str
    quando: str = ""
    miniatura: str = ""
    formato: str = "feed"
    legenda: str = ""


class RespostaPrateleira(BaseModel):
    itens: list[ItemPrateleira]


class PedidoRascunho(BaseModel):
    marca_id: int = 0
    legenda: str = Field(default="", max_length=5000)
    plano_arte: dict | None = None
    formato: str = Field(default="feed", max_length=20)
    cena: str = Field(default="", max_length=800)
    gancho: str = Field(default="", max_length=80)
    descricao: str = Field(default="", max_length=200)


class PedidoSalvarRascunho(BaseModel):
    legenda: str | None = Field(default=None, max_length=5000)
    plano_arte: dict | None = None
    formato: str | None = Field(default=None, max_length=20)
    descricao: str = Field(default="", max_length=200)
    nova_versao: bool = False


class PedidoAjuste(BaseModel):
    mensagem: str = Field(min_length=1, max_length=2000)
    fotos_ids: list[int] = Field(default_factory=list, max_length=6)
    anexo_url: str = Field(default="", max_length=500)


class PedidoRestaurar(BaseModel):
    n: int


class VersaoRascunho(BaseModel):
    n: int
    criado_em: str
    descricao_mudanca: str = ""
    feed_url: str = ""
    story_url: str = ""


class ItemRascunho(BaseModel):
    id: int
    marca_id: int
    legenda: str
    titulo: str = ""
    plano_arte: dict | None = None
    formato: str = "feed"
    base_url: str = ""
    feed_url: str = ""
    story_url: str = ""
    cena: str = ""
    gancho: str = ""
    agendamento_id: int | None = None
    versoes: list[VersaoRascunho] = Field(default_factory=list)
    versao_atual: int = 0


class RespostaRascunho(BaseModel):
    rascunho: ItemRascunho | None = None


class RespostaAjuste(BaseModel):
    rascunho: ItemRascunho | None = None
    resposta: str
    tipos: list[str] = Field(default_factory=list)
    novo: bool = False
    pergunta: bool = False
    custo_imagem: bool = False


class CamposArteAjuste(BaseModel):
    """Só os campos que a pessoa pediu para mudar; null = fica como está."""

    titulo_arte: str | None
    subtitulo_arte: str | None
    selo: str | None
    chamada: str | None
    antes: str | None
    modelo: Literal[
        "faixa", "selo", "destaque", "antes_depois", "tela_app", "story_chamada",
        "alerta", "etiqueta", "carimbo", "neon", "poster", "minimal", "livre",
    ] | None
    cor: Literal["principal", "destaque"] | None
    paleta: Literal[
        "marca", "urgencia", "quente", "fresca", "fria", "elegante", "doce", "natural", "vibrante", "neutra", "vermelha"
    ] | None
    cor_livre: str | None
    cor_texto: str | None
    cor_fundo: str | None
    escala_texto: Literal["maior", "menor"] | None
    posicao_texto: Literal["auto", "topo", "base"] | None


class IntencaoAjuste(BaseModel):
    """Como a IA leu a mensagem do chat sobre o rascunho atual."""

    tipos: list[Literal["imagem", "arte", "legenda", "formato", "novo"]]
    instrucao_imagem: str
    tela_no_aparelho: bool
    fotos_ids: list[int]
    arte: CamposArteAjuste
    legenda_nova: str
    formato: Literal["feed", "story", "ambos"] | None
    pergunta: str
    resumo_mudanca: str


class PedidoImagemRascunho(BaseModel):
    marca_id: int = 0
    descricao: str = Field(min_length=2, max_length=1500)
    fotos_ids: list[int] = Field(default_factory=list, max_length=4)
    anexo_url: str = Field(default="", max_length=500)
    formato: str = Field(default="feed", max_length=20)


class PedidoArteAgenda(BaseModel):
    modelo: str = Field(default="", max_length=20)
    paleta: str = Field(default="", max_length=20)
    titulo_arte: str = Field(default="", max_length=80)
    subtitulo_arte: str = Field(default="", max_length=120)
    selo: str = Field(default="", max_length=30)
    chamada: str = Field(default="", max_length=40)
    antes: str = Field(default="", max_length=120)


class FatosMarca(BaseModel):
    oferta: str = Field(default="", max_length=2000)
    itens: str = Field(default="", max_length=4000)
    horarios: str = Field(default="", max_length=1000)
    diferenciais: str = Field(default="", max_length=2000)
    promocoes: str = Field(default="", max_length=2000)
    tom: str = Field(default="", max_length=1000)
    nunca: str = Field(default="", max_length=2000)
    canal: str = Field(default="", max_length=20)
    chamada: str = Field(default="", max_length=120)
    hashtags: str = Field(default="", max_length=80)
    soa: str = Field(default="", max_length=300)
    fora: str = Field(default="", max_length=300)


class ItemFotoMemoria(BaseModel):
    id: int
    url: str
    miniatura_url: str
    largura: int = 0
    altura: int = 0
    descricao: str = ""
    tags: str = ""
    tipo: str = "auto"
    papel: str = "auto"
    usar_em_posts: bool = True


class RespostaFotosMemoria(BaseModel):
    itens: list[ItemFotoMemoria]
    limite: int
    tipos: list[str]
    avisos: list[str] = Field(default_factory=list)


class PedidoFotoMemoria(BaseModel):
    descricao: str = Field(default="", max_length=1500)
    tags: str = Field(default="", max_length=500)
    tipo: str = Field(default="auto", max_length=30)
    papel: str = Field(default="auto", max_length=20)
    usar_em_posts: bool = True


class PedidoImagemMemoria(BaseModel):
    marca_id: int = 0
    foto_id: int
    gancho: str = Field(default="", max_length=80)


class PedidoFotoAgenda(BaseModel):
    foto_id: int | None = None


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


class CampanhaMemoria(BaseModel):
    titulo_anuncio: str
    legenda_redes: str
    cena: str
    angulo: str
    gancho: str
    plano_arte: PlanoArte


class CampanhaCompleta(CampanhaGerada):
    roteiro_video: RoteiroVideo = Field(default_factory=RoteiroVideo)
    google_meu_negocio: GoogleMeuNegocio = Field(default_factory=GoogleMeuNegocio)
    foto_url: str | None = None
    audio_url: str | None = None
    video_url: str | None = None
    aviso_midia: str | None = None
    cena: str = ""
    angulo: str = ""
    gancho: str = ""
    foto_id: int | None = None
    referencia_id: int | None = None
    memoria_miniatura: str = ""
    story_url: str = ""
    plano_arte: dict | None = None


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
    imagem_site: str = Field(default="", max_length=1000)
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
    imagem_site: str = ""
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
    imagem_site: str = Field(default="", max_length=1000)


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
    foto_id: int | None = None
    gancho: str = Field(default="", max_length=80)
    plano_arte: dict | None = None


class RespostaPublicacao(BaseModel):
    status: str
    detalhe: str = ""


class PedidoMarca(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    publico: str = Field(default="", max_length=200)
    site: str = Field(default="", max_length=200)
    marca_dagua: str = Field(default="", max_length=60)
    abrangencia: str = Field(default="cidade", pattern=r"^(cidade|estado|brasil)$")
    cidade: str = Field(default="", max_length=120)
    uf: str = Field(default="", max_length=2)
    texto: str = Field(min_length=20, max_length=8000)
    memoria: FatosMarca | None = None
    cor_principal: str = Field(default="", pattern=r"^(#[0-9a-fA-F]{6})?$")
    cor_destaque: str = Field(default="", pattern=r"^(#[0-9a-fA-F]{6})?$")
    moldes: str = Field(default="", max_length=80)
    logo_lado: str = Field(default="", pattern=r"^(esquerda|direita|centro)?$")


class ItemMarca(BaseModel):
    id: int
    nome: str
    publico: str
    site: str = ""
    marca_dagua: str = ""
    abrangencia: str = "cidade"
    cidade: str = ""
    uf: str = ""
    latitude: float | None = None
    longitude: float | None = None
    aviso_local: str = ""
    texto: str
    memoria: FatosMarca = Field(default_factory=FatosMarca)
    cor_principal: str = ""
    cor_destaque: str = ""
    moldes: str = ""
    logo_lado: str = ""
    logo_url: str = ""


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
    abrangencia: str = "cidade"
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
    foto_id: int | None = None
    gancho: str = Field(default="", max_length=80)
    plano_arte: dict | None = None

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
    pode_criar_ia: bool = False
    foto_id: int | None = None
    gancho: str = ""
    memoria_miniatura: str = ""
    legenda: str = ""
    arte: dict | None = None


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


class IdeiaResumo(BaseModel):
    tipo: str
    rotulo: str
    motivo: str = ""
    pedido: str
    tema: str = ""
    dia: str = ""
    sugestoes: list[int] = Field(default_factory=list)


class RespostaResumo(BaseModel):
    marca_id: int
    nome: str
    abrangencia: str = "cidade"
    local: str = ""
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
    ideias: list[IdeiaResumo] = Field(default_factory=list)
