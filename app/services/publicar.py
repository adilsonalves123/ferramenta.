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
from app.models import Agendamento, ContaRede, Ficha, agora

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
            "scope": "pages_show_list,pages_read_engagement,pages_manage_posts",
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


def publicar_instagram_story(conta_id: str, token: str, imagem_url: str) -> None:
    criado = _pedir(
        f"https://graph.instagram.com/{VERSAO}/{conta_id}/media",
        {"media_type": "STORIES", "image_url": imagem_url, "access_token": token},
    )
    criacao = str(criado.get("id") or "")
    if not criacao:
        raise RuntimeError("O Instagram não preparou o Story.")
    _esperar_midia(criacao, token, "o Story")
    _pedir(
        f"https://graph.instagram.com/{VERSAO}/{conta_id}/media_publish",
        {"creation_id": criacao, "access_token": token},
    )


def publicar_instagram_story_video(conta_id: str, token: str, video_url: str) -> None:
    criado = _pedir(
        f"https://graph.instagram.com/{VERSAO}/{conta_id}/media",
        {"media_type": "STORIES", "video_url": video_url, "access_token": token},
    )
    criacao = str(criado.get("id") or "")
    if not criacao:
        raise RuntimeError("O Instagram não preparou o Story.")
    _esperar_midia(criacao, token, "o Story")
    _pedir(
        f"https://graph.instagram.com/{VERSAO}/{conta_id}/media_publish",
        {"creation_id": criacao, "access_token": token},
    )


def publicar_facebook_story(pagina_id: str, token: str, imagem_url: str) -> None:
    foto = _pedir(
        f"https://graph.facebook.com/{VERSAO}/{pagina_id}/photos",
        {"url": imagem_url, "published": "false", "access_token": token},
    )
    photo_id = str(foto.get("id") or "")
    if not photo_id:
        raise RuntimeError("O Facebook não preparou a foto do Story.")
    _pedir(
        f"https://graph.facebook.com/{VERSAO}/{pagina_id}/photo_stories",
        {"photo_id": photo_id, "access_token": token},
    )


def alvos_de(rede: str, formato: str) -> list[str]:
    redes = ["Instagram", "Facebook"] if rede == "Ambos" else [rede]
    formatos = ["feed", "story"] if formato == "ambos" else [formato]
    return [f"{nome}:{tipo}" for nome in redes for tipo in formatos]


def _publicar_alvo(
    alvo: str, conta: ContaRede, texto: str, imagem_url: str, video_url: str, foto_story: str = ""
) -> None:
    from app.services.midia import arquivo_story

    rede, formato = alvo.split(":", 1)
    video = video_url.strip()
    imagem = imagem_url.strip()
    if formato == "story":
        if not imagem and not video:
            raise RuntimeError("O Story pede uma foto.")
        if rede == "Facebook":
            if video and not imagem:
                raise RuntimeError("O Story do Facebook sai com foto.")
            publicar_facebook_story(
                conta.externo_id, conta.token, endereco_publico(foto_story or arquivo_story(imagem))
            )
            return
        if video and video.lower().split("?", 1)[0].endswith(".mp4"):
            publicar_instagram_story_video(conta.externo_id, conta.token, endereco_publico(video))
            return
        publicar_instagram_story(conta.externo_id, conta.token, endereco_publico(foto_story or arquivo_story(imagem)))
        return
    _publicar_rede(rede, conta, texto, imagem, video)


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


def _usa_foto(alvo: str, imagem_url: str, video_url: str) -> bool:
    rede, formato = alvo.split(":", 1)
    video = video_url.strip().lower().split("?", 1)[0]
    if not imagem_url.strip():
        return False
    if formato == "story":
        return rede == "Facebook" or not video.endswith(".mp4")
    return not video


def fotos_com_marca(alvos: list[str], imagem_url: str, video_url: str, marca_dagua: str) -> tuple[str, str]:
    """Foto do feed e do Story com a marca d'água. Vídeo segue sem marca d'água."""
    from app.services.midia import foto_com_marca

    feed, story = imagem_url, ""
    if not marca_dagua.strip():
        return feed, story
    usados = [alvo for alvo in alvos if _usa_foto(alvo, imagem_url, video_url)]
    if any(alvo.endswith(":feed") for alvo in usados):
        feed = foto_com_marca(imagem_url, marca_dagua)
    if any(alvo.endswith(":story") for alvo in usados):
        story = foto_com_marca(imagem_url, marca_dagua, story=True)
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
) -> tuple[str, str]:
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
    texto = com_site(f"{titulo.strip()}\n\n{legenda.strip()}".strip(), site)
    try:
        foto_feed, foto_story = await asyncio.to_thread(fotos_com_marca, alvos, imagem_url, video_url, marca_dagua)
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
            await asyncio.to_thread(_publicar_alvo, alvo, conta, texto, foto_feed, video_url, foto_story)
        except Exception as erro:
            erros.append(_erro_foto(erro)[:180])
    if erros:
        return "erro", " ".join(erros)[:300]
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


def _feitos(item: Agendamento) -> set[str]:
    feitos = {parte for parte in (item.feito or "").split(",") if parte}
    formato = (item.formato or "feed").strip().lower()
    if formato in {"feed", "ambos"}:
        if item.ok_instagram:
            feitos.add("Instagram:feed")
        if item.ok_facebook:
            feitos.add("Facebook:feed")
    return feitos


async def _publicar_item(session: AsyncSession, item: Agendamento) -> None:
    formato = (item.formato or "feed").strip().lower()
    if formato not in {"feed", "story", "ambos"}:
        formato = "feed"
    if item.rede not in REDES_PUBLICAVEIS:
        item.status = "manual"
        item.detalhe = "Essa rede ainda não publica sozinha."
        return
    alvos = alvos_de(item.rede, formato)
    precisa_midia = any(alvo.endswith(":story") or alvo.startswith("Instagram:") for alvo in alvos)
    if precisa_midia and not item.imagem_url and not item.video_url:
        if (item.cena or "").strip():
            return
        item.status = "falta_imagem"
        item.detalhe = "Falta a foto."
        return
    feitos = _feitos(item)
    marca = await marca_efetiva(session, item.marca_id)
    site, marca_dagua = await dados_marca(session, marca)
    texto = com_site(f"{item.titulo}\n\n{item.legenda}".strip(), site)
    pendentes = [alvo for alvo in alvos if alvo not in feitos]
    faltam = await contas_faltando(session, pendentes, marca)
    prontos = [alvo for alvo in pendentes if alvo.split(":", 1)[0] not in faltam]
    try:
        foto_feed, foto_story = await asyncio.to_thread(
            fotos_com_marca, prontos, item.imagem_url, item.video_url, marca_dagua
        )
    except Exception as erro:
        item.tentativas_marca = (item.tentativas_marca or 0) + 1
        if item.tentativas_marca >= 3:
            item.status = "erro"
            item.detalhe = "A marca d'água não entrou na foto depois de 3 tentativas. O post não saiu sem ela."
        else:
            item.detalhe = (erro_marca(erro) + " Tento de novo em instantes.")[:300]
        return
    erros: list[str] = []
    for alvo in alvos:
        if alvo in feitos:
            continue
        nome = alvo.split(":", 1)[0]
        conta = await _conta(session, nome.lower(), marca)
        if conta is None or not conta.token:
            erros.append(falta_conta(nome))
            continue
        try:
            await asyncio.to_thread(
                _publicar_alvo,
                alvo,
                conta,
                texto,
                foto_feed,
                item.video_url,
                foto_story,
            )
            feitos.add(alvo)
            item.feito = ",".join(sorted(feitos))[:80]
            if alvo == "Instagram:feed":
                item.ok_instagram = True
            if alvo == "Facebook:feed":
                item.ok_facebook = True
            await session.commit()
        except Exception as erro:
            erros.append(_erro_foto(erro)[:180])
    if all(alvo in feitos for alvo in alvos):
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
        antes = (item.status, item.detalhe, item.feito, item.ok_instagram, item.ok_facebook, item.tentativas_marca)
        await _publicar_item(session, item)
        if (
            item.status,
            item.detalhe,
            item.feito,
            item.ok_instagram,
            item.ok_facebook,
            item.tentativas_marca,
        ) != antes:
            mudou = True
    if mudou:
        await session.commit()
