from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Agendamento
from app.schemas import EstadoRede, ItemAgenda, PedidoAgenda, RespostaAgenda
from app.services.publicar import (
    estado_contas,
    guardar_codigo,
    meta_pronta,
    publicar_vencidos,
    state_valido,
    url_oauth,
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
    <h1>A agenda já guarda as 9h</h1>
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
    resultado = await session.exec(select(Agendamento).where(Agendamento.dia == pedido.dia))
    registro = resultado.first()
    if registro is None:
        registro = Agendamento(
            dia=pedido.dia,
            rede=pedido.rede.strip(),
            titulo=pedido.titulo.strip(),
            legenda=pedido.legenda.strip(),
        )
        session.add(registro)
    else:
        registro.rede = pedido.rede.strip()
        registro.titulo = pedido.titulo.strip()
        registro.legenda = pedido.legenda.strip()
        registro.hora = "09:00"
        registro.status = "na_fila"
        registro.detalhe = ""
        registro.imagem_url = ""
    contas = await estado_contas(session)
    if registro.rede == "Facebook" and _estado(contas["facebook"]).ligado is False:
        registro.detalhe = "Falta ligar a conta."
    elif registro.rede == "Instagram":
        registro.status = "falta_imagem"
        registro.detalhe = "Falta ligar a conta e a imagem do post." if not _estado(contas["instagram"]).ligado else "Falta a imagem do post."
    elif registro.rede not in ("Instagram", "Facebook"):
        registro.status = "manual"
        registro.detalhe = "Essa rede ainda não publica sozinha."
    await session.commit()
    await session.refresh(registro)
    return _item(registro)


@router.get("/contas/iniciar", response_model=None)
async def iniciar_contas():
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
    if error or not code or not state_valido(state):
        return RedirectResponse("/?contas=cancelado")
    try:
        await guardar_codigo(session, code)
    except Exception:
        return RedirectResponse("/?contas=erro")
    return RedirectResponse("/?contas=ligado")
