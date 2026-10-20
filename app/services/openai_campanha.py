import re
from functools import lru_cache

from openai import APIError, AsyncOpenAI

from app.config import get_settings
from app.schemas import CampanhaGerada, PedidoCampanha

SISTEMA = """Você escreve campanhas de marketing em português do Brasil para pequenos negócios.
Use só o nicho e o produto informados. Não invente preço, cidade, telefone, avaliação, horário de funcionamento ou promessa que não dê para cumprir.
titulo_anuncio: uma frase curta, pronta para anúncio.
legenda_redes: texto de post e, no final, de 5 a 8 hashtags."""

SISTEMA_CENA = """Você descreve uma única fotografia realista para acompanhar um post de rede social, em português do Brasil.
Responda só com a descrição da cena, em até 3 frases: o lugar, o que aparece em destaque e a luz.
A foto combina com o negócio da marca. Sem texto escrito, letras, números, logotipo ou tela acesa."""

REGRAS_ALCOOL = """
Regras para bebida alcoólica (CONAR):
- Nunca use o imperativo "beba", "bebam" ou parecido, nem incentive beber em excesso ou rápido.
- A cena não mostra pessoas bebendo, levando copo à boca, nem menores de idade. Pode mostrar a bebida servida, a garrafa ou o copo na mesa.
- Não ligue bebida a direção, esporte, sucesso ou desempenho.
- Se a legenda falar de bebida alcoólica, termine com a frase "Beba com moderação." antes das hashtags."""

CENA_ALCOOL = "\nSe aparecer bebida alcoólica, ninguém está bebendo nem levando o copo à boca, e não aparece menor de idade."

PALAVRAS_ALCOOL = re.compile(
    r"\b(bebidas?|cervejas?|chopps?|chopes?|drinks?|drinques?|cacha[cç]as?|caipirinhas?|caipiroskas?|vinhos?|"
    r"whiske?y|vodkas?|gin|adegas?|long necks?|happy hour|destilados?)\b",
    re.IGNORECASE,
)


def fala_de_alcool(texto: str) -> bool:
    return bool(PALAVRAS_ALCOOL.search(texto or ""))


def com_moderacao(legenda: str) -> str:
    if not fala_de_alcool(legenda) or "beba com modera" in legenda.lower():
        return legenda
    tags = re.search(r"(?:\s*#[^\s#]+)+\s*$", legenda)
    corpo = legenda[: tags.start()] if tags else legenda
    fim = "\n\n" + tags.group(0).strip() if tags else ""
    return corpo.rstrip() + "\n\nBeba com moderação." + fim


@lru_cache
def cliente_openai() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=get_settings().openai_api_key, timeout=60)


async def gerar_campanha(pedido: PedidoCampanha) -> CampanhaGerada:
    alcool = fala_de_alcool(f"{pedido.nicho} {pedido.produto}")
    resposta = await cliente_openai().chat.completions.parse(
        model=get_settings().openai_model,
        messages=[
            {"role": "system", "content": SISTEMA + (REGRAS_ALCOOL if alcool else "")},
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
    if alcool:
        return mensagem.parsed.model_copy(update={"legenda_redes": com_moderacao(mensagem.parsed.legenda_redes)})
    return mensagem.parsed


async def cena_da_legenda(legenda: str, marca: str = "") -> str:
    """Cena curta de foto a partir da legenda. Se a IA não responder, usa um molde simples."""
    contexto = f"Sobre a marca:\n{marca.strip()[:1500]}\n\n" if marca.strip() else ""
    sistema = SISTEMA_CENA + (CENA_ALCOOL if fala_de_alcool(f"{marca} {legenda}") else "")
    try:
        resposta = await cliente_openai().with_options(timeout=40).chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=200,
            messages=[
                {"role": "system", "content": sistema},
                {"role": "user", "content": f"{contexto}Legenda do post:\n{legenda.strip()[:2000]}"},
            ],
        )
        cena = (resposta.choices[0].message.content or "").strip()
    except APIError:
        cena = ""
    if not cena:
        cena = f"Fotografia realista que combina com este post: {legenda.strip()[:300]}"
    return cena[:800]
