from functools import lru_cache

from openai import APIError, AsyncOpenAI

from app.config import get_settings
from app.schemas import CampanhaGerada, PedidoCampanha

SISTEMA = """Você escreve campanhas de marketing em português do Brasil para pequenos negócios.
Use só o nicho e o produto informados. Não invente preço, cidade, telefone, avaliação ou promessa que não dê para cumprir.
titulo_anuncio: uma frase curta, pronta para anúncio.
legenda_redes: texto de post e, no final, de 5 a 8 hashtags."""

SISTEMA_CENA = """Você descreve uma única fotografia realista para acompanhar um post de rede social, em português do Brasil.
Responda só com a descrição da cena, em até 3 frases: o lugar, o que aparece em destaque e a luz.
A foto combina com o negócio da marca. Sem texto escrito, letras, números, logotipo ou tela acesa."""


@lru_cache
def cliente_openai() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=get_settings().openai_api_key, timeout=60)


async def gerar_campanha(pedido: PedidoCampanha) -> CampanhaGerada:
    resposta = await cliente_openai().chat.completions.parse(
        model=get_settings().openai_model,
        messages=[
            {"role": "system", "content": SISTEMA},
            {
                "role": "user",
                "content": f"Nicho: {pedido.nicho.strip()}\nProduto ou serviço: {pedido.produto.strip()}",
            },
        ],
        response_format=CampanhaGerada,
    )
    mensagem = resposta.choices[0].message
    if mensagem.refusal:
        raise ValueError(mensagem.refusal)
    if mensagem.parsed is None:
        raise ValueError("A IA não devolveu a campanha no formato esperado.")
    return mensagem.parsed


async def cena_da_legenda(legenda: str, marca: str = "") -> str:
    """Cena curta de foto a partir da legenda. Se a IA não responder, usa um molde simples."""
    contexto = f"Sobre a marca:\n{marca.strip()[:1500]}\n\n" if marca.strip() else ""
    try:
        resposta = await cliente_openai().with_options(timeout=40).chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=200,
            messages=[
                {"role": "system", "content": SISTEMA_CENA},
                {"role": "user", "content": f"{contexto}Legenda do post:\n{legenda.strip()[:2000]}"},
            ],
        )
        cena = (resposta.choices[0].message.content or "").strip()
    except APIError:
        cena = ""
    if not cena:
        cena = f"Fotografia realista que combina com este post: {legenda.strip()[:300]}"
    return cena[:800]
