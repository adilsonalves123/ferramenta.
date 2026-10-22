import asyncio
import base64
import uuid
from pathlib import Path

from app.schemas import PedidoCampanha
from app.services import arte
from app.services.imagens import guardar_figura
from app.services.openai_campanha import cliente_openai

PASTA_MIDIA = Path(__file__).resolve().parent.parent.parent / "midia"


def tipo_da_foto(dados: bytes) -> str | None:
    if dados.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if dados.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(dados) > 12 and dados.startswith(b"RIFF") and dados[8:12] == b"WEBP":
        return "image/webp"
    return None


async def guardar_foto_post(dados: bytes, marca_id: int = 0, origem: str = "") -> str:
    """Converte para o JPEG que o Instagram aceita e guarda no banco. Devolve /img/{id}.jpg."""
    figura = await asyncio.to_thread(lambda: arte.instagram(arte.abrir(dados)))
    return await guardar_figura(figura, marca_id, origem)


def salvar_video(dados: bytes) -> str:
    ident, pasta = nova_pasta()
    (pasta / "anexo.mp4").write_bytes(dados)
    return f"/midia/{ident}/anexo.mp4"


async def anexar(dados: bytes, marca_id: int = 0) -> tuple[str, str]:
    if len(dados) > 12 and dados[4:8] == b"ftyp":
        if len(dados) > 25_000_000:
            raise ValueError("O vídeo precisa ter até 25 MB.")
        return await asyncio.to_thread(salvar_video, dados), "video"
    if dados.startswith(b"\x1a\x45\xdf\xa3"):
        raise ValueError("Envie o vídeo em MP4.")
    if tipo_da_foto(dados) is None:
        raise ValueError("Envie uma foto JPG, PNG ou WebP, ou um vídeo MP4.")
    if len(dados) > 8_000_000:
        raise ValueError("A foto precisa ter até 8 MB. O vídeo, até 25 MB.")
    return await guardar_foto_post(dados, marca_id, "anexo"), "foto"


def nova_pasta() -> tuple[str, Path]:
    PASTA_MIDIA.mkdir(parents=True, exist_ok=True)
    ident = uuid.uuid4().hex
    pasta = PASTA_MIDIA / ident
    pasta.mkdir()
    return ident, pasta


def _aviso(rotulo: str, erro: Exception) -> str:
    detalhe = str(erro).replace("\n", " ")
    if "insufficient_quota" in detalhe or "credit_balance_exhausted" in detalhe:
        return f"{rotulo}: a conta da OpenAI está sem créditos."
    return f"{rotulo}: {detalhe[:160]}"


def _prompt_foto(pedido: PedidoCampanha, tem_referencia: bool) -> str:
    nicho = pedido.nicho.strip()
    produto = pedido.produto.strip()
    brief = pedido.brief_foto.strip()
    if brief:
        cena = (
            "Fotografia realista para post de rede social, luz natural do lugar. "
            f"{brief} "
            "Sem texto legível, sem letras, sem números, sem logotipo e sem marca d'água. "
            "Não desenhe celular, tablet, computador, tela, ícone, botão nem interface. "
            "Se o texto pedir aparelho ou tela, deixe o aparelho fora da foto."
        )
        if tem_referencia:
            return (
                "Use a foto enviada como o lugar real da marca. Conserve o ambiente, as cores, os objetos e o estilo. "
                + cena
            )
        return cena
    if tem_referencia:
        return (
            "Use a foto enviada como a cena real. Conserve o produto, os objetos, as cores e o lugar. "
            "Ajuste só a luz e o enquadramento para um post quadrado. "
            "Não troque o produto, não invente pessoa, não adicione texto, letra, logotipo nem marca d'água. "
            f"Negócio: {nicho}. O que deve continuar em destaque: {produto}."
        )
    return (
        "Fotografia realista, luz natural, câmera perto do assunto principal. "
        f"Ambiente de {nicho}, com o fundo levemente desfocado. "
        f"Assunto único: {produto}. "
        "Sem pessoas, sem mãos, sem texto, sem letras, sem logotipo, sem marca d'água."
    )


def _referencia(pedido: PedidoCampanha) -> tuple[bytes | None, str | None]:
    bruto = pedido.foto_base64.strip()
    if not bruto:
        return None, None
    if bruto.startswith("data:") and "," in bruto:
        bruto = bruto.split(",", 1)[1]
    try:
        dados = base64.b64decode(bruto, validate=True)
    except Exception as erro:
        raise ValueError("A foto enviada não pôde ser lida. Use JPG, PNG ou WebP.") from erro
    if not dados or len(dados) > 4_000_000:
        raise ValueError("A foto precisa ter até 4 MB.")
    tipo = tipo_da_foto(dados)
    if tipo is None:
        raise ValueError("Envie a foto em JPG, PNG ou WebP.")
    return dados, tipo


async def _foto(
    prompt: str,
    referencia: bytes | None,
    tipo: str | None,
    modelo: str,
    qualidade: str,
) -> bytes:
    """Pede a foto à OpenAI e devolve os bytes (PNG)."""
    cliente = cliente_openai().with_options(timeout=180)
    pedido = {
        "model": modelo,
        "prompt": prompt,
        "size": "1024x1024",
        "quality": qualidade,
        "output_format": "png",
    }
    if referencia:
        nome = "referencia.jpg" if tipo == "image/jpeg" else "referencia.webp" if tipo == "image/webp" else "referencia.png"
        extra = {"input_fidelity": "high"} if modelo in ("gpt-image-1", "gpt-image-1.5") else {}
        imagem = await cliente.images.edit(
            image=(nome, referencia, tipo or "image/png"),
            **extra,
            **pedido,
        )
    else:
        imagem = await cliente.images.generate(**pedido)
    conteudo = imagem.data[0].b64_json if imagem.data else None
    if not conteudo:
        raise ValueError("A foto não veio.")
    return base64.b64decode(conteudo)


def _modelo() -> str:
    from app.config import get_settings

    return get_settings().openai_image_model.strip() or "gpt-image-2.5-flare"


def _arquivo_imagem(dados: bytes, nome: str) -> tuple[str, bytes, str]:
    tipo = tipo_da_foto(dados) or "image/png"
    extensao = {"image/jpeg": "jpg", "image/webp": "webp"}.get(tipo, "png")
    return (f"{nome}.{extensao}", dados, tipo)


async def editar_com_ia(prompt: str, imagens: list[bytes], qualidade: str = "medium") -> tuple[bytes, int]:
    """Edita/cria a foto com o modelo de imagem a partir de 1 ou 2 fotos (a 1ª é a base).
    Se o modelo não aceitar 2 fotos, tenta só com a base. Devolve (PNG, quantas fotos foram usadas)."""
    modelo = _modelo()
    cliente = cliente_openai().with_options(timeout=180)
    extra = {"input_fidelity": "high"} if modelo in ("gpt-image-1", "gpt-image-1.5") else {}
    pedido = {"model": modelo, "prompt": prompt, "size": "1024x1024", "quality": qualidade, "output_format": "png", **extra}
    arquivos = [_arquivo_imagem(dados, f"foto{numero}") for numero, dados in enumerate(imagens[:2], start=1)]
    try:
        resposta = await cliente.images.edit(image=arquivos if len(arquivos) > 1 else arquivos[0], **pedido)
        usadas = len(arquivos)
    except Exception as erro:
        texto = str(erro)
        if len(arquivos) < 2 or "insufficient_quota" in texto or "credit_balance" in texto:
            raise
        resposta = await cliente.images.edit(image=arquivos[0], **pedido)
        usadas = 1
    conteudo = resposta.data[0].b64_json if resposta.data else None
    if not conteudo:
        raise ValueError("A foto não veio.")
    return base64.b64decode(conteudo), usadas


def tamanho_para(largura: int, altura: int, modelo: str) -> str:
    """Tamanho de saída com a mesma proporção da foto base (a edição não pode reenquadrar a cena)."""
    if largura <= 0 or altura <= 0:
        return "1024x1024"
    proporcao = min(max(largura / altura, 1 / 3), 3)
    if not modelo.startswith("gpt-image-2"):
        if proporcao > 1.2:
            return "1536x1024"
        if proporcao < 1 / 1.2:
            return "1024x1536"
        return "1024x1024"
    area = 1024 * 1024
    nova_largura = max(16, round((area * proporcao) ** 0.5 / 16) * 16)
    nova_altura = max(16, round((area / proporcao) ** 0.5 / 16) * 16)
    return f"{nova_largura}x{nova_altura}"


async def editar_varias(prompt: str, imagens: list[bytes], tamanho: str = "1024x1024", qualidade: str = "medium") -> bytes:
    """Edita a 1ª foto usando as outras como material (até 16 fotos nos modelos GPT Image).
    Não descarta as fotos extras: se o modelo recusar, o erro sobe para quem chamou decidir."""
    modelo = _modelo()
    cliente = cliente_openai().with_options(timeout=240)
    extra = {"input_fidelity": "high"} if modelo in ("gpt-image-1", "gpt-image-1.5") else {}
    arquivos = [_arquivo_imagem(dados, f"foto{numero}") for numero, dados in enumerate(imagens[:16], start=1)]
    resposta = await cliente.images.edit(
        image=arquivos if len(arquivos) > 1 else arquivos[0],
        model=modelo,
        prompt=prompt,
        size=tamanho,
        quality=qualidade,
        output_format="png",
        **extra,
    )
    conteudo = resposta.data[0].b64_json if resposta.data else None
    if not conteudo:
        raise ValueError("A foto não veio.")
    return base64.b64decode(conteudo)


async def gerar_com_ia(prompt: str, qualidade: str = "medium") -> bytes:
    return await _foto(prompt, None, None, _modelo(), qualidade)


async def montar_midia(pedido: PedidoCampanha, marca_id: int = 0) -> dict[str, str | None]:
    aviso: str | None = None
    url: str | None = None
    try:
        referencia, tipo = _referencia(pedido)
        dados = await _foto(_prompt_foto(pedido, referencia is not None), referencia, tipo, _modelo(), "medium")
        url = await guardar_foto_post(dados, marca_id, "ia")
    except Exception as erro:
        aviso = _aviso("A foto não saiu", erro)
    return {
        "foto_url": url,
        "audio_url": None,
        "video_url": None,
        "aviso_midia": aviso,
    }


async def foto_da_cena(cena: str, referencia: bytes | None = None, marca_id: int = 0) -> str:
    """Cria a foto da cena com IA. Com 'referencia' (foto real da marca), mantém o lugar e o estilo."""
    pedido = PedidoCampanha(
        nicho="pequeno negócio local",
        produto=(cena.strip()[:200] or "o dia a dia do negócio"),
        acao="foto",
        brief_foto=cena.strip()[:800],
    )
    tipo = tipo_da_foto(referencia) if referencia else None
    if tipo is None:
        referencia = None
    try:
        dados = await _foto(_prompt_foto(pedido, referencia is not None), referencia, tipo, _modelo(), "medium")
    except Exception as erro:
        raise RuntimeError(_aviso("A foto não saiu", erro)) from erro
    return await guardar_foto_post(dados, marca_id, "ia")
