from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Ficha, LogoMarca, MemoriaFoto, agora
from app.schemas import PedidoArte, PedidoArteFoto, RespostaFoto
from app.services import diretor, molde
from app.services.memoria import montar_contexto

router = APIRouter()


async def _marca(session: AsyncSession, marca_id: int) -> Ficha:
    marca = await session.get(Ficha, marca_id) if marca_id > 0 else None
    if marca is None:
        raise HTTPException(status_code=404, detail="Essa marca não existe mais.")
    return marca


def _falha(erro: Exception, padrao: str) -> HTTPException:
    texto = str(erro)
    if "insufficient_quota" in texto or "credit_balance" in texto:
        return HTTPException(status_code=502, detail="A conta da OpenAI está sem créditos. Adicione créditos e tente de novo.")
    if isinstance(erro, RuntimeError) and texto:
        return HTTPException(status_code=422, detail=texto[:200])
    return HTTPException(status_code=502, detail=padrao)


def _resposta(plano: dict) -> RespostaFoto:
    return RespostaFoto(
        foto_url=plano["feed_url"],
        story_url=plano.get("story_url") or "",
        plano_arte=diretor.resumo_plano(plano),
        aviso=plano.get("aviso") or "",
    )


@router.post("/arte/refazer", response_model=RespostaFoto)
async def post_refazer(pedido: PedidoArte, session: AsyncSession = Depends(get_session)) -> RespostaFoto:
    """Monta a arte de novo com o texto ou o modelo editados, usando a mesma foto base (sem custo de IA)."""
    marca = await _marca(session, pedido.marca_id)
    plano = diretor.do_cliente(pedido.plano_arte, await montar_contexto(session, marca))
    if plano is None:
        raise HTTPException(status_code=422, detail="Gere o post antes de refazer a arte.")
    try:
        return _resposta(await diretor.refazer(session, marca, plano, pedido.formato))
    except Exception as erro:
        raise _falha(erro, "Não consegui refazer a arte agora.") from erro


@router.post("/arte/foto", response_model=RespostaFoto)
async def post_arte_foto(pedido: PedidoArteFoto, session: AsyncSession = Depends(get_session)) -> RespostaFoto:
    """Troca a foto base da arte por outra da memória. 'Pode postar' entra como está; 'base' é editada pela IA."""
    marca = await _marca(session, pedido.marca_id)
    foto = await session.get(MemoriaFoto, pedido.foto_id)
    if foto is None or foto.marca_id != marca.id:
        raise HTTPException(status_code=404, detail="Essa foto não está na memória desta marca.")
    if foto.papel == "estilo":
        raise HTTPException(status_code=422, detail="Essa foto é só inspiração de estilo. Mude o papel dela em Minha marca para usar no post.")
    contexto = await montar_contexto(session, marca)
    dados = dict(pedido.plano_arte or diretor.plano_padrao("", pedido.gancho))
    dados.update(foto_base_id=foto.id, base_url="", feed_url="", story_url="", editada=False)
    plano = diretor.do_cliente(dados, contexto)
    if plano is None or plano.get("foto_base_id") != foto.id:
        raise HTTPException(status_code=422, detail="Essa foto não está liberada para os posts.")
    try:
        return _resposta(await diretor.produzir(session, marca, plano, reusar_base=False))
    except Exception as erro:
        raise _falha(erro, "Não consegui montar a arte com essa foto.") from erro


@router.api_route("/marcas/{marca_id}/logo.png", methods=["GET", "HEAD"], include_in_schema=False)
async def get_logo(marca_id: int, request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    logo = await session.get(LogoMarca, marca_id)
    if logo is None:
        raise HTTPException(status_code=404, detail="Esta marca não tem logo.")
    cabecalho = {"Cache-Control": "public, max-age=300"}
    if request.method == "HEAD":
        return Response(media_type="image/png", headers={**cabecalho, "Content-Length": str(len(logo.arquivo))})
    return Response(content=logo.arquivo, media_type="image/png", headers=cabecalho)


@router.post("/marcas/{marca_id}/logo")
async def post_logo(
    marca_id: int, arquivo: UploadFile = File(), session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    await _marca(session, marca_id)
    dados = await arquivo.read(4_000_001)
    if len(dados) > 4_000_000:
        raise HTTPException(status_code=422, detail="O logo precisa ter até 4 MB.")
    try:
        png = molde.normalizar_logo(dados)
    except ValueError as erro:
        raise HTTPException(status_code=422, detail=str(erro)) from erro
    logo = await session.get(LogoMarca, marca_id)
    if logo is None:
        session.add(LogoMarca(marca_id=marca_id, arquivo=png))
    else:
        logo.arquivo = png
        logo.atualizado_em = agora()
    await session.commit()
    return {"logo_url": f"/marcas/{marca_id}/logo.png?v={int(agora().timestamp())}"}


@router.delete("/marcas/{marca_id}/logo")
async def delete_logo(marca_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, bool]:
    logo = await session.get(LogoMarca, marca_id)
    if logo is not None:
        await session.delete(logo)
        await session.commit()
    return {"ok": True}
