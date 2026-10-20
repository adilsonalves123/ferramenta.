import asyncio
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.models import Agendamento, ContaRede, Ficha, agora
from app.services.diretor import ler_plano

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


def _pedir(url: str, dados: dict | None = None, timeout: int = 40) -> dict:
    corpo = urllib.parse.urlencode(dados).encode() if dados is not None else None
    pedido = urllib.request.Request(url, data=corpo)
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            return json.loads(resposta.read().decode())
    except urllib.error.HTTPError as erro:
        texto = erro.read().decode()
        try:
            mensagem = json.loads(texto).get("error", {}).get("message", texto)
        except json.JSONDecodeError:
            mensagem = texto
        raise RuntimeError(mensagem[:300]) from erro


def _assinar(prefixo: str, marca_id: int, segredo: str) -> str:
    expira = int(time.time()) + 600
    corpo = f"{prefixo}.{marca_id}.{expira}"
    assinatura = hmac.new(segredo.encode(), corpo.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{corpo}.{assinatura}"


def _marca_do_state(state: str, prefixo: str, segredo: str) -> int | None:
    partes = state.split(".")
    if len(partes) != 4 or partes[0] != prefixo:
        return None
    try:
        marca_id = int(partes[1])
        expira = int(partes[2])
    except ValueError:
        return None
    if marca_id <= 0 or expira < int(time.time()):
        return None
    corpo = f"{partes[0]}.{partes[1]}.{partes[2]}"
    assinatura = hmac.new(segredo.encode(), corpo.encode(), hashlib.sha256).hexdigest()[:24]
    if not hmac.compare_digest(assinatura, partes[3]):
        return None
    return marca_id


def criar_state(marca_id: int) -> str:
    return _assinar("contas", marca_id, get_settings().instagram_app_secret)


def marca_do_state(state: str) -> int | None:
    return _marca_do_state(state, "contas", get_settings().instagram_app_secret)


def criar_state_facebook(marca_id: int) -> str:
    return _assinar("facebook", marca_id, get_settings().meta_app_secret)


def marca_do_state_facebook(state: str) -> int | None:
    return _marca_do_state(state, "facebook", get_settings().meta_app_secret)


def url_oauth_facebook(marca_id: int) -> str:
    ajustes = get_settings()
    query = urllib.parse.urlencode(
        {
            "client_id": ajustes.meta_app_id,
            "redirect_uri": url_retorno(),
            "state": criar_state_facebook(marca_id),
            "scope": "pages_show_list,pages_read_engagement,pages_manage_posts,read_insights",
            "response_type": "code",
        }
    )
    return f"https://www.facebook.com/{VERSAO}/dialog/oauth?{query}"


def url_oauth(marca_id: int) -> str:
    ajustes = get_settings()
    query = urllib.parse.urlencode(
        {
            "client_id": ajustes.instagram_app_id,
            "redirect_uri": url_retorno(),
            "state": criar_state(marca_id),
            "scope": "instagram_business_basic,instagram_business_content_publish,instagram_business_manage_insights",
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


def publicar_facebook(pagina_id: str, token: str, texto: str) -> str:
    """Devolve o id do post ({pagina}_{post})."""
    criado = _pedir(
        f"https://graph.facebook.com/{VERSAO}/{pagina_id}/feed",
        {"message": texto[:5000], "access_token": token},
    )
    return str(criado.get("id") or "")


def publicar_facebook_foto(pagina_id: str, token: str, texto: str, imagem_url: str) -> str:
    """Devolve o post_id (o 'id' da resposta é o da foto, não o do post)."""
    criado = _pedir(
        f"https://graph.facebook.com/{VERSAO}/{pagina_id}/photos",
        {"url": imagem_url, "caption": texto[:5000], "access_token": token},
    )
    return str(criado.get("post_id") or criado.get("id") or "")


def publicar_facebook_video(pagina_id: str, token: str, texto: str, video_url: str) -> str:
    """Devolve o id do vídeo; a resposta de /videos não traz o post_id."""
    criado = _pedir(
        f"https://graph.facebook.com/{VERSAO}/{pagina_id}/videos",
        {"file_url": video_url, "description": texto[:5000], "access_token": token},
    )
    return str(criado.get("id") or "")


ESPERA_FOTO = [2] * 12
ESPERA_VIDEO = [3, 5, 8, 12, 15] + [20] * 7


def _esperar_midia(criacao: str, token: str, o_que: str, video: bool = False) -> None:
    for pausa in ESPERA_VIDEO if video else ESPERA_FOTO:
        time.sleep(pausa)
        consulta = urllib.parse.urlencode({"fields": "status_code,status", "access_token": token})
        estado = _pedir(f"https://graph.instagram.com/{VERSAO}/{criacao}?{consulta}")
        codigo = str(estado.get("status_code") or "")
        if codigo == "FINISHED":
            return
        if codigo == "ERROR":
            detalhe = str(estado.get("status") or "").lower()
            if "ratio" in detalhe or "aspect" in detalhe:
                raise RuntimeError("O Instagram não aceitou a proporção da foto. Anexe de novo.")
            raise RuntimeError(f"O Instagram não aceitou {o_que}. Anexe de novo e clique em Publicar agora.")
    raise RuntimeError(f"O Instagram ainda está preparando {o_que}. Tente de novo em instantes.")


def _publicar_criacao(conta_id: str, token: str, criacao: str) -> str:
    """media_publish devolve o id da mídia publicada."""
    publicado = _pedir(
        f"https://graph.instagram.com/{VERSAO}/{conta_id}/media_publish",
        {"creation_id": criacao, "access_token": token},
    )
    return str(publicado.get("id") or "")


def publicar_instagram(conta_id: str, token: str, texto: str, imagem_url: str) -> str:
    criado = _pedir(
        f"https://graph.instagram.com/{VERSAO}/{conta_id}/media",
        {"image_url": imagem_url, "caption": texto[:2200], "access_token": token},
    )
    criacao = str(criado.get("id") or "")
    if not criacao:
        raise RuntimeError("O Instagram não preparou a imagem.")
    _esperar_midia(criacao, token, "a foto")
    return _publicar_criacao(conta_id, token, criacao)


def publicar_instagram_video(conta_id: str, token: str, texto: str, video_url: str) -> str:
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
    _esperar_midia(criacao, token, "o vídeo", video=True)
    return _publicar_criacao(conta_id, token, criacao)


def _publicar_rede(rede: str, conta: ContaRede, texto: str, imagem_url: str, video_url: str) -> tuple[str, str]:
    """Publica no feed e devolve (id criado na rede, formato: feed, reels ou video)."""
    video = video_url.strip()
    imagem = imagem_url.strip()
    if video and not video.lower().split("?", 1)[0].endswith(".mp4"):
        raise RuntimeError("Para publicar o vídeo, envie um arquivo MP4.")
    if rede == "Facebook":
        if video:
            return publicar_facebook_video(conta.externo_id, conta.token, texto, endereco_publico(video)), "video"
        if imagem:
            return publicar_facebook_foto(conta.externo_id, conta.token, texto, endereco_publico(imagem)), "feed"
        return publicar_facebook(conta.externo_id, conta.token, texto), "feed"
    if video:
        return publicar_instagram_video(conta.externo_id, conta.token, texto, endereco_publico(video)), "reels"
    return publicar_instagram(conta.externo_id, conta.token, texto, endereco_publico(imagem)), "feed"


def publicar_instagram_story(conta_id: str, token: str, imagem_url: str) -> str:
    criado = _pedir(
        f"https://graph.instagram.com/{VERSAO}/{conta_id}/media",
        {"media_type": "STORIES", "image_url": imagem_url, "access_token": token},
    )
    criacao = str(criado.get("id") or "")
    if not criacao:
        raise RuntimeError("O Instagram não preparou o Story.")
    _esperar_midia(criacao, token, "o Story")
    return _publicar_criacao(conta_id, token, criacao)


def publicar_instagram_story_video(conta_id: str, token: str, video_url: str) -> str:
    criado = _pedir(
        f"https://graph.instagram.com/{VERSAO}/{conta_id}/media",
        {"media_type": "STORIES", "video_url": video_url, "access_token": token},
    )
    criacao = str(criado.get("id") or "")
    if not criacao:
        raise RuntimeError("O Instagram não preparou o Story.")
    _esperar_midia(criacao, token, "o Story", video=True)
    return _publicar_criacao(conta_id, token, criacao)


def publicar_facebook_story(pagina_id: str, token: str, imagem_url: str) -> str:
    """photo_stories responde {success, post_id}; devolve o post_id."""
    foto = _pedir(
        f"https://graph.facebook.com/{VERSAO}/{pagina_id}/photos",
        {"url": imagem_url, "published": "false", "access_token": token},
    )
    photo_id = str(foto.get("id") or "")
    if not photo_id:
        raise RuntimeError("O Facebook não preparou a foto do Story.")
    story = _pedir(
        f"https://graph.facebook.com/{VERSAO}/{pagina_id}/photo_stories",
        {"photo_id": photo_id, "access_token": token},
    )
    return str(story.get("post_id") or "")


def alvos_de(rede: str, formato: str) -> list[str]:
    redes = ["Instagram", "Facebook"] if rede == "Ambos" else [rede]
    formatos = ["feed", "story"] if formato == "ambos" else [formato]
    return [f"{nome}:{tipo}" for nome in redes for tipo in formatos]


def _publicar_alvo(
    alvo: str, conta: ContaRede, texto: str, imagem_url: str, video_url: str, foto_story: str = ""
) -> tuple[str, str]:
    """Publica um alvo ('Rede:formato') e devolve (id criado na rede, formato publicado)."""
    rede, formato = alvo.split(":", 1)
    video = video_url.strip()
    imagem = imagem_url.strip()
    if formato == "story":
        if not imagem and not video:
            raise RuntimeError("O Story pede uma foto.")
        if rede == "Facebook":
            if video and not imagem:
                raise RuntimeError("O Story do Facebook sai com foto.")
            return publicar_facebook_story(
                conta.externo_id, conta.token, endereco_publico(foto_story or imagem)
            ), "story"
        if video and video.lower().split("?", 1)[0].endswith(".mp4"):
            return publicar_instagram_story_video(conta.externo_id, conta.token, endereco_publico(video)), "story"
        return publicar_instagram_story(
            conta.externo_id, conta.token, endereco_publico(foto_story or imagem)
        ), "story"
    return _publicar_rede(rede, conta, texto, imagem, video)


LIMITE_LEGENDA = 2200


def com_site(texto: str, site: str) -> str:
    site = site.strip()
    if not site:
        return texto
    chave = site.lower().split("://", 1)[-1].removeprefix("www.").rstrip("/")
    if chave and chave in texto.lower():
        return texto
    corpo = texto.rstrip()[: max(0, LIMITE_LEGENDA - len(site) - 2)].rstrip()
    return f"{corpo}\n\n{site}" if corpo else site


def texto_do_post(titulo: str, legenda: str) -> str:
    """A legenda editada na prévia já começa pelo título; aí o título não entra de novo."""
    titulo, legenda = titulo.strip(), legenda.strip()
    if titulo and legenda.startswith(titulo):
        return legenda
    return f"{titulo}\n\n{legenda}".strip()


def _usa_foto(alvo: str, imagem_url: str, video_url: str) -> bool:
    rede, formato = alvo.split(":", 1)
    video = video_url.strip().lower().split("?", 1)[0]
    if not imagem_url.strip():
        return False
    if formato == "story":
        return rede == "Facebook" or not video.endswith(".mp4")
    return not video


async def _variante(base_url: str, chave: str, marca_id: int, desenhar) -> str:
    """Foto derivada (feed com marca d'água, Story) guardada no banco; reaproveita a que já foi feita."""
    from app.services.imagens import achar_variante, bytes_da_url, guardar_figura, id_da_url

    base_id = id_da_url(base_url)
    if base_id is not None:
        pronta = await achar_variante(base_id, chave)
        if pronta:
            return pronta
    dados = await bytes_da_url(base_url)
    figura = await asyncio.to_thread(desenhar, dados)
    return await guardar_figura(figura, marca_id, chave.split(":", 1)[0], base_id, chave)


async def fotos_com_marca(
    alvos: list[str],
    imagem_url: str,
    video_url: str,
    marca_dagua: str,
    gancho: str = "",
    story_base: str = "",
    marca_id: int = 0,
    plano: dict | None = None,
) -> tuple[str, str]:
    """Foto do feed com a marca d'água e o Story 1080x1920 (fundo desfocado, gancho e chamada).
    Com a arte do diretor (plano), o feed e o Story já saem prontos, com o logo. Vídeo segue sem marca d'água."""
    from app.services import arte
    from app.services.imagens import existe
    from app.services.memoria import CTA_STORY

    usados = [alvo for alvo in alvos if _usa_foto(alvo, imagem_url, video_url)]
    arte_pronta = bool(plano and plano.get("feed_url") and plano["feed_url"] == imagem_url.strip())
    if arte_pronta:
        story_arte = plano.get("story_url") or ""
        if not any(alvo.endswith(":story") for alvo in usados):
            return imagem_url, ""
        if story_arte and await existe(story_arte):
            return imagem_url, story_arte
    feed, story = imagem_url, ""
    marca_dagua = marca_dagua.strip()
    if marca_dagua and not arte_pronta and any(alvo.endswith(":feed") for alvo in usados):
        chave = "feed:" + hashlib.sha1(marca_dagua.encode()).hexdigest()[:16]
        feed = await _variante(
            imagem_url, chave, marca_id, lambda dados: arte.carimbar(arte.abrir(dados), marca_dagua)
        )
    if any(alvo.endswith(":story") for alvo in usados):
        base = story_base or imagem_url
        chave = "story:" + hashlib.sha1(f"{gancho}|{CTA_STORY}|{marca_dagua}|{base}".encode()).hexdigest()[:16]
        story = await _variante(
            base, chave, marca_id, lambda dados: arte.story(arte.abrir(dados), gancho, CTA_STORY, marca_dagua)
        )
    return feed, story


async def dados_marca(session: AsyncSession, marca_id: int) -> tuple[str, str]:
    if marca_id <= 0:
        return "", ""
    registro = await session.get(Ficha, marca_id)
    if registro is None:
        return "", ""
    return (registro.site or "").strip(), (registro.marca_dagua or "").strip()


def erro_marca(erro: Exception) -> str:
    return f"A marca d'água não entrou na foto: {str(erro)[:160]}"


def momento(dia: str, hora: str) -> datetime:
    ano, mes, dia_mes = (int(parte) for parte in dia.split("-"))
    horas, minutos = (int(parte) for parte in hora.split(":"))
    return datetime(ano, mes, dia_mes, horas, minutos, tzinfo=ZONA)


async def marca_efetiva(session: AsyncSession, marca_id: int) -> int:
    if marca_id > 0:
        return marca_id
    resultado = await session.exec(select(Ficha).order_by(Ficha.id))
    primeira = resultado.first()
    return primeira.id if primeira and primeira.id else 0


async def _conta(session: AsyncSession, rede: str, marca_id: int) -> ContaRede | None:
    if marca_id <= 0:
        return None
    resultado = await session.exec(
        select(ContaRede).where(ContaRede.rede == rede, ContaRede.marca_id == marca_id)
    )
    return resultado.first()


async def definir_conta(
    session: AsyncSession,
    marca_id: int,
    rede: str,
    nome: str,
    externo_id: str,
    token: str,
    usuario_meta: str = "",
) -> None:
    conta = await _conta(session, rede, marca_id)
    if conta is None:
        session.add(
            ContaRede(
                marca_id=marca_id,
                rede=rede,
                nome=nome,
                externo_id=externo_id,
                token=token,
                usuario_meta=usuario_meta[:200],
            )
        )
        return
    conta.nome = nome
    conta.externo_id = externo_id
    conta.token = token
    conta.usuario_meta = usuario_meta[:200]
    conta.atualizado_em = agora()


async def apagar_conta(session: AsyncSession, rede: str, marca_id: int) -> None:
    conta = await _conta(session, rede, marca_id)
    if conta is not None:
        await session.delete(conta)


def perfil_instagram(token: str) -> tuple[str, str, str]:
    query = urllib.parse.urlencode({"fields": "id,user_id,username", "access_token": token})
    dados = _pedir(f"https://graph.instagram.com/{VERSAO}/me?{query}")
    return (
        str(dados.get("user_id") or ""),
        str(dados.get("username") or "Instagram"),
        str(dados.get("id") or ""),
    )


def usuario_facebook(token: str) -> str:
    query = urllib.parse.urlencode({"fields": "id", "access_token": token})
    dados = _pedir(f"https://graph.facebook.com/{VERSAO}/me?{query}")
    return str(dados.get("id") or "")


def juntar_ids(*ids: str) -> str:
    return ",".join(dict.fromkeys(item for item in ids if item))


async def guardar_codigo_facebook(session: AsyncSession, codigo: str, marca_id: int) -> None:
    token = await asyncio.to_thread(trocar_codigo_facebook, codigo)
    if not token:
        raise RuntimeError("O Facebook não devolveu o acesso.")
    try:
        usuario = await asyncio.to_thread(usuario_facebook, token)
    except Exception:
        usuario = ""
    await definir_conta(session, marca_id, "facebook_acesso", "", "", token, usuario)
    await session.commit()


async def paginas_facebook(session: AsyncSession, marca_id: int) -> tuple[list[dict[str, str]], str]:
    acesso = await _conta(session, "facebook_acesso", marca_id)
    escolhida = await _conta(session, "facebook", marca_id)
    escolhida_id = escolhida.externo_id if escolhida and escolhida.token else ""
    if acesso is None or not acesso.token:
        return [], escolhida_id
    try:
        paginas = await asyncio.to_thread(nomes_paginas, acesso.token)
    except Exception:
        return [], escolhida_id
    return paginas, escolhida_id


async def escolher_pagina_facebook(session: AsyncSession, pagina_id: str, marca_id: int) -> str:
    acesso = await _conta(session, "facebook_acesso", marca_id)
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
    await definir_conta(
        session, marca_id, "facebook", nome, pagina_id, token_pagina, acesso.usuario_meta or ""
    )
    await session.commit()
    return nome


async def guardar_codigo(session: AsyncSession, codigo: str, marca_id: int) -> None:
    token, usuario = await asyncio.to_thread(trocar_codigo, codigo)
    usuario_app = usuario
    escopo = ""
    try:
        usuario_perfil, nome, escopo = await asyncio.to_thread(perfil_instagram, token)
        if usuario_perfil:
            usuario = usuario_perfil
    except Exception:
        nome = "Instagram"
    if not usuario:
        raise RuntimeError("O Instagram não informou a conta.")
    await definir_conta(
        session, marca_id, "instagram", nome, usuario, token, juntar_ids(escopo, usuario_app, usuario)
    )
    await session.commit()


async def estado_contas(session: AsyncSession, marca_id: int) -> dict[str, ContaRede | None]:
    return {
        "instagram": await _conta(session, "instagram", marca_id),
        "facebook": await _conta(session, "facebook", marca_id),
    }


def falta_conta(rede: str) -> str:
    return f"Falta ligar o {rede} desta marca."


async def contas_faltando(session: AsyncSession, alvos: list[str], marca_id: int) -> list[str]:
    faltam: list[str] = []
    for alvo in alvos:
        nome = alvo.split(":", 1)[0]
        if nome in faltam:
            continue
        conta = await _conta(session, nome.lower(), marca_id)
        if conta is None or not conta.token:
            faltam.append(nome)
    return faltam


async def publicar_agora(
    session: AsyncSession,
    rede: str,
    titulo: str,
    legenda: str,
    imagem_url: str,
    video_url: str = "",
    formato: str = "feed",
    marca_id: int = 0,
    tema: str = "",
    gancho: str = "",
    foto_id: int | None = None,
    plano: dict | None = None,
) -> tuple[str, str]:
    from app.services.memoria import base_do_story
    from app.services.resultados import anotar_publicacao

    formato = formato if formato in {"feed", "story", "ambos"} else "feed"
    if rede not in REDES_PUBLICAVEIS:
        return "manual", "Essa rede ainda não publica sozinha."
    alvos = alvos_de(rede, formato)
    precisa_midia = any(alvo.endswith(":story") or alvo.startswith("Instagram:") for alvo in alvos)
    if precisa_midia and not imagem_url.strip() and not video_url.strip():
        return "falta_imagem", "Esse formato pede uma foto ou um vídeo."
    marca = await marca_efetiva(session, marca_id)
    faltam = await contas_faltando(session, alvos, marca)
    if faltam:
        return "falta_conta", " ".join(falta_conta(nome) for nome in faltam)
    site, marca_dagua = await dados_marca(session, marca)
    texto = com_site(texto_do_post(titulo, legenda), site)
    try:
        foto_feed, foto_story = await fotos_com_marca(
            alvos, imagem_url, video_url, marca_dagua, gancho, await base_do_story(session, foto_id), marca, plano
        )
    except Exception as erro:
        return "erro", (erro_marca(erro) + " Nada foi publicado.")[:300]
    erros: list[str] = []
    for alvo in alvos:
        nome = alvo.split(":", 1)[0]
        conta = await _conta(session, nome.lower(), marca)
        if conta is None or not conta.token:
            erros.append(falta_conta(nome))
            continue
        try:
            externo_id, publicado_como = await asyncio.to_thread(
                _publicar_alvo, alvo, conta, texto, foto_feed, video_url, foto_story
            )
        except Exception as erro:
            erros.append(_erro_foto(erro)[:180])
            continue
        await anotar_publicacao(
            marca, nome, publicado_como, externo_id, conta.token, texto,
            _foto_do_alvo(alvo, foto_feed, foto_story, imagem_url, video_url), tema,
            pagina_id=conta.externo_id,
        )
    if erros:
        return "erro", " ".join(erros)[:300]
    return "publicado", ""


def _foto_do_alvo(alvo: str, foto_feed: str, foto_story: str, imagem_url: str, video_url: str) -> str:
    if not _usa_foto(alvo, imagem_url, video_url):
        return ""
    if alvo.endswith(":story"):
        return foto_story or imagem_url
    return foto_feed or imagem_url


def _erro_foto(erro: Exception) -> str:
    detalhe = str(erro).replace("\n", " ")
    if "insufficient_quota" in detalhe or "credit_balance" in detalhe:
        return "A conta da OpenAI está sem créditos."
    if "api_key" in detalhe.lower() or "sk-" in detalhe:
        return "A foto não saiu agora."
    if "Media ID is not available" in detalhe:
        return "O Instagram não conseguiu usar essa foto."
    return detalhe[:300]


ANTECEDENCIA_FOTO = timedelta(hours=3)
LIMITE_FOTOS = 3


async def foto_sumiu(imagem_url: str) -> bool:
    """True quando a foto do post não existe mais (no banco, ou no disco antigo /midia)."""
    from app.services.imagens import existe

    if not imagem_url.strip():
        return False
    return not await existe(imagem_url)


def _foto_automatica(item: Agendamento) -> bool:
    """O post tem como a foto ser feita sozinha: cena, foto da memória ou plano de arte."""
    return not item.video_url and bool((item.cena or "").strip() or item.foto_id or ler_plano(item.plano_arte))


async def _refaz_foto(item: Agendamento) -> bool:
    if not _foto_automatica(item):
        return False
    return await foto_sumiu(item.imagem_url or "")


async def _foto_da_memoria(session: AsyncSession, item: Agendamento) -> str:
    """Monta a foto do post com a foto real da memória. Devolve '' se a foto não existe mais."""
    from app.services.memoria import imagem_da_memoria

    if not item.foto_id:
        return ""
    marca = await session.get(Ficha, await marca_efetiva(session, item.marca_id))
    try:
        return (await imagem_da_memoria(session, item.foto_id, item.gancho or "", marca))[:500]
    except Exception:
        return ""


async def _gerar_arte(
    session: AsyncSession, item: Agendamento, plano: dict, automatica: bool, forcar_ia: bool
) -> str:
    """Arte do diretor: foto base (reaproveitada, original, editada ou criada) + molde em feed e Story.
    Só conta tentativa quando a IA de imagem roda."""
    from app.services import diretor

    marca_id = await marca_efetiva(session, item.marca_id)
    marca = await session.get(Ficha, marca_id)
    if await diretor.vai_usar_ia(session, marca_id, plano, forcar_ia):
        item.tentativas = (item.tentativas or 0) + 1
        await session.commit()
    try:
        feito = await diretor.produzir(
            session, marca, plano, cena=item.cena or "", forcar_ia=forcar_ia, reusar_base=not forcar_ia
        )
        item.imagem_url = feito["feed_url"][:500]
        item.foto_id = feito.get("foto_base_id")
        item.plano_arte = diretor.gravar_plano(feito)
        item.detalhe = ""
        if automatica:
            item.tentativas = 0
        falha = ""
    except Exception as erro:
        falha = _erro_foto(erro)
        item.detalhe = falha
    await session.commit()
    return falha


async def _gerar_foto(session: AsyncSession, item: Agendamento, automatica: bool = False, forcar_ia: bool = False) -> str:
    """Faz a foto do post. Com plano de arte, o diretor monta a peça (edita a foto da memória ou cria).
    Sem plano: com foto da memória, usa a foto real (sem IA, sem custo); sem ela, cria com IA
    (conta uma tentativa), com a foto real do lugar como referência.
    Devolve o erro, ou '' quando deu certo.

    Na do robô (automatica), acertar zera a conta: refazer uma foto que sumiu não pode gastar
    as tentativas do post."""
    from app.services.memoria import bytes_referencia
    from app.services.midia import foto_da_cena

    plano = ler_plano(item.plano_arte)
    if plano is not None:
        return await _gerar_arte(session, item, plano, automatica, forcar_ia)
    if item.foto_id:
        url = await _foto_da_memoria(session, item)
        if url:
            item.imagem_url = url
            item.detalhe = ""
            await session.commit()
            return ""
        item.foto_id = None
    if not (item.cena or "").strip():
        item.detalhe = "A foto da memória saiu. Escolha outra em Trocar foto."
        await session.commit()
        return item.detalhe
    item.tentativas = (item.tentativas or 0) + 1
    await session.commit()
    try:
        marca = await marca_efetiva(session, item.marca_id)
        referencia = await bytes_referencia(session, marca, item.referencia_id, item.cena)
        item.imagem_url = (await foto_da_cena(item.cena, referencia, marca))[:500]
        item.detalhe = ""
        if automatica:
            item.tentativas = 0
        falha = ""
    except Exception as erro:
        falha = _erro_foto(erro)
        item.detalhe = falha
    await session.commit()
    return falha


def _sem_mais_fotos(item: Agendamento) -> None:
    item.status = "erro"
    item.detalhe = f"A foto não saiu depois de {LIMITE_FOTOS} tentativas. O texto não foi publicado."


async def trocar_foto(session: AsyncSession, item: Agendamento) -> str:
    """Refaz a foto de um post que a IA preencheu (com plano de arte, a IA edita ou cria a foto de novo).
    Devolve o erro, ou '' quando deu certo."""
    tem_plano = ler_plano(item.plano_arte) is not None
    if not ((item.cena or "").strip() or tem_plano) or item.video_url:
        return "Só dá para trocar a foto dos posts que a IA preencheu."
    if item.status not in {"na_fila", "falta_imagem"}:
        return "Esse post não está mais na fila."
    if (item.tentativas or 0) >= LIMITE_FOTOS:
        return f"A foto deste post já foi criada {LIMITE_FOTOS} vezes. Cancele e preencha o dia de novo."
    antes = item.detalhe
    falha = await _gerar_foto(session, item, forcar_ia=tem_plano)
    if falha:
        item.detalhe = antes
        await session.commit()
    elif item.status == "falta_imagem":
        item.status = "na_fila"
        await session.commit()
    return falha


async def tentar_de_novo(session: AsyncSession, item: Agendamento) -> str:
    """Põe um post com erro de volta na fila, com as tentativas zeradas. O que já saiu não sai de novo."""
    if item.status != "erro":
        return "Só dá para tentar de novo um post que deu erro."
    item.status = "na_fila"
    item.detalhe = ""
    item.tentativas = 0
    item.tentativas_publicar = 0
    item.tentativas_marca = 0
    item.reservado_em = None
    await session.commit()
    return ""


async def preparar_fotos(session: AsyncSession) -> None:
    limite = datetime.now(ZONA) + ANTECEDENCIA_FOTO
    resultado = await session.exec(select(Agendamento).where(Agendamento.status == "na_fila"))
    for item in resultado.all():
        if momento(item.dia, item.hora) > limite:
            continue
        if not _foto_automatica(item):
            continue
        if item.imagem_url and not await foto_sumiu(item.imagem_url):
            continue
        if item.rede in REDES_PUBLICAVEIS:
            formato = (item.formato or "feed").strip().lower()
            alvos = alvos_de(item.rede, formato if formato in {"feed", "story", "ambos"} else "feed")
            faltam = await contas_faltando(session, alvos, await marca_efetiva(session, item.marca_id))
            if faltam:
                aviso = " ".join(falta_conta(nome) for nome in faltam)
                if item.detalhe != aviso:
                    item.detalhe = aviso
                    await session.commit()
                continue
        if not item.imagem_url and (item.tentativas or 0) >= LIMITE_FOTOS:
            _sem_mais_fotos(item)
            await session.commit()
            return
        item.imagem_url = ""
        await _gerar_foto(session, item, automatica=True)
        return


def _feitos(item: Agendamento) -> set[str]:
    feitos = {parte for parte in (item.feito or "").split(",") if parte}
    formato = (item.formato or "feed").strip().lower()
    if formato in {"feed", "ambos"}:
        if item.ok_instagram:
            feitos.add("Instagram:feed")
        if item.ok_facebook:
            feitos.add("Facebook:feed")
    return feitos


TENTATIVAS_PUBLICAR = 3
PRESO_DEPOIS = timedelta(minutes=10)


def _falhou_publicar(item: Agendamento, motivo: str) -> None:
    """Conta uma tentativa de publicar; na terceira o post para com status 'erro'."""
    item.tentativas_publicar = (item.tentativas_publicar or 0) + 1
    if item.tentativas_publicar >= TENTATIVAS_PUBLICAR:
        item.status = "erro"
        saiu = _feitos(item)
        parcial = f" Já saiu em: {', '.join(alvo.replace(':', ' ') for alvo in sorted(saiu))}." if saiu else ""
        item.detalhe = f"Não saiu: {motivo[:180]}{parcial}"[:300]
        return
    item.status = "na_fila"
    item.detalhe = f"{motivo} Tento de novo em instantes."[:300]


async def _publicar_item(session: AsyncSession, item: Agendamento) -> None:
    """Publica um post já reservado como 'publicando' e deixa o status final no item."""
    from app.services.resultados import anotar_publicacao

    formato = (item.formato or "feed").strip().lower()
    if formato not in {"feed", "story", "ambos"}:
        formato = "feed"
    if item.rede not in REDES_PUBLICAVEIS:
        item.status = "manual"
        item.detalhe = "Essa rede ainda não publica sozinha."
        return
    from app.services.memoria import base_do_story

    alvos = alvos_de(item.rede, formato)
    if await _refaz_foto(item):
        item.imagem_url = ""
        if await _gerar_foto(session, item, automatica=True):
            item.status = "na_fila"
            return
    precisa_midia = any(alvo.endswith(":story") or alvo.startswith("Instagram:") for alvo in alvos)
    if precisa_midia and not item.imagem_url and not item.video_url:
        if (item.cena or "").strip() or ler_plano(item.plano_arte):
            item.status = "na_fila"
            return
        item.status = "falta_imagem"
        item.detalhe = "Falta a foto."
        return
    feitos = _feitos(item)
    marca = await marca_efetiva(session, item.marca_id)
    site, marca_dagua = await dados_marca(session, marca)
    story_base = await base_do_story(session, item.foto_id)
    texto = com_site(texto_do_post(item.titulo, item.legenda), site)
    pendentes = [alvo for alvo in alvos if alvo not in feitos]
    faltam = await contas_faltando(session, pendentes, marca)
    avisos_conta = [falta_conta(nome) for nome in faltam]
    prontos = [alvo for alvo in pendentes if alvo.split(":", 1)[0] not in faltam]
    if not prontos:
        item.status = "publicado" if not pendentes else "na_fila"
        item.detalhe = " ".join(avisos_conta)[:300]
        return
    try:
        foto_feed, foto_story = await fotos_com_marca(
            prontos,
            item.imagem_url,
            item.video_url,
            marca_dagua,
            item.gancho or "",
            story_base,
            marca,
            ler_plano(item.plano_arte),
        )
    except Exception as erro:
        item.tentativas_marca = (item.tentativas_marca or 0) + 1
        if item.tentativas_marca >= 3:
            item.status = "erro"
            item.detalhe = "A marca d'água não entrou na foto depois de 3 tentativas. O post não saiu sem ela."
        else:
            item.status = "na_fila"
            item.detalhe = (erro_marca(erro) + " Tento de novo em instantes.")[:300]
        return
    erros: list[str] = []
    for alvo in prontos:
        nome = alvo.split(":", 1)[0]
        conta = await _conta(session, nome.lower(), marca)
        if conta is None or not conta.token:
            avisos_conta.append(falta_conta(nome))
            continue
        try:
            externo_id, publicado_como = await asyncio.to_thread(
                _publicar_alvo,
                alvo,
                conta,
                texto,
                foto_feed,
                item.video_url,
                foto_story,
            )
        except Exception as erro:
            erros.append(_erro_foto(erro)[:180])
            continue
        feitos.add(alvo)
        item.feito = ",".join(sorted(feitos))[:80]
        if alvo == "Instagram:feed":
            item.ok_instagram = True
        if alvo == "Facebook:feed":
            item.ok_facebook = True
        await session.commit()
        await anotar_publicacao(
            marca, nome, publicado_como, externo_id, conta.token, texto,
            _foto_do_alvo(alvo, foto_feed, foto_story, item.imagem_url, item.video_url),
            item.tema or "", item.id, pagina_id=conta.externo_id,
        )
    if all(alvo in feitos for alvo in alvos):
        item.status = "publicado"
        item.detalhe = ""
        return
    if erros:
        _falhou_publicar(item, " ".join(erros + avisos_conta))
        return
    item.status = "na_fila"
    item.detalhe = " ".join(avisos_conta)[:300]


async def publicar_um(session: AsyncSession, ident: int) -> bool:
    """Reserva o post (na_fila -> publicando) numa única UPDATE e publica. False se outro já pegou."""
    reserva = await session.exec(
        update(Agendamento)
        .where(Agendamento.id == ident, Agendamento.status == "na_fila")
        .values(status="publicando", reservado_em=agora())
        .returning(Agendamento.id)
    )
    pegou = reserva.first() is not None
    await session.commit()
    if not pegou:
        return False
    item = await session.get(Agendamento, ident, populate_existing=True)
    if item is None:
        return False
    try:
        await _publicar_item(session, item)
    except Exception as erro:
        await session.rollback()
        item = await session.get(Agendamento, ident, populate_existing=True)
        if item is None:
            return True
        _falhou_publicar(item, _erro_foto(erro)[:180])
    if item.status == "publicando":
        item.status = "na_fila"
    item.reservado_em = None
    await session.commit()
    return True


async def publicar_vencidos(session: AsyncSession) -> None:
    agora_local = datetime.now(ZONA)
    resultado = await session.exec(select(Agendamento).where(Agendamento.status == "na_fila"))
    vencidos = sorted(
        (momento(item.dia, item.hora), item.id)
        for item in resultado.all()
        if item.id and momento(item.dia, item.hora) <= agora_local
    )
    for _quando, ident in vencidos:
        await publicar_um(session, ident)


async def soltar_presos(session: AsyncSession) -> int:
    """Devolve à fila o que ficou 'publicando' há mais de 10 minutos (o servidor caiu no meio)."""
    limite = agora() - PRESO_DEPOIS
    resultado = await session.exec(
        update(Agendamento)
        .where(
            Agendamento.status == "publicando",
            or_(Agendamento.reservado_em.is_(None), Agendamento.reservado_em < limite),
        )
        .values(status="na_fila", reservado_em=None)
    )
    await session.commit()
    return resultado.rowcount or 0
