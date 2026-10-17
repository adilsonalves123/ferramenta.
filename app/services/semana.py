from datetime import datetime, timedelta, timezone

from openai import APIError
from pydantic import BaseModel
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.models import Agendamento, ContaRede, Ficha, agora
from app.services.openai_campanha import cliente_openai

ZONA = timezone(timedelta(hours=-3))

FICHA_PADRAO = """Risca Aí é um sistema para o dono de bar, adega, restaurante ou petiscaria controlar o caixa, o estoque, as mesas e os fiados. Ele acompanha a venda no celular. O site é https://www.riscaai.com.br

Não invente preço, cidade, telefone, depoimento nem quantidade de clientes. Os preços de um cardápio de demonstração não são o preço do sistema.

Cada publicação mostra um assunto visual diferente, nesta rotação: o caixa do bar aberto no celular em cima do balcão; o fiado organizado, sem papel solto; as mesas com a comanda; o estoque de bebidas; o dono olhando o movimento do salão; o balcão no fim da tarde. A foto é realista, sem texto, sem letra, sem número, sem logotipo e sem tela de aplicativo com palavras."""

SISTEMA = """Você monta publicações para Instagram e Facebook, em português do Brasil.
Cada item precisa de titulo, legenda e cena.
titulo: uma frase curta.
legenda: texto de post, com 5 a 8 hashtags no final.
cena: descrição de uma fotografia realista, diferente da anterior. O celular pode aparecer, mas a tela fica virada ou apagada. Sem tela acesa, sem gráfico, sem botão, sem menu, sem caderno aberto e sem texto escrito.
Use somente a ficha. Não invente preço, cidade, telefone, depoimento ou número de clientes.
Não repita o mesmo assunto visual."""


class PosteGerado(BaseModel):
    titulo: str
    legenda: str
    cena: str


class PacoteSemana(BaseModel):
    posts: list[PosteGerado]


async def listar_marcas(session: AsyncSession) -> list[Ficha]:
    resultado = await session.exec(select(Ficha).order_by(Ficha.id))
    marcas = list(resultado.all())
    if not marcas:
        primeira = Ficha(nome="Risca Aí", publico="Donos de bar no Brasil", texto=FICHA_PADRAO)
        session.add(primeira)
        await session.commit()
        await session.refresh(primeira)
        return [primeira]
    mudou = False
    for indice, marca in enumerate(marcas):
        if not (marca.nome or "").strip():
            marca.nome = "Risca Aí" if indice == 0 else f"Marca {marca.id}"
            if indice == 0 and not (marca.publico or "").strip():
                marca.publico = "Donos de bar no Brasil"
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


async def criar_marca(session: AsyncSession, nome: str, publico: str, texto: str) -> Ficha:
    registro = Ficha(nome=nome.strip()[:120], publico=publico.strip()[:200], texto=texto.strip())
    session.add(registro)
    await session.commit()
    await session.refresh(registro)
    return registro


async def atualizar_marca(session: AsyncSession, marca_id: int, nome: str, publico: str, texto: str) -> Ficha | None:
    resultado = await session.exec(select(Ficha).where(Ficha.id == marca_id))
    registro = resultado.first()
    if registro is None:
        return None
    registro.nome = nome.strip()[:120]
    registro.publico = publico.strip()[:200]
    registro.texto = texto.strip()
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
        select(Agendamento).where(Agendamento.marca_id == marca_id, Agendamento.status == "na_fila")
    )
    if fila.first() is not None:
        raise ValueError("Essa marca tem post na fila. Cancele os posts dela antes.")
    contas = await session.exec(select(ContaRede).where(ContaRede.marca_id == marca_id))
    for conta in contas.all():
        await session.delete(conta)
    await session.delete(alvo)
    await session.commit()


async def texto_ficha(session: AsyncSession) -> str:
    marcas = await listar_marcas(session)
    texto = (marcas[0].texto if marcas else "").strip()
    return texto or FICHA_PADRAO


async def guardar_ficha(session: AsyncSession, texto: str) -> str:
    limpo = texto.strip()
    resultado = await session.exec(select(Ficha).order_by(Ficha.id))
    registro = resultado.first()
    if registro is None:
        registro = Ficha(texto=limpo)
        session.add(registro)
    else:
        registro.texto = limpo
        registro.atualizado_em = agora()
    await session.commit()
    await session.refresh(registro)
    return registro.texto


TEMAS = {
    "bastidores": "Bastidores: mostra a rotina real do bar, como o dono organiza o dia.",
    "oferta": "Oferta: chama para conhecer o sistema, sem inventar preço nem desconto.",
    "dica": "Dica: ensina algo útil ao dono de bar sobre caixa, estoque, mesas ou fiado.",
    "história": "História: conta uma situação comum de bar, com começo, problema e solução.",
    "prova": "Prova: mostra o resultado de usar o sistema, sem inventar números nem depoimentos.",
}


async def escrever_semana(ficha: str, quantidade: int, tema: str = "") -> list[PosteGerado]:
    guia = TEMAS.get(tema.strip().lower(), "")
    pedido_tema = f"\nTema de todas as publicações: {guia}" if guia else ""
    resposta = await cliente_openai().with_options(timeout=90).chat.completions.parse(
        model=get_settings().openai_model,
        messages=[
            {"role": "system", "content": SISTEMA},
            {
                "role": "user",
                "content": (
                    f"Ficha do negócio:\n{ficha.strip()[:6000]}\n\n"
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
    return posts[:quantidade]


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
) -> tuple[list[Agendamento], str]:
    if not pedidos:
        raise ValueError("Marque pelo menos um dia.")
    if len(pedidos) > 14:
        raise ValueError("Marque no máximo 14 dias.")
    dias = [dia for dia, _hora in pedidos]
    if len(dias) != len(set(dias)):
        raise ValueError("Esse dia foi marcado duas vezes.")
    agora_local = datetime.now(ZONA)
    limite = agora_local.date() + timedelta(days=21)
    from app.services.publicar import momento

    for dia, hora in pedidos:
        data = _data_dia(dia)
        if data < agora_local.date() or data > limite:
            raise ValueError("Escolha um dia dos próximos 21 dias.")
        if momento(dia, hora) <= agora_local:
            raise ValueError("Um desses horários já passou.")
    resultado = await session.exec(select(Agendamento).where(col(Agendamento.dia).in_(dias)))
    if resultado.first() is not None:
        raise ValueError("Um desses dias já tem publicação. Cancele primeiro.")
    marca = await marca_por_id(session, marca_id)
    if marca is None:
        raise ValueError("Crie uma marca no Prompt antes.")
    cabecalho = f"Marca: {marca.nome}."
    if (marca.publico or "").strip():
        cabecalho += f" Público: {marca.publico}."
    ficha = f"{cabecalho}\n{(marca.texto or '').strip() or FICHA_PADRAO}"
    try:
        posts = await escrever_semana(ficha, len(pedidos), tema)
    except APIError as erro:
        detalhe = str(erro)
        if "insufficient_quota" in detalhe or "credit_balance" in detalhe:
            raise ValueError("A conta da OpenAI está sem créditos.") from erro
        raise ValueError("Os posts não puderam ser escritos agora.") from erro
    criados: list[Agendamento] = []
    for (dia, hora), post in zip(pedidos, posts, strict=False):
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
            status="na_fila",
        )
        session.add(registro)
        criados.append(registro)
    await session.commit()
    for registro in criados:
        await session.refresh(registro)
    aviso = f"Post de {marca.nome} na fila. A foto é criada perto do horário marcado."
    if len(criados) < len(pedidos):
        aviso = "Parte dos dias entrou na fila. " + aviso
    return criados, aviso
