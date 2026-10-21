from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Agendamento, Ficha, Rascunho
from app.schemas import (
    ItemAgenda,
    ItemPrateleira,
    PedidoAjuste,
    PedidoImagemRascunho,
    PedidoRascunho,
    PedidoRestaurar,
    PedidoSalvarRascunho,
    PedidoTresArtes,
    RespostaAjuste,
    RespostaPrateleira,
    RespostaRascunho,
    RespostaTresArtes,
    OpcaoArtePronta,
)
from app.services import rascunho as servico
from app.services.publicar import marca_efetiva

router = APIRouter()


async def _marca(session: AsyncSession, marca_id: int) -> Ficha | None:
    return await session.get(Ficha, marca_id) if marca_id > 0 else None


async def _rascunho(session: AsyncSession, rascunho_id: int) -> Rascunho:
    registro = await session.get(Rascunho, rascunho_id)
    if registro is None or registro.status != "aberto":
        raise HTTPException(status_code=404, detail="Esse rascunho não está mais aberto. Clique em Novo post.")
    return registro


def _falha_ia(erro: Exception, padrao: str) -> HTTPException:
    texto = str(erro)
    if "insufficient_quota" in texto or "credit_balance" in texto:
        return HTTPException(status_code=502, detail="A conta da OpenAI está sem créditos. Adicione créditos e tente de novo.")
    if isinstance(erro, (ValueError, RuntimeError)) and texto and "sk-" not in texto:
        return HTTPException(status_code=422, detail=texto[:200])
    return HTTPException(status_code=502, detail=padrao)


@router.get("/rascunho/atual", response_model=RespostaRascunho)
async def get_atual(marca_id: int = 0, session: AsyncSession = Depends(get_session)) -> RespostaRascunho:
    registro = await servico.atual(session, marca_id)
    return RespostaRascunho(rascunho=servico.para_item(registro) if registro else None)


@router.post("/rascunho", response_model=RespostaRascunho)
async def post_rascunho(pedido: PedidoRascunho, session: AsyncSession = Depends(get_session)) -> RespostaRascunho:
    registro = await servico.criar(
        session,
        await _marca(session, pedido.marca_id),
        pedido.legenda,
        pedido.plano_arte,
        pedido.formato,
        pedido.cena,
        pedido.gancho,
        pedido.descricao,
    )
    return RespostaRascunho(rascunho=servico.para_item(registro))


@router.post("/rascunho/imagem", response_model=RespostaRascunho)
async def post_rascunho_imagem(pedido: PedidoImagemRascunho, session: AsyncSession = Depends(get_session)) -> RespostaRascunho:
    """Criar imagem: descreve o que quer (pode mandar fotos da memória ou um anexo como base)."""
    try:
        registro = await servico.criar_imagem(
            session, await _marca(session, pedido.marca_id), pedido.descricao, pedido.fotos_ids, pedido.anexo_url, pedido.formato
        )
    except Exception as erro:
        await session.rollback()
        raise _falha_ia(erro, "Não consegui criar a imagem agora. Tente de novo em instantes.") from erro
    return RespostaRascunho(rascunho=servico.para_item(registro))


@router.post("/rascunho/artes", response_model=RespostaTresArtes)
async def post_tres_artes(pedido: PedidoTresArtes, session: AsyncSession = Depends(get_session)) -> RespostaTresArtes:
    """Criar arte: uma foto e três peças diferentes, no tamanho escolhido."""
    try:
        feito = await servico.criar_tres_artes(
            session,
            await _marca(session, pedido.marca_id),
            pedido.descricao,
            pedido.formato,
            pedido.fotos_ids,
            pedido.anexo_url,
        )
    except Exception as erro:
        await session.rollback()
        raise _falha_ia(erro, "Não consegui criar as artes agora. Tente de novo em instantes.") from erro
    return RespostaTresArtes(
        legenda=feito["legenda"],
        formato=feito["formato"],
        opcoes=[
            OpcaoArtePronta(
                plano_arte=servico.diretor.resumo_plano(plano) or plano,
                foto_url=plano.get("feed_url") or "",
                modelo=plano.get("modelo") or "",
            )
            for plano in feito["opcoes"]
        ],
    )


@router.get("/prateleira", response_model=RespostaPrateleira)
async def get_prateleira(marca_id: int = 0, session: AsyncSession = Depends(get_session)) -> RespostaPrateleira:
    itens = await servico.listar_prateleira(session, marca_id)
    return RespostaPrateleira(itens=[ItemPrateleira(**item) for item in itens])


@router.post("/rascunho/{rascunho_id}/guardar")
async def post_guardar(rascunho_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    """Tira do estúdio e deixa na prateleira, se a arte já existe."""
    registro = await session.get(Rascunho, rascunho_id)
    if registro is not None and registro.status == "aberto":
        plano = servico.plano_de(registro)
        registro.status = "guardado" if plano and (plano.get("feed_url") or plano.get("story_url")) else "fechado"
        await session.commit()
    return {"ok": True}


@router.post("/rascunho/{rascunho_id}/abrir", response_model=RespostaRascunho)
async def post_abrir(rascunho_id: int, session: AsyncSession = Depends(get_session)) -> RespostaRascunho:
    registro = await session.get(Rascunho, rascunho_id)
    if registro is None or registro.status not in {"aberto", "guardado"}:
        raise HTTPException(status_code=404, detail="Esse rascunho não está mais na prateleira.")
    registro = await servico.reabrir(session, registro)
    return RespostaRascunho(rascunho=servico.para_item(registro))


@router.put("/rascunho/{rascunho_id}", response_model=RespostaRascunho)
async def put_rascunho(
    rascunho_id: int, pedido: PedidoSalvarRascunho, session: AsyncSession = Depends(get_session)
) -> RespostaRascunho:
    registro = await _rascunho(session, rascunho_id)
    registro = await servico.salvar(
        session,
        registro,
        await _marca(session, registro.marca_id),
        pedido.legenda,
        pedido.plano_arte,
        pedido.formato,
        pedido.descricao,
        pedido.nova_versao,
    )
    return RespostaRascunho(rascunho=servico.para_item(registro))


@router.post("/rascunho/{rascunho_id}/ajustar", response_model=RespostaAjuste)
async def post_ajustar(
    rascunho_id: int, pedido: PedidoAjuste, session: AsyncSession = Depends(get_session)
) -> RespostaAjuste:
    """Cada mensagem do chat ajusta o rascunho atual: foto, arte, legenda ou formato. Só 'novo' recomeça."""
    registro = await _rascunho(session, rascunho_id)
    marca = await _marca(session, registro.marca_id)
    try:
        feito = await servico.ajustar(session, registro, marca, pedido.mensagem, pedido.fotos_ids)
    except Exception as erro:
        await session.rollback()
        raise _falha_ia(erro, "Não consegui ajustar o post agora. Tente de novo em instantes.") from erro
    await session.refresh(registro)
    return RespostaAjuste(rascunho=servico.para_item(registro), **feito)


@router.post("/rascunho/{rascunho_id}/restaurar", response_model=RespostaRascunho)
async def post_restaurar(
    rascunho_id: int, pedido: PedidoRestaurar, session: AsyncSession = Depends(get_session)
) -> RespostaRascunho:
    registro = await _rascunho(session, rascunho_id)
    try:
        registro = await servico.restaurar(session, registro, pedido.n)
    except ValueError as erro:
        raise HTTPException(status_code=404, detail=str(erro)) from erro
    return RespostaRascunho(rascunho=servico.para_item(registro))


@router.post("/rascunho/{rascunho_id}/fechar")
async def post_fechar(rascunho_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    registro = await session.get(Rascunho, rascunho_id)
    if registro is not None and registro.status == "aberto":
        registro.status = "fechado"
        await session.commit()
    return {"ok": True}


@router.post("/agenda/{item_id}/rascunho", response_model=RespostaRascunho)
async def post_rascunho_da_fila(item_id: int, session: AsyncSession = Depends(get_session)) -> RespostaRascunho:
    """Ajustar: abre o post da fila como rascunho na prévia."""
    registro = await session.get(Agendamento, item_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="Esse post não está na fila.")
    if registro.status not in {"na_fila", "falta_imagem"}:
        raise HTTPException(status_code=422, detail="Esse post não está mais na fila.")
    if registro.video_url:
        raise HTTPException(status_code=422, detail="Este post sai com vídeo.")
    marca = await _marca(session, await marca_efetiva(session, registro.marca_id))
    try:
        rascunho = await servico.abrir_da_fila(session, registro, marca)
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    return RespostaRascunho(rascunho=servico.para_item(rascunho))


@router.post("/rascunho/{rascunho_id}/salvar-fila", response_model=ItemAgenda)
async def post_salvar_fila(rascunho_id: int, session: AsyncSession = Depends(get_session)) -> ItemAgenda:
    from app.routers.agenda import _item

    rascunho = await _rascunho(session, rascunho_id)
    try:
        registro = await servico.salvar_na_fila(session, rascunho)
    except LookupError as erro:
        raise HTTPException(status_code=404, detail=str(erro)) from erro
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    return _item(registro)
