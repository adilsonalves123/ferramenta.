from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Campanha, Ficha
from app.schemas import CampanhaCompleta, PedidoCampanha, PedidoFotoLegenda, RespostaFoto
from app.services import diretor
from app.services.imagens import url_memoria
from app.services.memoria import (
    CAMPOS_FATOS,
    artes_recentes,
    combinacoes_recentes,
    ler_fatos,
    limpar_gancho,
    montar_contexto,
    nome_do_dia,
    posts_recentes,
)
from app.services.midia import montar_midia
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


def _aviso_foto(erro: Exception) -> str:
    texto = str(erro)
    if "insufficient_quota" in texto or "credit_balance" in texto or "sem créditos" in texto:
        return "A arte não saiu: a conta da OpenAI está sem créditos."
    return "A arte não saiu agora. Use Criar foto com IA ou escolha uma foto da memória."


async def _com_memoria(pedido: PedidoCampanha, marca: Ficha, session: AsyncSession) -> CampanhaCompleta:
    contexto = await montar_contexto(session, marca)
    quando = ""
    if pedido.dia:
        quando = nome_do_dia(pedido.dia) + (f" às {pedido.hora}" if pedido.hora else "")
    recentes = await posts_recentes(session, marca.id or 0)
    artes = await artes_recentes(session, marca.id or 0)
    gerada = await gerar_campanha_memoria(
        pedido, contexto.regras() + "\n" + diretor.texto_kit(marca), contexto.texto, quando, recentes, artes
    )
    gancho = limpar_gancho(gerada.gancho)
    plano, legenda = diretor.validar_plano(
        gerada.plano_arte, contexto, diretor.pedido_do_usuario(pedido.produto), gerada.legenda_redes, gancho
    )
    plano = diretor.variar(plano, await combinacoes_recentes(session, marca.id or 0), marca)
    foto_id = plano.get("foto_base_id")
    completa = CampanhaCompleta(
        titulo_anuncio=gerada.titulo_anuncio,
        legenda_redes=legenda,
        cena=gerada.cena.strip()[:800],
        angulo=gerada.angulo.strip()[:200],
        gancho=gancho,
        foto_id=foto_id,
        referencia_id=(plano.get("fotos_estilo_ids") or [None])[0],
        plano_arte=diretor.resumo_plano(plano),
    )
    if foto_id:
        try:
            feito = await diretor.produzir(session, marca, plano, cena=completa.cena)
            completa.foto_url = feito["feed_url"]
            completa.story_url = feito["story_url"]
            completa.plano_arte = diretor.resumo_plano(feito)
            completa.memoria_miniatura = url_memoria(foto_id, mini=True)
        except Exception as erro:
            completa.aviso_midia = _aviso_foto(erro)
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
    """Criar foto com IA: edita a foto base do plano (ou cria uma) e monta a arte. Sem plano, usa um plano simples."""
    marca = await session.get(Ficha, pedido.marca_id) if pedido.marca_id > 0 else None
    contexto = await montar_contexto(session, marca)
    plano = diretor.do_cliente(pedido.plano_arte, contexto) or diretor.plano_padrao(pedido.legenda)
    cena = pedido.cena.strip()
    if not plano.get("foto_base_id") and not cena:
        cena = await cena_da_legenda(pedido.legenda, _sobre_marca(marca))
    sem_fotos = not plano.get("foto_base_id") and not plano.get("fotos_estilo_ids")
    if sem_fotos and pedido.referencia_id in contexto.lugar | contexto.estilo:
        plano["fotos_estilo_ids"] = [pedido.referencia_id]
    try:
        feito = await diretor.produzir(session, marca, plano, cena=cena, forcar_ia=True, reusar_base=False)
    except Exception as erro:
        texto = str(erro)
        if "insufficient_quota" in texto or "credit_balance" in texto or "sem créditos" in texto:
            detalhe = "A conta da OpenAI está sem créditos. Adicione créditos no site da OpenAI e tente de novo."
        else:
            detalhe = "Não consegui criar a foto agora. Tente de novo em instantes."
        raise HTTPException(status_code=502, detail=detalhe) from erro
    return RespostaFoto(foto_url=feito["feed_url"], story_url=feito["story_url"], plano_arte=diretor.resumo_plano(feito))
