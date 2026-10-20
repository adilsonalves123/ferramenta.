from html import escape

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.models import Agendamento, Ficha, LogoMarca, MemoriaFoto
from app.schemas import (
    EstadoRede,
    FatosMarca,
    ItemAgenda,
    PedidoAgenda,
    PedidoArteAgenda,
    ItemMarca,
    PedidoFormato,
    PedidoFotoAgenda,
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
from app.services import diretor, molde
from app.services.calendario import ajustar_local, alcance, aviso_local, mudar_status, uf_valida
from app.services.imagens import url_memoria
from app.services.memoria import imagem_da_memoria, ler_fatos, limpar_gancho, montar_contexto
from app.services.midia import anexar
from app.services.publicar import (
    LIMITE_FOTOS,
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
    tentar_de_novo,
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
    plano = diretor.ler_plano(registro.plano_arte)
    tem_cena = bool((registro.cena or "").strip()) or plano is not None
    aberto = registro.status in {"na_fila", "falta_imagem"}
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
        aguarda_foto=tem_cena and not registro.imagem_url and not registro.video_url,
        imagem_url=registro.imagem_url or "",
        pode_trocar_foto=aberto and not registro.video_url,
        pode_criar_ia=aberto and tem_cena and not registro.video_url,
        foto_id=registro.foto_id,
        gancho=registro.gancho or "",
        memoria_miniatura=url_memoria(registro.foto_id, mini=True) if registro.foto_id else "",
        arte=diretor.resumo_plano(plano),
    )


def _estado(conta) -> EstadoRede:
    if conta is None or not conta.token:
        return EstadoRede(ligado=False)
    return EstadoRede(ligado=True, nome=conta.nome)


@router.post("/anexo")
async def post_anexo(arquivo: UploadFile = File(), marca_id: int = Form(0)) -> dict[str, str]:
    dados = await arquivo.read(25_000_001)
    if len(dados) > 25_000_000:
        raise HTTPException(status_code=422, detail="O vídeo precisa ter até 25 MB.")
    if (arquivo.content_type or "").lower() == "video/webm" or (arquivo.filename or "").lower().endswith(".webm"):
        raise HTTPException(status_code=422, detail="Envie o vídeo em MP4.")
    try:
        url, tipo = await anexar(dados, marca_id)
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    return {"url": url, "tipo": tipo}


async def _foto_da_marca(session: AsyncSession, foto_id: int | None, marca_id: int) -> int | None:
    if not foto_id:
        return None
    foto = await session.get(MemoriaFoto, foto_id)
    return foto_id if foto is not None and foto.marca_id == marca_id else None


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
        gancho=limpar_gancho(pedido.gancho),
    )
    marca_id = await marca_efetiva(session, pedido.marca_id)
    plano = await _plano_do_pedido(session, pedido.plano_arte, marca_id)
    usa_arte = (
        plano is not None
        and not registro.video_url
        and (not registro.imagem_url or registro.imagem_url == plano.get("feed_url"))
    )
    if usa_arte:
        registro.plano_arte = diretor.gravar_plano(plano)
        registro.foto_id = plano.get("foto_base_id")
    else:
        registro.foto_id = await _foto_da_marca(session, pedido.foto_id, marca_id)
    session.add(registro)
    formato = registro.formato or "feed"
    precisa_foto = formato in {"story", "ambos"} or registro.rede in {"Instagram", "Ambos"}
    contas = await estado_contas(session, marca_id)
    faltam = []
    if registro.rede in ("Instagram", "Ambos") and not _estado(contas["instagram"]).ligado:
        faltam.append(falta_conta("Instagram"))
    if registro.rede in ("Facebook", "Ambos") and not _estado(contas["facebook"]).ligado:
        faltam.append(falta_conta("Facebook"))
    if precisa_foto and not registro.imagem_url and not registro.video_url and not usa_arte:
        registro.status = "falta_imagem"
        registro.detalhe = "Falta a foto."
    elif faltam:
        registro.detalhe = " ".join(faltam)
    elif registro.rede not in ("Instagram", "Facebook", "Ambos"):
        registro.status = "manual"
        registro.detalhe = "Essa rede ainda não publica sozinha."
    elif usa_arte and not registro.imagem_url:
        registro.detalhe = "A IA prepara a foto e monta a arte até 3 horas antes do horário."
    await session.commit()
    await session.refresh(registro)
    return _item(registro)


async def _plano_do_pedido(session: AsyncSession, dados: dict | None, marca_id: int) -> dict | None:
    if not dados:
        return None
    marca = await session.get(Ficha, marca_id) if marca_id > 0 else None
    return diretor.do_cliente(dados, await montar_contexto(session, marca))


@router.post("/publicar-agora", response_model=RespostaPublicacao)
async def post_publicar_agora(
    pedido: PedidoPublicarAgora,
    session: AsyncSession = Depends(get_session),
) -> RespostaPublicacao:
    marca_id = await marca_efetiva(session, pedido.marca_id)
    plano = await _plano_do_pedido(session, pedido.plano_arte, marca_id)
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
        limpar_gancho(pedido.gancho),
        await _foto_da_marca(session, pedido.foto_id, marca_id),
        plano,
    )
    return RespostaPublicacao(status=status, detalhe=detalhe)


def _marca(registro, aviso: str = "", logos: dict[int, str] | None = None) -> ItemMarca:
    versao = (logos or {}).get(registro.id or 0, "")
    return ItemMarca(
        cor_principal=molde.hex_valido(registro.cor_principal, molde.COR_PRINCIPAL),
        cor_destaque=molde.hex_valido(registro.cor_destaque, molde.COR_DESTAQUE),
        logo_url=f"/marcas/{registro.id}/logo.png?v={versao}" if versao else "",
        id=registro.id or 0,
        nome=registro.nome or "",
        publico=registro.publico or "",
        site=registro.site or "",
        marca_dagua=registro.marca_dagua or "",
        abrangencia=alcance(registro),
        cidade=registro.cidade or "",
        uf=registro.uf or "",
        latitude=registro.latitude,
        longitude=registro.longitude,
        aviso_local=aviso or aviso_local(registro),
        texto=registro.texto or "",
        memoria=FatosMarca(**ler_fatos(registro)),
    )


def _uf_do_pedido(pedido: PedidoMarca) -> str:
    if pedido.abrangencia == "brasil":
        return ""
    try:
        uf = uf_valida(pedido.uf)
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    if pedido.abrangencia == "estado" and not uf:
        raise HTTPException(status_code=422, detail="Escreva a sigla do estado, por exemplo SP.")
    return uf


async def _logos(session: AsyncSession) -> dict[int, str]:
    resultado = await session.exec(select(LogoMarca.marca_id, LogoMarca.atualizado_em))
    return {marca_id: str(int(quando.timestamp())) for marca_id, quando in resultado.all()}


def _cores(pedido: PedidoMarca) -> tuple[str, str] | None:
    if not pedido.cor_principal and not pedido.cor_destaque:
        return None
    return (
        molde.hex_valido(pedido.cor_principal, molde.COR_PRINCIPAL),
        molde.hex_valido(pedido.cor_destaque, molde.COR_DESTAQUE),
    )


@router.get("/marcas", response_model=RespostaMarcas)
async def get_marcas(session: AsyncSession = Depends(get_session)) -> RespostaMarcas:
    marcas = await listar_marcas(session)
    logos = await _logos(session)
    return RespostaMarcas(itens=[_marca(item, logos=logos) for item in marcas])


@router.post("/marcas", response_model=ItemMarca)
async def post_marca(pedido: PedidoMarca, session: AsyncSession = Depends(get_session)) -> ItemMarca:
    uf = _uf_do_pedido(pedido)
    registro = await criar_marca(
        session,
        pedido.nome,
        pedido.publico,
        pedido.texto,
        pedido.site,
        pedido.marca_dagua,
        pedido.memoria.model_dump() if pedido.memoria else None,
        _cores(pedido),
    )
    aviso = await ajustar_local(session, registro, pedido.cidade, uf, pedido.abrangencia)
    return _marca(registro, aviso, await _logos(session))


@router.put("/marcas/{marca_id}", response_model=ItemMarca)
async def put_marca(
    marca_id: int,
    pedido: PedidoMarca,
    session: AsyncSession = Depends(get_session),
) -> ItemMarca:
    uf = _uf_do_pedido(pedido)
    registro = await atualizar_marca(
        session,
        marca_id,
        pedido.nome,
        pedido.publico,
        pedido.texto,
        pedido.site,
        pedido.marca_dagua,
        pedido.memoria.model_dump() if pedido.memoria else None,
        _cores(pedido),
    )
    if registro is None:
        raise HTTPException(status_code=404, detail="Essa marca não existe mais.")
    aviso = await ajustar_local(session, registro, pedido.cidade, uf, pedido.abrangencia)
    return _marca(registro, aviso, await _logos(session))


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


def _aberto(registro: Agendamento | None) -> Agendamento:
    if registro is None:
        raise HTTPException(status_code=404, detail="Esse post não está na fila.")
    if registro.status not in {"na_fila", "falta_imagem"}:
        raise HTTPException(status_code=422, detail="Esse post não está mais na fila.")
    if registro.video_url:
        raise HTTPException(status_code=422, detail="Este post sai com vídeo.")
    return registro


def _arte_pronta(registro: Agendamento, feito: dict) -> None:
    registro.imagem_url = feito["feed_url"][:500]
    registro.foto_id = feito.get("foto_base_id")
    registro.plano_arte = diretor.gravar_plano(feito)
    registro.detalhe = ""
    if registro.status == "falta_imagem":
        registro.status = "na_fila"


async def _arte_com_foto(session: AsyncSession, registro: Agendamento, marca: Ficha | None, plano: dict, foto_id: int) -> None:
    """Refaz a arte do post com outra foto da memória: 'pode postar' sai como está, 'base' a IA edita."""
    foto = await session.get(MemoriaFoto, foto_id)
    if foto is not None and foto.papel == "estilo":
        raise HTTPException(status_code=422, detail="Essa foto é só inspiração de estilo. Mude o papel dela em Minha marca.")
    dados = {**plano, "foto_base_id": foto_id, "base_url": "", "feed_url": "", "story_url": "", "editada": False}
    novo = diretor.do_cliente(dados, await montar_contexto(session, marca))
    if novo is None or novo.get("foto_base_id") != foto_id:
        raise HTTPException(status_code=422, detail="Essa foto não está liberada para os posts.")
    marca_id = marca.id if marca and marca.id else 0
    if await diretor.vai_usar_ia(session, marca_id, novo):
        if (registro.tentativas or 0) >= LIMITE_FOTOS:
            raise HTTPException(
                status_code=422,
                detail=f"A IA já editou a foto deste post {LIMITE_FOTOS} vezes. Escolha uma foto 'pode postar' ou preencha o dia de novo.",
            )
        registro.tentativas = (registro.tentativas or 0) + 1
        await session.commit()
    try:
        feito = await diretor.produzir(session, marca, novo, cena=registro.cena or "", reusar_base=False)
    except Exception as erro:
        raise HTTPException(status_code=422, detail="Não consegui montar a arte com essa foto agora.") from erro
    _arte_pronta(registro, feito)
    await session.commit()


@router.put("/agenda/{item_id}/arte", response_model=ItemAgenda)
async def put_arte_agenda(
    item_id: int, pedido: PedidoArteAgenda, session: AsyncSession = Depends(get_session)
) -> ItemAgenda:
    """Refazer arte de um post da fila: muda o texto ou o modelo, com a mesma foto (sem custo de IA)."""
    registro = _aberto(await session.get(Agendamento, item_id))
    plano = diretor.ler_plano(registro.plano_arte)
    if plano is None:
        raise HTTPException(status_code=422, detail="Este post não tem arte para refazer.")
    marca = await session.get(Ficha, await marca_efetiva(session, registro.marca_id))
    textos = pedido.model_dump(exclude={"modelo"})
    dados = {**plano, **textos, "modelo": pedido.modelo or plano.get("modelo")}
    novo = diretor.do_cliente(dados, await montar_contexto(session, marca))
    if novo is None:
        raise HTTPException(status_code=422, detail="Não consegui ler a arte deste post.")
    try:
        feito = await diretor.refazer(session, marca, novo)
    except RuntimeError as erro:
        raise HTTPException(status_code=422, detail=str(erro)[:200]) from erro
    except Exception as erro:
        raise HTTPException(status_code=502, detail="Não consegui refazer a arte agora.") from erro
    _arte_pronta(registro, feito)
    await session.commit()
    await session.refresh(registro)
    return _item(registro)


async def _trocar_por_ia(session: AsyncSession, registro: Agendamento) -> None:
    if diretor.ler_plano(registro.plano_arte) is not None:
        falha = await trocar_foto(session, registro)
        if falha:
            raise HTTPException(status_code=422, detail=falha)
        return
    antes = registro.foto_id
    registro.foto_id = None
    falha = await trocar_foto(session, registro)
    if falha:
        registro.foto_id = antes
        await session.commit()
        raise HTTPException(status_code=422, detail=falha)


@router.post("/agenda/{item_id}/trocar-foto", response_model=ItemAgenda)
async def post_trocar_foto(item_id: int, session: AsyncSession = Depends(get_session)) -> ItemAgenda:
    registro = await session.get(Agendamento, item_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="Esse post não está na fila.")
    await _trocar_por_ia(session, registro)
    await session.refresh(registro)
    return _item(registro)


@router.post("/agenda/{item_id}/foto-memoria", response_model=ItemAgenda)
async def post_foto_memoria_agenda(
    item_id: int, pedido: PedidoFotoAgenda, session: AsyncSession = Depends(get_session)
) -> ItemAgenda:
    """Troca a foto do post por outra foto da memória (sem custo) ou, com foto_id vazio, cria com IA."""
    registro = await session.get(Agendamento, item_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="Esse post não está na fila.")
    if registro.status not in {"na_fila", "falta_imagem"}:
        raise HTTPException(status_code=422, detail="Esse post não está mais na fila.")
    if registro.video_url:
        raise HTTPException(status_code=422, detail="Este post sai com vídeo.")
    if not pedido.foto_id:
        await _trocar_por_ia(session, registro)
        await session.refresh(registro)
        return _item(registro)
    marca = await session.get(Ficha, await marca_efetiva(session, registro.marca_id))
    if await _foto_da_marca(session, pedido.foto_id, marca.id if marca else 0) is None:
        raise HTTPException(status_code=404, detail="Essa foto não está na memória desta marca.")
    plano = diretor.ler_plano(registro.plano_arte)
    if plano is not None:
        await _arte_com_foto(session, registro, marca, plano, pedido.foto_id)
        await session.refresh(registro)
        return _item(registro)
    try:
        url = await imagem_da_memoria(session, pedido.foto_id, registro.gancho or "", marca)
    except Exception as erro:
        raise HTTPException(status_code=422, detail=str(erro)[:200] or "Não consegui usar essa foto.") from erro
    registro.foto_id = pedido.foto_id
    registro.imagem_url = url[:500]
    registro.detalhe = ""
    if registro.status == "falta_imagem":
        registro.status = "na_fila"
    await session.commit()
    await session.refresh(registro)
    return _item(registro)


@router.post("/agenda/{item_id}/tentar-de-novo", response_model=ItemAgenda)
async def post_tentar_de_novo(item_id: int, session: AsyncSession = Depends(get_session)) -> ItemAgenda:
    registro = await session.get(Agendamento, item_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="Esse post não está na fila.")
    falha = await tentar_de_novo(session, registro)
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
