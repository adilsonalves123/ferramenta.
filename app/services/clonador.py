from app.config import get_settings
from app.schemas import ClonagemGerada, PedidoClonador
from app.services.openai_campanha import cliente_openai

SISTEMA = """Você reescreve roteiros de vídeo em português do Brasil para pequenos negócios.
Você recebe um link e, às vezes, o nome de um arquivo. Você não assistiu esse vídeo e não tem a transcrição real.
Simule que chegou uma transcrição curta de um vídeo que segurou a atenção: gancho nos dois primeiros segundos, uma pergunta aberta, um problema concreto, a virada e um pedido de ação.
Entregue um roteiro NOVO, adaptado só ao nicho e ao produto informados.
Use estes gatilhos de retenção, um em cada parte: corte seco no gancho, loop aberto na retencao, detalhe específico no desenvolvimento, recompensa e pedido claro no cta.
Não invente preço, telefone, cidade, avaliação nem promessa que não dê para cumprir.
Não diga que assistiu o arquivo. referencia: repita o link ou, se não houver link, o nome do arquivo."""


async def clonar_video(pedido: PedidoClonador) -> ClonagemGerada:
    link = pedido.link_video.strip()
    arquivo = pedido.nome_arquivo.strip()
    referencia = link or arquivo
    resposta = await cliente_openai().chat.completions.parse(
        model=get_settings().openai_model,
        messages=[
            {"role": "system", "content": SISTEMA},
            {
                "role": "user",
                "content": (
                    f"Nicho: {pedido.nicho.strip()}\n"
                    f"Produto ou serviço: {pedido.produto.strip()}\n"
                    f"Link: {link or 'não informado'}\n"
                    f"Arquivo: {arquivo or 'não enviado'}"
                ),
            },
        ],
        response_format=ClonagemGerada,
    )
    mensagem = resposta.choices[0].message
    if mensagem.refusal:
        raise ValueError(mensagem.refusal)
    if mensagem.parsed is None:
        raise ValueError("A IA não devolveu o roteiro no formato esperado.")
    gerada = mensagem.parsed
    if not gerada.referencia.strip():
        gerada.referencia = referencia
    return gerada
