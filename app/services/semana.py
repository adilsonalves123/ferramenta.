from datetime import datetime, timedelta, timezone

from openai import APIError
from pydantic import BaseModel
from sqlalchemy import delete
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.models import (
    Agendamento,
    ContaRede,
    Ficha,
    Imagem,
    LogoMarca,
    MemoriaFoto,
    Publicacao,
    Rascunho,
    Sugestao,
    agora,
)
from app.schemas import PlanoArte
from app.services import diretor
from app.services.memoria import (
    ContextoMemoria,
    artes_recentes,
    combinacoes_recentes,
    gravar_fatos,
    limpar_gancho,
    montar_contexto,
    nome_do_dia,
    posts_recentes,
)
from app.services.openai_campanha import REGRAS_ALCOOL, cliente_openai, com_moderacao, fala_de_alcool

ZONA = timezone(timedelta(hours=-3))

NOME_PADRAO = "Minha marca"

SISTEMA = """Você monta publicações para Instagram e Facebook, em português do Brasil.
Cada item precisa de titulo, legenda e cena.
titulo: uma frase curta que nomeia o item do post.
legenda: siga o contrato da memória. Sem memória, primeira linha com o item do pedido, um parágrafo com um detalhe concreto e no máximo 2 hashtags do assunto. Sem "Peça já", "Saiba mais" ou "Responda QUERO".
cena: descrição de uma fotografia realista, diferente da anterior, que combina com o negócio da ficha. Se aparecer celular ou tela, ela fica virada ou apagada. Sem gráfico, sem botão, sem menu e sem texto escrito.
Use somente a ficha. Não invente preço, cidade, telefone, depoimento ou número de clientes.
Não invente horário de funcionamento, preço, desconto nem promoção que não esteja na ficha ou no contexto.
Não repita o mesmo assunto visual."""


class PosteGerado(BaseModel):
    titulo: str
    legenda: str
    cena: str
    angulo: str
    gancho: str
    plano_arte: PlanoArte


class PacoteSemana(BaseModel):
    posts: list[PosteGerado]


async def listar_marcas(session: AsyncSession) -> list[Ficha]:
    resultado = await session.exec(select(Ficha).order_by(Ficha.id))
    marcas = list(resultado.all())
    if not marcas:
        primeira = Ficha(nome=NOME_PADRAO, publico="", site="", marca_dagua="", texto="")
        session.add(primeira)
        await session.commit()
        await session.refresh(primeira)
        return [primeira]
    mudou = False
    for indice, marca in enumerate(marcas):
        if not (marca.nome or "").strip():
            marca.nome = NOME_PADRAO if indice == 0 else f"Marca {marca.id}"
            mudou = True
    if mudou:
        await session.commit()
    return marcas


async def marca_por_id(session: AsyncSession, marca_id: int) -> Ficha | None:
    marcas = await listar_marcas(session)
    for marca in marcas:
        if marca.id == marca_id:
            return marca
    return marcas[0] if marcas else None


async def criar_marca(
    session: AsyncSession,
    nome: str,
    publico: str,
    texto: str,
    site: str = "",
    marca_dagua: str = "",
    memoria: dict[str, str] | None = None,
    cores: tuple[str, str] | None = None,
    moldes: str = "",
    logo_lado: str = "",
) -> Ficha:
    registro = Ficha(
        nome=nome.strip()[:120],
        publico=publico.strip()[:200],
        site=site.strip()[:200],
        marca_dagua=marca_dagua.strip()[:60],
        texto=texto.strip(),
    )
    if memoria is not None:
        gravar_fatos(registro, memoria)
    if cores is not None:
        registro.cor_principal, registro.cor_destaque = (cor.strip().upper()[:7] for cor in cores)
    registro.moldes = diretor.normalizar_moldes(moldes)
    registro.logo_lado = logo_lado if logo_lado in diretor.LADOS_LOGO else ""
    session.add(registro)
    await session.commit()
    await session.refresh(registro)
    return registro


async def atualizar_marca(
    session: AsyncSession,
    marca_id: int,
    nome: str,
    publico: str,
    texto: str,
    site: str = "",
    marca_dagua: str = "",
    memoria: dict[str, str] | None = None,
    cores: tuple[str, str] | None = None,
    moldes: str = "",
    logo_lado: str = "",
) -> Ficha | None:
    resultado = await session.exec(select(Ficha).where(Ficha.id == marca_id))
    registro = resultado.first()
    if registro is None:
        return None
    registro.nome = nome.strip()[:120]
    registro.publico = publico.strip()[:200]
    registro.site = site.strip()[:200]
    registro.marca_dagua = marca_dagua.strip()[:60]
    registro.texto = texto.strip()
    if memoria is not None:
        gravar_fatos(registro, memoria)
    if cores is not None:
        registro.cor_principal, registro.cor_destaque = (cor.strip().upper()[:7] for cor in cores)
    registro.moldes = diretor.normalizar_moldes(moldes)
    registro.logo_lado = logo_lado if logo_lado in diretor.LADOS_LOGO else ""
    registro.atualizado_em = agora()
    await session.commit()
    await session.refresh(registro)
    return registro


async def apagar_marca(session: AsyncSession, marca_id: int) -> None:
    marcas = await listar_marcas(session)
    if len(marcas) <= 1:
        raise ValueError("Precisa ficar pelo menos uma marca.")
    alvo = next((marca for marca in marcas if marca.id == marca_id), None)
    if alvo is None:
        raise ValueError("Essa marca não existe mais.")
    fila = await session.exec(
        select(Agendamento).where(
            Agendamento.marca_id == marca_id, col(Agendamento.status).in_(["na_fila", "publicando"])
        )
    )
    if fila.first() is not None:
        raise ValueError("Essa marca tem post na fila. Cancele os posts dela antes.")
    contas = await session.exec(select(ContaRede).where(ContaRede.marca_id == marca_id))
    for conta in contas.all():
        await session.delete(conta)
    publicacoes = await session.exec(select(Publicacao).where(Publicacao.marca_id == marca_id))
    for publicacao in publicacoes.all():
        await session.delete(publicacao)
    sugestoes = await session.exec(select(Sugestao).where(Sugestao.marca_id == marca_id))
    for sugestao in sugestoes.all():
        await session.delete(sugestao)
    await session.exec(delete(MemoriaFoto).where(MemoriaFoto.marca_id == marca_id))
    await session.exec(delete(Imagem).where(Imagem.marca_id == marca_id))
    await session.exec(delete(LogoMarca).where(LogoMarca.marca_id == marca_id))
    await session.exec(delete(Rascunho).where(Rascunho.marca_id == marca_id))
    await session.delete(alvo)
    await session.commit()


TEMAS = {
    "bastidores": "Bastidores: mostra a rotina real do negócio, quem faz e como é feito.",
    "oferta": "Oferta: convida para conhecer ou comprar o que a marca oferece, sem inventar preço nem desconto.",
    "dica": "Dica: ensina algo útil para o público da marca, ligado ao que ela faz.",
    "história": "História: conta uma situação comum do dia a dia do público, com começo, problema e solução.",
    "prova": "Prova: mostra o resultado de usar o produto ou serviço, sem inventar números nem depoimentos.",
    "interação": "Interação: faz uma pergunta simples para o público responder nos comentários.",
}


async def escrever_semana(
    ficha: str,
    quantidade: int,
    tema: str = "",
    site: str = "",
    contexto: str = "",
    memoria: ContextoMemoria | None = None,
    quando: list[str] | None = None,
    recentes: list[str] | None = None,
    artes: list[str] | None = None,
    kit: str = "",
) -> list[tuple[PosteGerado, dict]]:
    """Escreve os posts e o plano de arte de cada um. Devolve (post, plano validado)."""
    memoria = memoria or ContextoMemoria()
    guia = TEMAS.get(tema.strip().lower(), "")
    pedido_tema = f"\nTema de todas as publicações: {guia}" if guia else ""
    if site.strip():
        pedido_tema += "\nNão escreva o site nem link na legenda: o site da marca entra sozinho no final."
    if contexto.strip():
        pedido_tema += f"\nMotivo deste post: {contexto.strip()[:1500]}"
    if quando:
        pedido_tema += "\nDia de cada publicação, na ordem:\n" + "\n".join(
            f"{numero}. {texto}" for numero, texto in enumerate(quando, start=1)
        )
    if recentes:
        pedido_tema += "\nPosts recentes desta marca (não repita o ângulo nem o gancho):\n" + "\n".join(
            f"- {linha}" for linha in recentes
        )
    if artes:
        pedido_tema += "\nÚltimas artes desta marca (não repita a frase; o molde fica no kit):\n" + "\n".join(
            f"- {linha}" for linha in artes
        )
    if kit.strip():
        pedido_tema += "\n" + kit.strip()
    bloco_memoria = f"\n\n{memoria.texto[:14000]}" if memoria.tem_memoria else ""
    alcool = fala_de_alcool(ficha) or fala_de_alcool(contexto) or fala_de_alcool(memoria.texto)
    sistema = SISTEMA + memoria.regras() + diretor.REGRAS_ARTE
    if alcool:
        sistema += REGRAS_ALCOOL + diretor.REGRAS_ARTE_ALCOOL
    resposta = await cliente_openai().with_options(timeout=90).chat.completions.parse(
        model=get_settings().openai_model,
        messages=[
            {"role": "system", "content": sistema},
            {
                "role": "user",
                "content": (
                    f"Ficha do negócio:\n{ficha.strip()[:6000]}{bloco_memoria}\n\n"
                    f"Crie exatamente {quantidade} publicações diferentes, nesta ordem.{pedido_tema}"
                ),
            },
        ],
        response_format=PacoteSemana,
    )
    mensagem = resposta.choices[0].message
    if mensagem.refusal:
        raise ValueError("A semana não pôde ser escrita.")
    if mensagem.parsed is None:
        raise ValueError("A semana não veio no formato esperado.")
    posts = [
        item
        for item in mensagem.parsed.posts
        if item.titulo.strip() and item.legenda.strip() and item.cena.strip()
    ]
    if not posts:
        raise ValueError("A semana veio sem texto.")
    if alcool:
        posts = [item.model_copy(update={"legenda": com_moderacao(item.legenda)}) for item in posts]
    validados = []
    for item in posts[:quantidade]:
        gancho = limpar_gancho(item.gancho)
        plano, legenda = diretor.validar_plano(item.plano_arte, memoria, contexto, item.legenda, gancho)
        validados.append((item.model_copy(update={"gancho": gancho, "legenda": legenda}), plano))
    return validados


def _data_dia(dia: str):
    ano, mes, dia_mes = (int(parte) for parte in dia.split("-"))
    return datetime(ano, mes, dia_mes, tzinfo=ZONA).date()


async def montar_dias(
    session: AsyncSession,
    pedidos: list[tuple[str, str]],
    rede: str = "Ambos",
    formato: str = "feed",
    tema: str = "",
    marca_id: int = 0,
    contexto: str = "",
) -> tuple[list[Agendamento], str]:
    if not pedidos:
        raise ValueError("Marque pelo menos um dia.")
    if len(pedidos) > 14:
        raise ValueError("Marque no máximo 14 dias.")
    dias = [dia for dia, _hora in pedidos]
    if len(pedidos) != len(set(pedidos)):
        raise ValueError("Esse dia e horário foram marcados duas vezes.")
    agora_local = datetime.now(ZONA)
    limite = agora_local.date() + timedelta(days=21)
    from app.services.publicar import momento

    for dia, hora in pedidos:
        data = _data_dia(dia)
        if data < agora_local.date() or data > limite:
            raise ValueError("Escolha um dia dos próximos 21 dias.")
        if momento(dia, hora) <= agora_local:
            raise ValueError("Um desses horários já passou.")
    marca = await marca_por_id(session, marca_id)
    if marca is None:
        raise ValueError("Crie uma marca em Minha marca antes.")
    existentes = await session.exec(
        select(Agendamento).where(col(Agendamento.dia).in_(dias), Agendamento.marca_id == (marca.id or 0))
    )
    ocupados = {(item.dia, item.hora) for item in existentes.all()}
    repetidos = [par for par in pedidos if par in ocupados]
    cabecalho = f"Marca: {marca.nome}."
    if (marca.publico or "").strip():
        cabecalho += f" Público: {marca.publico}."
    ficha = f"{cabecalho}\n{(marca.texto or '').strip()}".strip()
    memoria = await montar_contexto(session, marca)
    recentes = await posts_recentes(session, marca.id or 0)
    artes = await artes_recentes(session, marca.id or 0)
    quando = [f"{nome_do_dia(dia)} às {hora}" for dia, hora in pedidos]
    try:
        posts = await escrever_semana(
            ficha, len(pedidos), tema, marca.site or "", contexto, memoria, quando, recentes, artes, diretor.texto_kit(marca)
        )
    except APIError as erro:
        detalhe = str(erro)
        if "insufficient_quota" in detalhe or "credit_balance" in detalhe:
            raise ValueError("A conta da OpenAI está sem créditos.") from erro
        raise ValueError("Os posts não puderam ser escritos agora.") from erro
    criados: list[Agendamento] = []
    planos: list[dict] = []
    combinacoes = await combinacoes_recentes(session, marca.id or 0)
    for (dia, hora), (post, plano) in zip(pedidos, posts, strict=False):
        plano = diretor.variar(plano, combinacoes, marca)
        combinacoes = [(plano["modelo"], plano.get("paleta") or "marca"), *combinacoes][:5]
        registro = Agendamento(
            dia=dia,
            hora=hora,
            rede=rede,
            formato=formato,
            tema=tema,
            marca_id=marca.id or 0,
            titulo=post.titulo.strip()[:300],
            legenda=post.legenda.strip(),
            cena=post.cena.strip()[:800],
            foto_id=plano.get("foto_base_id"),
            referencia_id=(plano.get("fotos_estilo_ids") or [None])[0],
            gancho=post.gancho[:80],
            plano_arte=diretor.gravar_plano(plano),
            status="na_fila",
        )
        session.add(registro)
        criados.append(registro)
        planos.append(plano)
    await session.commit()
    for registro in criados:
        await session.refresh(registro)
    prontas = 0
    for registro, plano in zip(criados, planos):
        if await diretor.vai_usar_ia(session, marca.id or 0, plano):
            continue
        try:
            feito = await diretor.produzir(session, marca, plano, cena=registro.cena)
        except Exception:
            continue
        registro.imagem_url = feito["feed_url"][:500]
        registro.plano_arte = diretor.gravar_plano(feito)
        prontas += 1
    await session.commit()
    avisos_conar = [plano["aviso"] for plano in planos if plano.get("aviso")]
    if prontas == len(criados):
        aviso = f"Post de {marca.nome} na fila, com a arte pronta. Você pode revisar, trocar a foto e cancelar antes de sair."
    else:
        aviso = (
            f"Post de {marca.nome} na fila. A IA prepara a foto e monta a arte até 3 horas antes do horário. "
            "Você pode revisar e cancelar antes de sair."
        )
    if avisos_conar:
        aviso += " " + avisos_conar[0]
    if len(criados) < len(pedidos):
        aviso = "Parte dos dias entrou na fila. " + aviso
    if repetidos:
        aviso += f" Atenção: {marca.nome} já tinha post nesse dia e horário."
    return criados, aviso
