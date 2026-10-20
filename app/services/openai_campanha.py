from functools import lru_cache

from openai import AsyncOpenAI

from app.config import get_settings
from app.schemas import CampanhaGerada, PedidoCampanha

SISTEMA = """Você escreve campanhas de marketing em português do Brasil para pequenos negócios.
Use só o nicho e o produto informados. Não invente preço, cidade, telefone, avaliação ou promessa que não dê para cumprir.
titulo_anuncio: uma frase curta, pronta para anúncio.
legenda_redes: texto de post e, no final, de 5 a 8 hashtags."""


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
