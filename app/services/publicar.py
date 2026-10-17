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
REDES_PUBLICAVEIS = {"Instagram", "Facebook", "Ambos"}


def meta_pronta() -> bool:
    ajustes = get_settings()
    return bool(ajustes.instagram_app_id.strip() and ajustes.instagram_app_secret.strip())


def facebook_pronta() -> bool:
    ajustes = get_settings()
    return bool(ajustes.meta_app_id.strip() and ajustes.meta_app_secret.strip())


def url_retorno() -> str:
    return get_settings().app_url.rstrip("/") + "/contas/retorno"


def endereco_publico(caminho: str) -> str:
    if caminho.startswith("http://") or caminho.startswith("https://"):
        return caminho
    if not caminho.startswith("/"):
        caminho = "/" + caminho
    return get_settings().app_url.rstrip("/") + caminho


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
    segredo = get_settings().instagram_app_secret.encode()
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
    segredo = get_settings().instagram_app_secret.encode()
    assinatura = hmac.new(segredo, corpo.encode(), hashlib.sha256).hexdigest()[:24]
    return hmac.compare_digest(assinatura, partes[2])


def criar_state_facebook() -> str:
    expira = int(time.time()) + 600
    corpo = f"facebook.{expira}"
    segredo = get_settings().meta_app_secret.encode()
    assinatura = hmac.new(segredo, corpo.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{corpo}.{assinatura}"


def state_facebook_valido(state: str) -> bool:
    partes = state.split(".")
    if len(partes) != 3 or partes[0] != "facebook":
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


def url_oauth_facebook() -> str:
    ajustes = get_settings()
    query = urllib.parse.urlencode(
        {
            "client_id": ajustes.meta_app_id,
            "redirect_uri": url_retorno(),
            "state": criar_state_facebook(),
            "scope": "pages_show_list,pages_manage_posts,business_management",
            "response_type": "code",
        }
    )
    return f"https://www.facebook.com/{VERSAO}/dialog/oauth?{query}"


def url_oauth() -> str:
    ajustes = get_settings()
    query = urllib.parse.urlencode(
        {
            "client_id": ajustes.instagram_app_id,
            "redirect_uri": url_retorno(),
            "state": criar_state(),
            "scope": "instagram_business_basic,instagram_business_content_publish",
            "response_type": "code",
        }
    )
    return f"https://www.instagram.com/oauth/authorize?{query}"


def trocar_codigo(codigo: str) -> tuple[str, str]:
    ajustes = get_settings()
    dados = _pedir(
        "https://api.instagram.com/oauth/access_token",
        {
            "client_id": ajustes.instagram_app_id,
            "client_secret": ajustes.instagram_app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": url_retorno(),
            "code": codigo,
        },
    )
    curto = str(dados.get("access_token") or "")
    usuario = str(dados.get("user_id") or "")
    if not curto:
        raise RuntimeError("O Instagram não devolveu o acesso.")
    longo = urllib.parse.urlencode(
        {
            "grant_type": "ig_exchange_token",
            "client_secret": ajustes.instagram_app_secret,
            "access_token": curto,
        }
    )
    estendido = _pedir(f"https://graph.instagram.com/access_token?{longo}")
    token = str(estendido.get("access_token") or curto)
    return token, usuario


def listar_paginas(token: str) -> list[dict]:
    query = urllib.parse.urlencode(
        {"fields": "id,name,access_token", "access_token": token}
    )
    dados = _pedir(f"https://graph.facebook.com/{VERSAO}/me/accounts?{query}")
    return list(dados.get("data") or [])


def trocar_codigo_facebook(codigo: str) -> str:
    ajustes = get_settings()
    curto = _pedir(
        f"https://graph.facebook.com/{VERSAO}/oauth/access_token",
        {
            "client_id": ajustes.meta_app_id,
            "client_secret": ajustes.meta_app_secret,
            "redirect_uri": url_retorno(),
            "code": codigo,
        },
    )
    token_curto = str(curto.get("access_token") or "")
    if not token_curto:
        raise RuntimeError("O Facebook não devolveu o acesso.")
    estendido = _pedir(
        f"https://graph.facebook.com/{VERSAO}/oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": ajustes.meta_app_id,
            "client_secret": ajustes.meta_app_secret,
            "fb_exchange_token": token_curto,
        },
    )
    return str(estendido.get("access_token") or token_curto)


def nomes_paginas(token: str) -> list[dict[str, str]]:
    return [
        {"id": str(item.get("id") or ""), "nome": str(item.get("name") or "Página")}
        for item in listar_paginas(token)
        if item.get("id")
    ]


def publicar_facebook(pagina_id: str, token: str, texto: str) -> None:
    _pedir(
        f"https://graph.facebook.com/{VERSAO}/{pagina_id}/feed",
        {"message": texto[:5000], "access_token": token},
    )


def publicar_facebook_foto(pagina_id: str, token: str, texto: str, imagem_url: str) -> None:
    _pedir(
        f"https://graph.facebook.com/{VERSAO}/{pagina_id}/photos",
        {"url": imagem_url, "caption": texto[:5000], "access_token": token},
    )


def publicar_facebook_video(pagina_id: str, token: str, texto: str, video_url: str) -> None:
    _pedir(
        f"https://graph.facebook.com/{VERSAO}/{pagina_id}/videos",
        {"file_url": video_url, "description": texto[:5000], "access_token": token},
    )


def _esperar_midia(criacao: str, token: str, o_que: str) -> None:
    for _ in range(12):
        time.sleep(2)
        consulta = urllib.parse.urlencode({"fields": "status_code,status", "access_token": token})
        estado = _pedir(f"https://graph.instagram.com/{VERSAO}/{criacao}?{consulta}")
        codigo = str(estado.get("status_code") or "")
        if codigo == "FINISHED":
            return
        if codigo == "ERROR":
            detalhe = str(estado.get("status") or "").lower()
            if "ratio" in detalhe or "aspect" in detalhe:
                raise RuntimeError("O Instagram não aceitou a proporção da foto. Anexe de novo.")
            raise RuntimeError(f"O Instagram não aceitou {o_que}. Anexe de novo e clique em Criar agora.")
    raise RuntimeError(f"O Instagram ainda está preparando {o_que}. Tente de novo em instantes.")


def publicar_instagram(conta_id: str, token: str, texto: str, imagem_url: str) -> None:
    criado = _pedir(
        f"https://graph.instagram.com/{VERSAO}/{conta_id}/media",
        {"image_url": imagem_url, "caption": texto[:2200], "access_token": token},
    )
    criacao = str(criado.get("id") or "")
    if not criacao:
        raise RuntimeError("O Instagram não preparou a imagem.")
    _esperar_midia(criacao, token, "a foto")
    _pedir(
        f"https://graph.instagram.com/{VERSAO}/{conta_id}/media_publish",
        {"creation_id": criacao, "access_token": token},
    )


def publicar_instagram_video(conta_id: str, token: str, texto: str, video_url: str) -> None:
    criado = _pedir(
        f"https://graph.instagram.com/{VERSAO}/{conta_id}/media",
        {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": texto[:2200],
            "access_token": token,
        },
    )
    criacao = str(criado.get("id") or "")
    if not criacao:
        raise RuntimeError("O Instagram não preparou o vídeo.")
    _esperar_midia(criacao, token, "o vídeo")
    _pedir(
        f"https://graph.instagram.com/{VERSAO}/{conta_id}/media_publish",
        {"creation_id": criacao, "access_token": token},
    )


def _publicar_rede(rede: str, conta: ContaRede, texto: str, imagem_url: str, video_url: str) -> None:
    video = video_url.strip()
    imagem = imagem_url.strip()
    if video and not video.lower().split("?", 1)[0].endswith(".mp4"):
        raise RuntimeError("Para publicar o vídeo, envie um arquivo MP4.")
    if rede == "Facebook":
        if video:
            publicar_facebook_video(conta.externo_id, conta.token, texto, endereco_publico(video))
        elif imagem:
            publicar_facebook_foto(conta.externo_id, conta.token, texto, endereco_publico(imagem))
        else:
            publicar_facebook(conta.externo_id, conta.token, texto)
        return
    if video:
        publicar_instagram_video(conta.externo_id, conta.token, texto, endereco_publico(video))
        return
    publicar_instagram(conta.externo_id, conta.token, texto, endereco_publico(imagem))


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


def perfil_instagram(token: str) -> tuple[str, str]:
    query = urllib.parse.urlencode({"fields": "user_id,username", "access_token": token})
    dados = _pedir(f"https://graph.instagram.com/{VERSAO}/me?{query}")
    return str(dados.get("user_id") or ""), str(dados.get("username") or "Instagram")


async def guardar_codigo_facebook(session: AsyncSession, codigo: str) -> None:
    token = await asyncio.to_thread(trocar_codigo_facebook, codigo)
    if not token:
        raise RuntimeError("O Facebook não devolveu o acesso.")
    await definir_conta(session, "facebook_acesso", "", "", token)
    await session.commit()


async def paginas_facebook(session: AsyncSession) -> tuple[list[dict[str, str]], str]:
    acesso = await _conta(session, "facebook_acesso")
    escolhida = await _conta(session, "facebook")
    escolhida_id = escolhida.externo_id if escolhida and escolhida.token else ""
    if acesso is None or not acesso.token:
        return [], escolhida_id
    try:
        paginas = await asyncio.to_thread(nomes_paginas, acesso.token)
    except Exception:
        return [], escolhida_id
    return paginas, escolhida_id


async def escolher_pagina_facebook(session: AsyncSession, pagina_id: str) -> str:
    acesso = await _conta(session, "facebook_acesso")
    if acesso is None or not acesso.token:
        raise RuntimeError("Ligue o Facebook de novo para ver as Páginas.")
    paginas = await asyncio.to_thread(listar_paginas, acesso.token)
    pagina = next((item for item in paginas if str(item.get("id") or "") == pagina_id), None)
    if pagina is None:
        raise RuntimeError("Essa Página não está nesta conta.")
    nome = str(pagina.get("name") or "Facebook")
    token_pagina = str(pagina.get("access_token") or "")
    if not token_pagina:
        raise RuntimeError("O Facebook não entregou a Página.")
    await definir_conta(session, "facebook", nome, pagina_id, token_pagina)
    await session.commit()
    return nome


async def guardar_codigo(session: AsyncSession, codigo: str) -> None:
    token, usuario = await asyncio.to_thread(trocar_codigo, codigo)
    try:
        usuario_perfil, nome = await asyncio.to_thread(perfil_instagram, token)
        if usuario_perfil:
            usuario = usuario_perfil
    except Exception:
        nome = "Instagram"
    if not usuario:
        raise RuntimeError("O Instagram não informou a conta.")
    await definir_conta(session, "instagram", nome, usuario, token)
    await session.commit()


async def estado_contas(session: AsyncSession) -> dict[str, ContaRede | None]:
    return {
        "instagram": await _conta(session, "instagram"),
        "facebook": await _conta(session, "facebook"),
    }


async def publicar_agora(
    session: AsyncSession,
    rede: str,
    titulo: str,
    legenda: str,
    imagem_url: str,
    video_url: str = "",
) -> tuple[str, str]:
    if rede not in REDES_PUBLICAVEIS:
        return "manual", "Essa rede ainda não publica sozinha."
    if rede == "Instagram" and not imagem_url.strip() and not video_url.strip():
        return "falta_imagem", "O Instagram pede uma foto ou um vídeo."
    conta = await _conta(session, rede.lower())
    if conta is None or not conta.token:
        return "falta_conta", "Falta ligar a conta."
    texto = f"{titulo.strip()}\n\n{legenda.strip()}".strip()
    try:
        await asyncio.to_thread(_publicar_rede, rede, conta, texto, imagem_url, video_url)
    except Exception as erro:
        detalhe = str(erro)
        if "Media ID is not available" in detalhe:
            detalhe = "O Instagram não conseguiu usar essa foto. Anexe de novo e clique em Criar agora."
        return "erro", detalhe[:300]
    return "publicado", ""


def _erro_foto(erro: Exception) -> str:
    detalhe = str(erro).replace("\n", " ")
    if "insufficient_quota" in detalhe or "credit_balance" in detalhe:
        return "A conta da OpenAI está sem créditos."
    if "api_key" in detalhe.lower() or "sk-" in detalhe:
        return "A foto não saiu agora."
    if "Media ID is not available" in detalhe:
        return "O Instagram não conseguiu usar essa foto."
    return detalhe[:300]


async def preparar_fotos(session: AsyncSession) -> None:
    from app.services.midia import foto_da_cena

    agora_local = datetime.now(ZONA)
    resultado = await session.exec(select(Agendamento).where(Agendamento.status == "na_fila"))
    for item in resultado.all():
        if momento(item.dia, item.hora) > agora_local:
            continue
        if item.imagem_url or item.video_url or not (item.cena or "").strip():
            continue
        if item.tentativas >= 3:
            item.status = "erro"
            item.detalhe = "A foto não saiu depois de 3 tentativas. O texto não foi publicado."
            await session.commit()
            return
        item.tentativas += 1
        await session.commit()
        try:
            item.imagem_url = (await foto_da_cena(item.cena))[:500]
            item.detalhe = ""
        except Exception as erro:
            item.detalhe = _erro_foto(erro)
        await session.commit()
        return


async def _publicar_ambos(session: AsyncSession, item: Agendamento) -> None:
    texto = f"{item.titulo}\n\n{item.legenda}".strip()
    erros: list[str] = []
    if not item.ok_instagram:
        conta = await _conta(session, "instagram")
        if conta is None or not conta.token:
            erros.append("Falta ligar o Instagram.")
        else:
            try:
                await asyncio.to_thread(
                    _publicar_rede,
                    "Instagram",
                    conta,
                    texto,
                    item.imagem_url,
                    item.video_url,
                )
                item.ok_instagram = True
            except Exception as erro:
                erros.append(_erro_foto(erro)[:180])
    if not item.ok_facebook:
        conta = await _conta(session, "facebook")
        if conta is None or not conta.token:
            erros.append("Falta ligar o Facebook.")
        else:
            try:
                await asyncio.to_thread(
                    _publicar_rede,
                    "Facebook",
                    conta,
                    texto,
                    item.imagem_url,
                    item.video_url,
                )
                item.ok_facebook = True
            except Exception as erro:
                erros.append(_erro_foto(erro)[:180])
    if item.ok_instagram and item.ok_facebook:
        item.status = "publicado"
        item.detalhe = ""
    else:
        item.detalhe = " ".join(erros)[:300]


async def publicar_vencidos(session: AsyncSession) -> None:
    agora_local = datetime.now(ZONA)
    resultado = await session.exec(select(Agendamento).where(Agendamento.status == "na_fila"))
    mudou = False
    for item in resultado.all():
        if momento(item.dia, item.hora) > agora_local:
            continue
        if item.rede == "Ambos":
            if not item.imagem_url and not item.video_url:
                continue
            await _publicar_ambos(session, item)
            mudou = True
            continue
        if item.rede not in REDES_PUBLICAVEIS:
            item.status = "manual"
            item.detalhe = "Essa rede ainda não publica sozinha."
            mudou = True
            continue
        if item.rede == "Instagram" and not item.imagem_url and not item.video_url:
            item.status = "falta_imagem"
            item.detalhe = "O Instagram pede uma foto ou um vídeo."
            mudou = True
            continue
        conta = await _conta(session, item.rede.lower())
        if conta is None or not conta.token:
            item.detalhe = "Falta ligar a conta."
            mudou = True
            continue
        texto = f"{item.titulo}\n\n{item.legenda}".strip()
        try:
            await asyncio.to_thread(
                _publicar_rede,
                item.rede,
                conta,
                texto,
                item.imagem_url,
                item.video_url,
            )
            item.status = "publicado"
            item.detalhe = ""
        except Exception as erro:
            item.status = "erro"
            item.detalhe = _erro_foto(erro)
        mudou = True
    if mudou:
        await session.commit()
