from html import escape

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.models import Agendamento
from app.schemas import (
    EstadoRede,
    ItemAgenda,
    PedidoAgenda,
    ItemMarca,
    PedidoFormato,
    PedidoMarca,
    PedidoPagina,
    PedidoPublicarAgora,
    PedidoSemana,
    RespostaAgenda,
    RespostaMarcas,
    RespostaPaginas,
    RespostaPublicacao,
    RespostaSemana,
    PosteSemana,
)
from app.services.calendario import ajustar_local, aviso_local, mudar_status, uf_valida
from app.services.midia import salvar_anexo
from app.services.publicar import (
    escolher_pagina_facebook,
    estado_contas,
    facebook_pronta,
    falta_conta,
    guardar_codigo,
    guardar_codigo_facebook,
    marca_do_state,
    marca_do_state_facebook,
    marca_efetiva,
    meta_pronta,
    paginas_facebook,
    publicar_agora,
    trocar_foto,
    url_oauth,
    url_oauth_facebook,
)
from app.services.semana import (
    apagar_marca,
    atualizar_marca,
    criar_marca,
    listar_marcas,
    montar_dias,
)

router = APIRouter()

PAGINA_FALTA = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <title>Contas · __PRODUTO__</title>
  <style>
    body { margin: 0; font-family: Inter, Segoe UI, sans-serif; background: #f3f4f6; color: #1a1d23; }
    main { max-width: 460px; margin: 64px auto; background: white; border: 1px solid #e6e8ee; border-radius: 16px; padding: 28px; }
    a { color: #1d4ed8; font-weight: 600; }
  </style>
</head>
<body>
  <main>
    <h1>A agenda já guarda o dia e o horário</h1>
    <p>Para o post sair sozinho, falta criar o aplicativo na Meta e ligar o Instagram e o Facebook.</p>
    <p><a href="/">Voltar ao __PRODUTO__</a></p>
  </main>
</body>
</html>
"""


def _pagina_falta() -> str:
    return PAGINA_FALTA.replace("__PRODUTO__", escape(get_settings().produto_nome))


def _item(registro: Agendamento) -> ItemAgenda:
    return ItemAgenda(
        id=registro.id or 0,
        dia=registro.dia,
        hora=registro.hora,
        rede=registro.rede,
        formato=registro.formato or "feed",
        tema=registro.tema or "",
        marca_id=registro.marca_id or 0,
        titulo=registro.titulo,
        status=registro.status,
        detalhe=registro.detalhe,
        aguarda_foto=bool((registro.cena or "").strip()) and not registro.imagem_url and not registro.video_url,
        imagem_url=registro.imagem_url or "",
        pode_trocar_foto=bool((registro.cena or "").strip()) and not registro.video_url and bool(registro.imagem_url),
    )


def _estado(conta) -> EstadoRede:
    if conta is None or not conta.token:
        return EstadoRede(ligado=False)
    return EstadoRede(ligado=True, nome=conta.nome)


@router.post("/anexo")
async def post_anexo(arquivo: UploadFile = File()) -> dict[str, str]:
    dados = await arquivo.read(25_000_001)
    if len(dados) > 25_000_000:
        raise HTTPException(status_code=422, detail="O vídeo precisa ter até 25 MB.")
    if (arquivo.content_type or "").lower() == "video/webm" or (arquivo.filename or "").lower().endswith(".webm"):
        raise HTTPException(status_code=422, detail="Envie o vídeo em MP4.")
    try:
        url, tipo = salvar_anexo(dados)
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    return {"url": url, "tipo": tipo}


@router.get("/agenda", response_model=RespostaAgenda)
async def get_agenda(marca_id: int = 0, session: AsyncSession = Depends(get_session)) -> RespostaAgenda:
    resultado = await session.exec(
        select(Agendamento).order_by(Agendamento.dia, Agendamento.hora, Agendamento.id)
    )
    contas = await estado_contas(session, await marca_efetiva(session, marca_id))
    return RespostaAgenda(
        itens=[_item(item) for item in resultado.all()],
        instagram=_estado(contas["instagram"]),
        facebook=_estado(contas["facebook"]),
        meta_pronta=meta_pronta(),
    )


@router.post("/agenda", response_model=ItemAgenda)
async def post_agenda(
    pedido: PedidoAgenda,
    session: AsyncSession = Depends(get_session),
) -> ItemAgenda:
    try:
        hora = pedido.hora_normalizada()
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    registro = Agendamento(
        dia=pedido.dia,
        hora=hora,
        rede=pedido.rede.strip(),
        titulo=pedido.titulo.strip(),
        legenda=pedido.legenda.strip(),
        imagem_url=pedido.imagem_url.strip()[:500],
        video_url=pedido.video_url.strip()[:500],
        formato=pedido.formato if pedido.formato in {"feed", "story", "ambos"} else "feed",
        tema=pedido.tema.strip()[:30],
        marca_id=pedido.marca_id,
    )
    session.add(registro)
    formato = registro.formato or "feed"
    precisa_foto = formato in {"story", "ambos"} or registro.rede in {"Instagram", "Ambos"}
    contas = await estado_contas(session, await marca_efetiva(session, registro.marca_id))
    faltam = []
    if registro.rede in ("Instagram", "Ambos") and not _estado(contas["instagram"]).ligado:
        faltam.append(falta_conta("Instagram"))
    if registro.rede in ("Facebook", "Ambos") and not _estado(contas["facebook"]).ligado:
        faltam.append(falta_conta("Facebook"))
    if precisa_foto and not registro.imagem_url and not registro.video_url:
        registro.status = "falta_imagem"
        registro.detalhe = "Falta a foto."
    elif faltam:
        registro.detalhe = " ".join(faltam)
    elif registro.rede not in ("Instagram", "Facebook", "Ambos"):
        registro.status = "manual"
        registro.detalhe = "Essa rede ainda não publica sozinha."
    await session.commit()
    await session.refresh(registro)
    return _item(registro)


@router.post("/publicar-agora", response_model=RespostaPublicacao)
async def post_publicar_agora(
    pedido: PedidoPublicarAgora,
    session: AsyncSession = Depends(get_session),
) -> RespostaPublicacao:
    status, detalhe = await publicar_agora(
        session,
        pedido.rede.strip(),
        pedido.titulo,
        pedido.legenda,
        pedido.imagem_url,
        pedido.video_url,
        pedido.formato,
        pedido.marca_id,
        pedido.tema.strip()[:30],
    )
    return RespostaPublicacao(status=status, detalhe=detalhe)


def _marca(registro, aviso: str = "") -> ItemMarca:
    return ItemMarca(
        id=registro.id or 0,
        nome=registro.nome or "",
        publico=registro.publico or "",
        site=registro.site or "",
        marca_dagua=registro.marca_dagua or "",
        cidade=registro.cidade or "",
        uf=registro.uf or "",
        latitude=registro.latitude,
        longitude=registro.longitude,
        aviso_local=aviso or aviso_local(registro),
        texto=registro.texto or "",
    )


def _uf_do_pedido(pedido: PedidoMarca) -> str:
    try:
        return uf_valida(pedido.uf)
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro


@router.get("/marcas", response_model=RespostaMarcas)
async def get_marcas(session: AsyncSession = Depends(get_session)) -> RespostaMarcas:
    return RespostaMarcas(itens=[_marca(item) for item in await listar_marcas(session)])


@router.post("/marcas", response_model=ItemMarca)
async def post_marca(pedido: PedidoMarca, session: AsyncSession = Depends(get_session)) -> ItemMarca:
    uf = _uf_do_pedido(pedido)
    registro = await criar_marca(session, pedido.nome, pedido.publico, pedido.texto, pedido.site, pedido.marca_dagua)
    aviso = await ajustar_local(session, registro, pedido.cidade, uf)
    return _marca(registro, aviso)


@router.put("/marcas/{marca_id}", response_model=ItemMarca)
async def put_marca(
    marca_id: int,
    pedido: PedidoMarca,
    session: AsyncSession = Depends(get_session),
) -> ItemMarca:
    uf = _uf_do_pedido(pedido)
    registro = await atualizar_marca(
        session, marca_id, pedido.nome, pedido.publico, pedido.texto, pedido.site, pedido.marca_dagua
    )
    if registro is None:
        raise HTTPException(status_code=404, detail="Essa marca não existe mais.")
    aviso = await ajustar_local(session, registro, pedido.cidade, uf)
    return _marca(registro, aviso)


@router.delete("/marcas/{marca_id}")
async def delete_marca(marca_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    try:
        await apagar_marca(session, marca_id)
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    return {"ok": True}


@router.delete("/agenda/{item_id}")
async def delete_agenda(item_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    resultado = await session.exec(select(Agendamento).where(Agendamento.id == item_id))
    registro = resultado.first()
    if registro is None:
        raise HTTPException(status_code=404, detail="Esse post não está na fila.")
    if registro.status == "publicado":
        raise HTTPException(status_code=422, detail="Esse post já saiu.")
    if registro.status == "publicando":
        raise HTTPException(status_code=422, detail="Esse post está saindo agora.")
    await session.delete(registro)
    await session.commit()
    return {"ok": True}


@router.put("/agenda/{item_id}/formato", response_model=ItemAgenda)
async def put_formato_agenda(
    item_id: int,
    pedido: PedidoFormato,
    session: AsyncSession = Depends(get_session),
) -> ItemAgenda:
    if pedido.formato not in {"feed", "story", "ambos"}:
        raise HTTPException(status_code=422, detail="Escolha Feed, Story ou Feed e Story.")
    resultado = await session.exec(select(Agendamento).where(Agendamento.id == item_id))
    registro = resultado.first()
    if registro is None:
        raise HTTPException(status_code=404, detail="Esse post não está na fila.")
    if registro.status == "publicado":
        raise HTTPException(status_code=422, detail="Esse post já saiu.")
    if registro.status == "publicando":
        raise HTTPException(status_code=422, detail="Esse post está saindo agora.")
    registro.formato = pedido.formato
    precisa_foto = pedido.formato in {"story", "ambos"} or registro.rede in {"Instagram", "Ambos"}
    tem_foto = bool(registro.imagem_url or registro.video_url or (registro.cena or "").strip())
    if registro.status in {"na_fila", "falta_imagem"}:
        if precisa_foto and not tem_foto:
            registro.status = "falta_imagem"
            registro.detalhe = "Falta a foto."
        elif registro.status == "falta_imagem":
            registro.status = "na_fila"
            registro.detalhe = ""
    await session.commit()
    await session.refresh(registro)
    return _item(registro)


@router.post("/agenda/{item_id}/trocar-foto", response_model=ItemAgenda)
async def post_trocar_foto(item_id: int, session: AsyncSession = Depends(get_session)) -> ItemAgenda:
    registro = await session.get(Agendamento, item_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="Esse post não está na fila.")
    falha = await trocar_foto(session, registro)
    if falha:
        raise HTTPException(status_code=422, detail=falha)
    await session.refresh(registro)
    return _item(registro)


@router.post("/semana", response_model=RespostaSemana)
async def post_semana(
    pedido: PedidoSemana,
    session: AsyncSession = Depends(get_session),
) -> RespostaSemana:
    pares: list[tuple[str, str]] = []
    try:
        for dia in pedido.dias:
            pares.append((dia.dia, dia.hora_normalizada()))
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    try:
        criados, aviso = await montar_dias(
            session,
            pares,
            pedido.rede if pedido.rede in {"Instagram", "Facebook", "Ambos"} else "Ambos",
            pedido.formato if pedido.formato in {"feed", "story", "ambos"} else "feed",
            pedido.tema.strip()[:30],
            pedido.marca_id,
            pedido.contexto,
        )
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    if pedido.sugestao_id and criados:
        await mudar_status(session, pedido.sugestao_id, criados[0].marca_id, "usada")
    return RespostaSemana(
        itens=[
            PosteSemana(dia=item.dia, hora=item.hora, titulo=item.titulo, legenda=item.legenda)
            for item in criados
        ],
        aviso=aviso,
    )


async def _marca_existente(session: AsyncSession, marca_id: int) -> int:
    marcas = await listar_marcas(session)
    if marca_id <= 0 and marcas:
        return marcas[0].id or 0
    if any(marca.id == marca_id for marca in marcas):
        return marca_id
    raise HTTPException(status_code=404, detail="Essa marca não existe mais.")


@router.get("/contas/estado")
async def get_estado_contas(marca_id: int = 0, session: AsyncSession = Depends(get_session)) -> dict:
    marca = await _marca_existente(session, marca_id)
    contas = await estado_contas(session, marca)
    return {
        "marca_id": marca,
        "instagram": _estado(contas["instagram"]).model_dump(),
        "facebook": _estado(contas["facebook"]).model_dump(),
    }


@router.get("/contas/iniciar", response_model=None)
async def iniciar_contas(
    rede: str = "instagram",
    marca_id: int = 0,
    session: AsyncSession = Depends(get_session),
):
    marca = await _marca_existente(session, marca_id)
    if rede == "facebook":
        if not facebook_pronta():
            return HTMLResponse(_pagina_falta())
        return RedirectResponse(url_oauth_facebook(marca))
    if not meta_pronta():
        return HTMLResponse(_pagina_falta())
    return RedirectResponse(url_oauth(marca))


@router.get("/contas/retorno")
async def retorno_contas(
    code: str = "",
    state: str = "",
    error: str = "",
    session: AsyncSession = Depends(get_session),
):
    if error or not code:
        return RedirectResponse("/?contas=cancelado")
    try:
        if state.startswith("facebook."):
            marca = marca_do_state_facebook(state)
            if marca is None:
                return RedirectResponse("/?contas=cancelado")
            await guardar_codigo_facebook(session, code, marca)
            return RedirectResponse(f"/?contas=escolher&marca={marca}")
        marca = marca_do_state(state)
        if marca is None:
            return RedirectResponse("/?contas=cancelado")
        await guardar_codigo(session, code, marca)
    except Exception:
        return RedirectResponse("/?contas=erro")
    return RedirectResponse(f"/?contas=ligado&marca={marca}")


@router.get("/contas/facebook/paginas", response_model=RespostaPaginas)
async def get_paginas_facebook(marca_id: int = 0, session: AsyncSession = Depends(get_session)) -> RespostaPaginas:
    paginas, escolhida = await paginas_facebook(session, await marca_efetiva(session, marca_id))
    return RespostaPaginas(paginas=paginas, escolhida=escolhida)


@router.post("/contas/facebook/escolher")
async def post_pagina_facebook(
    pedido: PedidoPagina,
    session: AsyncSession = Depends(get_session),
):
    marca = await _marca_existente(session, pedido.marca_id)
    try:
        nome = await escolher_pagina_facebook(session, pedido.pagina_id.strip(), marca)
    except RuntimeError as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    return {"nome": nome}
