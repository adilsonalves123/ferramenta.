import asyncio
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.models import ContaRede, Publicacao
from app.services.publicar import ZONA, juntar_ids, perfil_instagram, usuario_facebook

SIGLAS = {"instagram": "ig", "facebook": "fb"}
REDES_DA_SIGLA = {"ig": "instagram", "fb": "facebook"}


def _segredo(rede: str) -> str:
    ajustes = get_settings()
    segredo = ajustes.instagram_app_secret if rede == "instagram" else ajustes.meta_app_secret
    return segredo.strip()


def _b64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def ler_pedido_assinado(signed_request: str) -> tuple[list[str], str] | None:
    """Confere o signed_request da Meta e devolve as redes do app que assinou e o user_id."""
    assinatura, ponto, carga = signed_request.strip().partition(".")
    if not ponto or not assinatura or not carga:
        return None
    try:
        recebida = _b64(assinatura)
        dados = json.loads(_b64(carga))
    except ValueError:
        return None
    if not isinstance(dados, dict) or str(dados.get("algorithm") or "").upper() != "HMAC-SHA256":
        return None
    usuario = str(dados.get("user_id") or "").strip()
    if not usuario:
        return None
    redes = []
    for rede in ("instagram", "facebook"):
        segredo = _segredo(rede)
        if not segredo:
            continue
        esperada = hmac.new(segredo.encode(), carga.encode(), hashlib.sha256).digest()
        if hmac.compare_digest(esperada, recebida):
            redes.append(rede)
    if not redes:
        return None
    return redes, usuario


def _ids(texto: str) -> set[str]:
    return {parte for parte in (texto or "").split(",") if parte}


def _quem_e(rede: str, token: str) -> set[str]:
    if rede == "instagram":
        profissional, _nome, escopo = perfil_instagram(token)
        return _ids(juntar_ids(escopo, profissional))
    return _ids(usuario_facebook(token))


async def _e_do_usuario(contas: list[ContaRede], rede: str, usuario: str) -> bool:
    conhecidos: set[str] = set()
    for conta in contas:
        conhecidos |= _ids(conta.usuario_meta)
        if conta.rede == "instagram" and conta.externo_id:
            conhecidos.add(conta.externo_id)
    if usuario in conhecidos:
        return True
    if any(conta.usuario_meta for conta in contas):
        return False
    ordem = sorted(contas, key=lambda conta: conta.rede != "facebook_acesso")
    token = next((conta.token for conta in ordem if conta.token), "")
    if not token:
        return False
    try:
        ids = await asyncio.to_thread(_quem_e, rede, token)
    except RuntimeError:
        # A Meta recusou o token: a pessoa já tirou o acesso desta conexão.
        return True
    except Exception:
        return False
    for conta in contas:
        conta.usuario_meta = ",".join(sorted(ids))[:200]
    return usuario in ids


async def apagar_contas_meta(session: AsyncSession, redes: list[str], usuario: str) -> int:
    apagadas = 0
    for rede in redes:
        alvo = ["instagram"] if rede == "instagram" else ["facebook_acesso", "facebook"]
        resultado = await session.exec(select(ContaRede).where(ContaRede.rede.in_(alvo)))
        grupos: dict[int, list[ContaRede]] = {}
        for conta in resultado.all():
            grupos.setdefault(conta.marca_id, []).append(conta)
        for marca_id, contas in grupos.items():
            if not await _e_do_usuario(contas, rede, usuario):
                continue
            for conta in contas:
                await session.delete(conta)
                apagadas += 1
            publicacoes = await session.exec(
                select(Publicacao).where(
                    Publicacao.marca_id == marca_id,
                    Publicacao.rede == ("Instagram" if rede == "instagram" else "Facebook"),
                )
            )
            for publicacao in publicacoes.all():
                await session.delete(publicacao)
    await session.commit()
    return apagadas


def _assinar_codigo(corpo: str, rede: str) -> str:
    return hmac.new(_segredo(rede).encode(), corpo.encode(), hashlib.sha256).hexdigest()[:12]


def criar_codigo(redes: list[str], apagadas: int) -> str:
    siglas = "".join(SIGLAS[rede] for rede in redes)
    corpo = f"{siglas}-{int(time.time())}-{apagadas}"
    return f"{corpo}-{_assinar_codigo(corpo, redes[0])}"


def ler_codigo(codigo: str) -> tuple[datetime, int] | None:
    partes = codigo.strip().split("-")
    if len(partes) != 4:
        return None
    siglas, quando, apagadas, assinatura = partes
    primeira = REDES_DA_SIGLA.get(siglas[:2])
    if primeira is None or not _segredo(primeira):
        return None
    corpo = f"{siglas}-{quando}-{apagadas}"
    if not hmac.compare_digest(_assinar_codigo(corpo, primeira), assinatura):
        return None
    try:
        momento = datetime.fromtimestamp(int(quando), tz=timezone.utc).astimezone(ZONA)
        return momento, int(apagadas)
    except (ValueError, OverflowError, OSError):
        return None
