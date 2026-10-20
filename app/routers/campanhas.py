from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Campanha, Ficha
from app.schemas import CampanhaCompleta, PedidoCampanha, PedidoFotoLegenda, RespostaFoto
from app.services.imagens import url_memoria
from app.services.memoria import (
    CAMPOS_FATOS,
    bytes_referencia,
    imagem_da_memoria,
    ler_fatos,
    limpar_gancho,
    montar_contexto,
    nome_do_dia,
    posts_recentes,
)
from app.services.midia import foto_da_cena, montar_midia
from app.services.openai_campanha import cena_da_legenda, gerar_campanha, gerar_campanha_memoria
from app.services.semana import TEMAS

router = APIRouter()


def _erro_ia(erro: Exception, padrao: str) -> HTTPException:
    texto = str(erro)
    if "insufficient_quota" in texto or "credit_balance_exhausted" in texto:
        detalhe = "A conta da OpenAI está sem créditos. Adicione créditos no site da OpenAI e tente de novo."
    else:
        detalhe = padrao
    return HTTPException(status_code=502, detail=detalhe)


async def _com_memoria(pedido: PedidoCampanha, marca: Ficha, session: AsyncSession) -> CampanhaCompleta:
    contexto = await montar_contexto(session, marca)
    quando = ""
    if pedido.dia:
        quando = nome_do_dia(pedido.dia) + (f" às {pedido.hora}" if pedido.hora else "")
    recentes = await posts_recentes(session, marca.id or 0)
    gerada = await gerar_campanha_memoria(pedido, contexto.regras(), contexto.texto, quando, recentes)
    foto_id, referencia_id = contexto.validar(gerada.foto_id, gerada.referencia_id)
    gancho = limpar_gancho(gerada.gancho)
    completa = CampanhaCompleta(
        titulo_anuncio=gerada.titulo_anuncio,
        legenda_redes=gerada.legenda_redes,
        cena=gerada.cena.strip()[:800],
        angulo=gerada.angulo.strip()[:200],
        gancho=gancho,
        foto_id=foto_id,
        referencia_id=referencia_id,
    )
    if foto_id:
        try:
            completa.foto_url = await imagem_da_memoria(session, foto_id, gancho, marca)
            completa.memoria_miniatura = url_memoria(foto_id, mini=True)
        except Exception:
            completa.foto_id = None
    return completa


@router.post("/gerar-campanha", response_model=CampanhaCompleta)
async def post_gerar_campanha(
    pedido: PedidoCampanha,
    session: AsyncSession = Depends(get_session),
) -> CampanhaCompleta:
    if pedido.acao == "foto":
        midia = await montar_midia(pedido, pedido.marca_id)
        return CampanhaCompleta(titulo_anuncio="", legenda_redes="", **midia)

    guia = TEMAS.get(pedido.tema.strip().lower(), "")
    if guia:
        pedido = pedido.model_copy(update={"produto": f"{pedido.produto.strip()}\nTipo de post: {guia}"})
    marca = await session.get(Ficha, pedido.marca_id) if pedido.marca_id > 0 else None
    try:
        if marca is not None:
            completa = await _com_memoria(pedido, marca, session)
        else:
            completa = CampanhaCompleta(**(await gerar_campanha(pedido)).model_dump())
    except ValueError as erro:
        raise HTTPException(status_code=502, detail=str(erro)) from erro
    except Exception as erro:
        raise _erro_ia(erro, "Não foi possível gerar a campanha agora.") from erro

    session.add(
        Campanha(
            nicho=pedido.nicho.strip(),
            produto=pedido.produto.strip(),
            titulo_anuncio=completa.titulo_anuncio,
            legenda_redes=completa.legenda_redes,
            gmn_nome="",
            gmn_categoria="",
            gmn_descricao="",
        )
    )
    try:
        await session.commit()
    except SQLAlchemyError as erro:
        await session.rollback()
        raise HTTPException(status_code=503, detail="A campanha foi gerada, mas não entrou no banco.") from erro
    return completa


def _sobre_marca(marca: Ficha | None) -> str:
    if marca is None:
        return ""
    fatos = ler_fatos(marca)
    linhas = [f"{rotulo}: {fatos[chave]}" for chave, rotulo in CAMPOS_FATOS if fatos[chave] and chave != "nunca"]
    return "\n".join([(marca.texto or "").strip()[:1200], *linhas]).strip()


@router.post("/foto-legenda", response_model=RespostaFoto)
async def post_foto_legenda(
    pedido: PedidoFotoLegenda,
    session: AsyncSession = Depends(get_session),
) -> RespostaFoto:
    marca = await session.get(Ficha, pedido.marca_id) if pedido.marca_id > 0 else None
    cena = pedido.cena.strip() or await cena_da_legenda(pedido.legenda, _sobre_marca(marca))
    marca_id = marca.id if marca and marca.id else 0
    referencia = await bytes_referencia(session, marca_id, pedido.referencia_id, f"{cena} {pedido.legenda}")
    try:
        url = await foto_da_cena(cena, referencia, marca_id)
    except Exception as erro:
        texto = str(erro)
        if "insufficient_quota" in texto or "credit_balance" in texto or "sem créditos" in texto:
            detalhe = "A conta da OpenAI está sem créditos. Adicione créditos no site da OpenAI e tente de novo."
        else:
            detalhe = "Não consegui criar a foto agora. Tente de novo em instantes."
        raise HTTPException(status_code=502, detail=detalhe) from erro
    return RespostaFoto(foto_url=url)
