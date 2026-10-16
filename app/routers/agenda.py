from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Agendamento
from app.schemas import (
    EstadoRede,
    ItemAgenda,
    PedidoAgenda,
    PedidoPagina,
    PedidoPublicarAgora,
    RespostaAgenda,
    RespostaPaginas,
    RespostaPublicacao,
)
from app.services.midia import salvar_anexo
from app.services.publicar import (
    escolher_pagina_facebook,
    estado_contas,
    facebook_pronta,
    guardar_codigo,
    guardar_codigo_facebook,
    meta_pronta,
    paginas_facebook,
    publicar_agora,
    publicar_vencidos,
    state_facebook_valido,
    state_valido,
    url_oauth,
    url_oauth_facebook,
)

router = APIRouter()

PAGINA_FALTA = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <title>Contas</title>
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
    <p><a href="/">Voltar</a></p>
  </main>
</body>
</html>
"""


def _item(registro: Agendamento) -> ItemAgenda:
    return ItemAgenda(
        id=registro.id or 0,
        dia=registro.dia,
        hora=registro.hora,
        rede=registro.rede,
        titulo=registro.titulo,
        status=registro.status,
        detalhe=registro.detalhe,
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
    try:
        url, tipo = salvar_anexo(dados)
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    return {"url": url, "tipo": tipo}


@router.get("/agenda", response_model=RespostaAgenda)
async def get_agenda(session: AsyncSession = Depends(get_session)) -> RespostaAgenda:
    await publicar_vencidos(session)
    resultado = await session.exec(select(Agendamento).order_by(Agendamento.dia))
    contas = await estado_contas(session)
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
    resultado = await session.exec(select(Agendamento).where(Agendamento.dia == pedido.dia))
    registro = resultado.first()
    if registro is None:
        registro = Agendamento(
            dia=pedido.dia,
            hora=hora,
            rede=pedido.rede.strip(),
            titulo=pedido.titulo.strip(),
            legenda=pedido.legenda.strip(),
            imagem_url=pedido.imagem_url.strip()[:500],
            video_url=pedido.video_url.strip()[:500],
        )
        session.add(registro)
    else:
        registro.rede = pedido.rede.strip()
        registro.titulo = pedido.titulo.strip()
        registro.legenda = pedido.legenda.strip()
        registro.hora = hora
        registro.status = "na_fila"
        registro.detalhe = ""
        registro.imagem_url = pedido.imagem_url.strip()[:500]
        registro.video_url = pedido.video_url.strip()[:500]
    contas = await estado_contas(session)
    if registro.rede == "Facebook" and _estado(contas["facebook"]).ligado is False:
        registro.detalhe = "Falta ligar a conta."
    elif registro.rede == "Instagram" and not registro.imagem_url and not registro.video_url:
        registro.status = "falta_imagem"
        registro.detalhe = "Falta ligar a conta e a foto ou o vídeo do post." if not _estado(contas["instagram"]).ligado else "Falta a foto ou o vídeo do post."
    elif registro.rede == "Instagram" and _estado(contas["instagram"]).ligado is False:
        registro.detalhe = "Falta ligar a conta."
    elif registro.rede not in ("Instagram", "Facebook"):
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
    )
    return RespostaPublicacao(status=status, detalhe=detalhe)


@router.get("/contas/iniciar", response_model=None)
async def iniciar_contas(rede: str = "instagram"):
    if rede == "facebook":
        if not facebook_pronta():
            return HTMLResponse(PAGINA_FALTA)
        return RedirectResponse(url_oauth_facebook())
    if not meta_pronta():
        return HTMLResponse(PAGINA_FALTA)
    return RedirectResponse(url_oauth())


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
            if not state_facebook_valido(state):
                return RedirectResponse("/?contas=cancelado")
            await guardar_codigo_facebook(session, code)
            return RedirectResponse("/?contas=escolher")
        if state_valido(state):
            await guardar_codigo(session, code)
        else:
            return RedirectResponse("/?contas=cancelado")
    except Exception:
        return RedirectResponse("/?contas=erro")
    return RedirectResponse("/?contas=ligado")


@router.get("/contas/facebook/paginas", response_model=RespostaPaginas)
async def get_paginas_facebook(session: AsyncSession = Depends(get_session)) -> RespostaPaginas:
    paginas, escolhida = await paginas_facebook(session)
    return RespostaPaginas(paginas=paginas, escolhida=escolhida)


@router.post("/contas/facebook/escolher")
async def post_pagina_facebook(
    pedido: PedidoPagina,
    session: AsyncSession = Depends(get_session),
):
    try:
        nome = await escolher_pagina_facebook(session, pedido.pagina_id.strip())
    except RuntimeError as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    return {"nome": nome}
