import asyncio
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.models import Agendamento, ContaRede, agora

VERSAO = "v22.0"
ZONA = timezone(timedelta(hours=-3))
REDES_PUBLICAVEIS = {"Instagram", "Facebook"}


def meta_pronta() -> bool:
    ajustes = get_settings()
    return bool(ajustes.meta_app_id.strip() and ajustes.meta_app_secret.strip())


def url_retorno() -> str:
    return get_settings().app_url.rstrip("/") + "/contas/retorno"


def _pedir(url: str, dados: dict | None = None) -> dict:
    corpo = urllib.parse.urlencode(dados).encode() if dados is not None else None
    pedido = urllib.request.Request(url, data=corpo)
    try:
        with urllib.request.urlopen(pedido, timeout=40) as resposta:
            return json.loads(resposta.read().decode())
    except urllib.error.HTTPError as erro:
        texto = erro.read().decode()
        try:
            mensagem = json.loads(texto).get("error", {}).get("message", texto)
        except json.JSONDecodeError:
            mensagem = texto
        raise RuntimeError(mensagem[:300]) from erro


def criar_state() -> str:
    expira = int(time.time()) + 600
    corpo = f"contas.{expira}"
    segredo = get_settings().meta_app_secret.encode()
    assinatura = hmac.new(segredo, corpo.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{corpo}.{assinatura}"


def state_valido(state: str) -> bool:
    partes = state.split(".")
    if len(partes) != 3:
        return False
    corpo = f"{partes[0]}.{partes[1]}"
    try:
        expira = int(partes[1])
    except ValueError:
        return False
    if expira < int(time.time()):
        return False
    segredo = get_settings().meta_app_secret.encode()
    assinatura = hmac.new(segredo, corpo.encode(), hashlib.sha256).hexdigest()[:24]
    return hmac.compare_digest(assinatura, partes[2])


def url_oauth() -> str:
    ajustes = get_settings()
    escopos = ",".join(
        [
            "pages_show_list",
            "pages_read_engagement",
            "pages_manage_posts",
            "instagram_basic",
            "instagram_content_publish",
            "business_management",
        ]
    )
    query = urllib.parse.urlencode(
        {
            "client_id": ajustes.meta_app_id,
            "redirect_uri": url_retorno(),
            "state": criar_state(),
            "scope": escopos,
            "response_type": "code",
        }
    )
    return f"https://www.facebook.com/{VERSAO}/dialog/oauth?{query}"


def trocar_codigo(codigo: str) -> str:
    ajustes = get_settings()
    query = urllib.parse.urlencode(
        {
            "client_id": ajustes.meta_app_id,
            "client_secret": ajustes.meta_app_secret,
            "redirect_uri": url_retorno(),
            "code": codigo,
        }
    )
    dados = _pedir(f"https://graph.facebook.com/{VERSAO}/oauth/access_token?{query}")
    curto = dados.get("access_token", "")
    if not curto:
        raise RuntimeError("A Meta não devolveu o acesso.")
    longo = urllib.parse.urlencode(
        {
            "grant_type": "fb_exchange_token",
            "client_id": ajustes.meta_app_id,
            "client_secret": ajustes.meta_app_secret,
            "fb_exchange_token": curto,
        }
    )
    estendido = _pedir(f"https://graph.facebook.com/{VERSAO}/oauth/access_token?{longo}")
    token = estendido.get("access_token", "")
    if not token:
        raise RuntimeError("A Meta não alongou o acesso.")
    return token


def listar_paginas(token: str) -> list[dict]:
    query = urllib.parse.urlencode(
        {
            "fields": "id,name,access_token,instagram_business_account{id,username}",
            "access_token": token,
        }
    )
    dados = _pedir(f"https://graph.facebook.com/{VERSAO}/me/accounts?{query}")
    return list(dados.get("data") or [])


def publicar_facebook(pagina_id: str, token: str, texto: str) -> None:
    _pedir(
        f"https://graph.facebook.com/{VERSAO}/{pagina_id}/feed",
        {"message": texto[:5000], "access_token": token},
    )


def publicar_instagram(conta_id: str, token: str, texto: str, imagem_url: str) -> None:
    criado = _pedir(
        f"https://graph.facebook.com/{VERSAO}/{conta_id}/media",
        {"image_url": imagem_url, "caption": texto[:2200], "access_token": token},
    )
    criacao = criado.get("id", "")
    if not criacao:
        raise RuntimeError("O Instagram não preparou a imagem.")
    _pedir(
        f"https://graph.facebook.com/{VERSAO}/{conta_id}/media_publish",
        {"creation_id": criacao, "access_token": token},
    )


def momento(dia: str, hora: str) -> datetime:
    ano, mes, dia_mes = (int(parte) for parte in dia.split("-"))
    horas, minutos = (int(parte) for parte in hora.split(":"))
    return datetime(ano, mes, dia_mes, horas, minutos, tzinfo=ZONA)


async def _conta(session: AsyncSession, rede: str) -> ContaRede | None:
    resultado = await session.exec(select(ContaRede).where(ContaRede.rede == rede))
    return resultado.first()


async def definir_conta(session: AsyncSession, rede: str, nome: str, externo_id: str, token: str) -> None:
    conta = await _conta(session, rede)
    if conta is None:
        session.add(ContaRede(rede=rede, nome=nome, externo_id=externo_id, token=token))
        return
    conta.nome = nome
    conta.externo_id = externo_id
    conta.token = token
    conta.atualizado_em = agora()


async def apagar_conta(session: AsyncSession, rede: str) -> None:
    conta = await _conta(session, rede)
    if conta is not None:
        await session.delete(conta)


async def guardar_codigo(session: AsyncSession, codigo: str) -> None:
    token = await asyncio.to_thread(trocar_codigo, codigo)
    paginas = await asyncio.to_thread(listar_paginas, token)
    if not paginas:
        raise RuntimeError("Nenhuma Página do Facebook apareceu nessa conta.")
    pagina = next((item for item in paginas if item.get("instagram_business_account")), paginas[0])
    await definir_conta(
        session,
        "facebook",
        str(pagina.get("name") or "Facebook"),
        str(pagina["id"]),
        str(pagina.get("access_token") or ""),
    )
    instagram = pagina.get("instagram_business_account") or {}
    if instagram.get("id"):
        await definir_conta(
            session,
            "instagram",
            str(instagram.get("username") or "Instagram"),
            str(instagram["id"]),
            str(pagina.get("access_token") or ""),
        )
    else:
        await apagar_conta(session, "instagram")
    await session.commit()


async def estado_contas(session: AsyncSession) -> dict[str, ContaRede | None]:
    return {
        "instagram": await _conta(session, "instagram"),
        "facebook": await _conta(session, "facebook"),
    }


async def publicar_vencidos(session: AsyncSession) -> None:
    agora_local = datetime.now(ZONA)
    resultado = await session.exec(select(Agendamento).where(Agendamento.status == "na_fila"))
    mudou = False
    for item in resultado.all():
        if momento(item.dia, item.hora) > agora_local:
            continue
        if item.rede not in REDES_PUBLICAVEIS:
            item.status = "manual"
            item.detalhe = "Essa rede ainda não publica sozinha."
            mudou = True
            continue
        if item.rede == "Instagram" and not item.imagem_url:
            item.status = "falta_imagem"
            item.detalhe = "O Instagram pede uma imagem para publicar."
            mudou = True
            continue
        conta = await _conta(session, item.rede.lower())
        if conta is None or not conta.token:
            item.detalhe = "Falta ligar a conta."
            mudou = True
            continue
        texto = f"{item.titulo}\n\n{item.legenda}".strip()
        try:
            if item.rede == "Facebook":
                await asyncio.to_thread(publicar_facebook, conta.externo_id, conta.token, texto)
            else:
                await asyncio.to_thread(
                    publicar_instagram, conta.externo_id, conta.token, texto, item.imagem_url
                )
            item.status = "publicado"
            item.detalhe = ""
        except Exception as erro:
            item.status = "erro"
            item.detalhe = str(erro)[:300]
        mudou = True
    if mudou:
        await session.commit()
