import ipaddress
import re
import socket
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, build_opener

from urllib.request import HTTPRedirectHandler


class _Redirecionou(Exception):
    def __init__(self, url: str) -> None:
        self.url = url


class _SemRedirecionar(HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        raise _Redirecionou(headers.get("Location") or "")

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _ip_interno(valor: str) -> bool:
    try:
        endereco = ipaddress.ip_address(valor)
    except ValueError:
        return True
    return (
        endereco.is_private
        or endereco.is_loopback
        or endereco.is_link_local
        or endereco.is_reserved
        or endereco.is_multicast
        or endereco.is_unspecified
    )


def _endereco_publico(url: str) -> str:
    partes = urlparse(url.strip())
    if partes.scheme not in {"http", "https"} or not partes.hostname:
        raise ValueError("Cole um link que comece com https://.")
    if partes.username or partes.password:
        raise ValueError("Cole só o endereço da página, sem usuário e senha.")
    try:
        infos = socket.getaddrinfo(partes.hostname, None)
    except socket.gaierror as erro:
        raise ValueError("Não encontrei esse endereço.") from erro
    if not infos or any(_ip_interno(info[4][0]) for info in infos):
        raise ValueError("Esse link não abre daqui.")
    return url


def extrair_texto(html: str) -> str:
    sem_bloco = re.sub(r"(?is)<(script|style|noscript|svg|template).*?>.*?</\1>", " ", html)
    titulo = ""
    achou_titulo = re.search(r"(?is)<title[^>]*>(.*?)</title>", sem_bloco)
    if achou_titulo:
        titulo = unescape(re.sub(r"(?is)<[^>]+>", " ", achou_titulo.group(1)))
    descricoes: list[str] = []
    for meta in re.findall(r"(?is)<meta\b[^>]*>", sem_bloco):
        if not re.search(r"(?is)(?:name|property)\s*=\s*['\"](?:description|og:description|og:title)['\"]", meta):
            continue
        conteudo = re.search(r"(?is)\bcontent\s*=\s*['\"](.*?)['\"]", meta)
        if conteudo:
            descricoes.append(unescape(conteudo.group(1)))
    visivel = unescape(re.sub(r"(?is)<[^>]+>", " ", sem_bloco))
    pedacos = [titulo, *descricoes, visivel]
    texto = re.sub(r"\s+", " ", " ".join(parte.strip() for parte in pedacos if parte.strip())).strip()
    return texto[:6000]


def ler_pagina(url: str) -> str:
    bruto = url.strip()
    if bruto and not re.match(r"^https?://", bruto, re.I):
        bruto = "https://" + bruto
    atual = _endereco_publico(bruto)
    abridor = build_opener(_SemRedirecionar)
    pedido = Request(
        atual,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Ferramenta/1.0)", "Accept": "text/html"},
    )
    for _ in range(4):
        try:
            with abridor.open(pedido, timeout=12) as resposta:
                tipo = (resposta.headers.get("Content-Type") or "").lower()
                if tipo and "html" not in tipo and "text/" not in tipo:
                    raise ValueError("Esse link não é uma página. Cole o endereço do app ou do site.")
                dados = resposta.read(400_000)
        except _Redirecionou as erro:
            if not erro.url:
                raise ValueError("A página não abriu.") from erro
            atual = _endereco_publico(urljoin(atual, erro.url))
            pedido = Request(
                atual,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Ferramenta/1.0)", "Accept": "text/html"},
            )
            continue
        except HTTPError as erro:
            raise ValueError("A página não abriu.") from erro
        except URLError as erro:
            raise ValueError("A página não abriu.") from erro
        except TimeoutError as erro:
            raise ValueError("A página demorou demais para abrir.") from erro
        texto = extrair_texto(dados.decode("utf-8", errors="replace"))
        if len(texto) < 40:
            raise ValueError("A página não trouxe texto. Escreva o que o app faz em Produto ou serviço.")
        return texto
    raise ValueError("O link ficou redirecionando.")
