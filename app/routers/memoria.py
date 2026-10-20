from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Ficha, MemoriaFoto
from app.schemas import (
    ItemFotoMemoria,
    PedidoFotoMemoria,
    PedidoImagemMemoria,
    RespostaFoto,
    RespostaFotosMemoria,
)
from app.services.imagens import ler, ler_memoria, url_memoria
from app.services.memoria import (
    LIMITE_FOTOS,
    PAPEIS,
    TIPOS,
    TIPOS_ANTIGOS,
    adicionar_foto,
    apagar_foto,
    imagem_da_memoria,
    listar_fotos,
    normalizar_papel,
    normalizar_tipo,
    reanalisar,
)

router = APIRouter()

CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}


def _jpeg(request: Request, dados: bytes | None) -> Response:
    if not dados:
        raise HTTPException(status_code=404, detail="Essa foto não existe mais.")
    if request.method == "HEAD":
        return Response(media_type="image/jpeg", headers={**CACHE, "Content-Length": str(len(dados))})
    return Response(content=dados, media_type="image/jpeg", headers=CACHE)


@router.api_route("/img/{ident}.jpg", methods=["GET", "HEAD"], include_in_schema=False)
async def get_imagem(ident: int, request: Request) -> Response:
    return _jpeg(request, await ler(ident))


@router.api_route("/memoria/foto/{ident}.jpg", methods=["GET", "HEAD"], include_in_schema=False)
async def get_foto_memoria(ident: int, request: Request, mini: int = 0) -> Response:
    return _jpeg(request, await ler_memoria(ident, mini=bool(mini)))


def _item(foto) -> ItemFotoMemoria:
    return ItemFotoMemoria(
        id=foto.id,
        url=url_memoria(foto.id),
        miniatura_url=url_memoria(foto.id, mini=True),
        largura=foto.largura or 0,
        altura=foto.altura or 0,
        descricao=foto.descricao or "",
        tags=foto.tags or "",
        tipo=normalizar_tipo(foto.tipo),
        papel=normalizar_papel(foto.papel),
        usar_em_posts=bool(foto.usar_em_posts),
    )


async def _marca(session: AsyncSession, marca_id: int) -> Ficha:
    marca = await session.get(Ficha, marca_id) if marca_id > 0 else None
    if marca is None:
        raise HTTPException(status_code=404, detail="Salve a marca antes de mandar as fotos.")
    return marca


async def _foto(session: AsyncSession, foto_id: int) -> MemoriaFoto:
    foto = await session.get(MemoriaFoto, foto_id)
    if foto is None:
        raise HTTPException(status_code=404, detail="Essa foto não está mais na memória.")
    return foto


async def _resposta(session: AsyncSession, marca_id: int, avisos: list[str] | None = None) -> RespostaFotosMemoria:
    return RespostaFotosMemoria(
        itens=[_item(foto) for foto in await listar_fotos(session, marca_id)],
        limite=LIMITE_FOTOS,
        tipos=list(TIPOS),
        avisos=avisos or [],
    )


@router.get("/memoria/{marca_id}/fotos", response_model=RespostaFotosMemoria)
async def get_fotos(marca_id: int, session: AsyncSession = Depends(get_session)) -> RespostaFotosMemoria:
    await _marca(session, marca_id)
    return await _resposta(session, marca_id)


@router.post("/memoria/{marca_id}/fotos", response_model=RespostaFotosMemoria)
async def post_fotos(
    marca_id: int,
    arquivos: list[UploadFile] = File(),
    session: AsyncSession = Depends(get_session),
) -> RespostaFotosMemoria:
    marca = await _marca(session, marca_id)
    avisos: list[str] = []
    guardadas = 0
    for arquivo in arquivos[:20]:
        nome = (arquivo.filename or "foto").strip()[:60]
        dados = await arquivo.read(8_000_001)
        try:
            _foto_nova, aviso = await adicionar_foto(session, marca, dados)
            guardadas += 1
            if aviso:
                avisos.append(f"{nome}: {aviso}")
        except ValueError as erro:
            avisos.append(f"{nome}: {erro}")
    if not guardadas and avisos:
        raise HTTPException(status_code=422, detail=" ".join(avisos)[:400])
    return await _resposta(session, marca_id, avisos)


@router.put("/memoria/fotos/{foto_id}", response_model=ItemFotoMemoria)
async def put_foto(
    foto_id: int, pedido: PedidoFotoMemoria, session: AsyncSession = Depends(get_session)
) -> ItemFotoMemoria:
    foto = await _foto(session, foto_id)
    tipo = pedido.tipo.strip().lower()
    if tipo not in TIPOS and tipo not in TIPOS_ANTIGOS:
        raise HTTPException(status_code=422, detail="Escolha um tipo da lista.")
    if pedido.papel not in PAPEIS:
        raise HTTPException(status_code=422, detail="Escolha como a IA usa a foto.")
    foto.descricao = pedido.descricao.strip()
    foto.tags = pedido.tags.strip()
    foto.tipo = normalizar_tipo(tipo)
    foto.papel = pedido.papel
    foto.usar_em_posts = pedido.usar_em_posts
    await session.commit()
    await session.refresh(foto)
    return _item(foto)


@router.post("/memoria/fotos/{foto_id}/analisar", response_model=ItemFotoMemoria)
async def post_analisar(foto_id: int, session: AsyncSession = Depends(get_session)) -> ItemFotoMemoria:
    foto = await _foto(session, foto_id)
    falha = await reanalisar(session, foto, await session.get(Ficha, foto.marca_id))
    if falha:
        raise HTTPException(status_code=502, detail=falha)
    return _item(foto)


@router.delete("/memoria/fotos/{foto_id}")
async def delete_foto(foto_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    await apagar_foto(session, await _foto(session, foto_id))
    return {"ok": True}


@router.post("/memoria/imagem", response_model=RespostaFoto)
async def post_imagem_memoria(
    pedido: PedidoImagemMemoria, session: AsyncSession = Depends(get_session)
) -> RespostaFoto:
    """Foto do post (prévia) feita de uma foto da memória: sem IA, sem custo."""
    marca = await _marca(session, pedido.marca_id)
    foto = await _foto(session, pedido.foto_id)
    if foto.marca_id != marca.id:
        raise HTTPException(status_code=404, detail="Essa foto não está na memória desta marca.")
    try:
        url = await imagem_da_memoria(session, pedido.foto_id, pedido.gancho, marca)
    except Exception as erro:
        raise HTTPException(status_code=422, detail=str(erro)[:200] or "Não consegui usar essa foto.") from erro
    return RespostaFoto(foto_url=url)
