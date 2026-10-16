from pydantic import BaseModel

from app.config import get_settings
from app.schemas import PedidoSugestao, SugestaoVideo
from app.services.openai_campanha import cliente_openai

SISTEMA = """Você sugere um vídeo curto em português do Brasil para um pequeno negócio.
A pessoa já disse o título e o que quer. Entregue uma sugestão concreta, pronta para ela aceitar ou ajustar.
mensagem: fale direto com a pessoa, em poucas frases. Diga a cena, leia a fala inteira e diga o que aparece na imagem.
titulo: uma frase curta.
cena: o que a imagem mostra, sem pessoa posando para a câmera.
gancho, problema, solucao e cta: a fala, cada parte em uma ou duas frases.
legenda: o texto curto do post.
Não invente preço, telefone, cidade ou promessa.
Se houver transcrição, siga a ordem das ideias e corrija o português, sem copiar frases inteiras.
Se a pessoa pedir um ajuste, mude a sugestão e diga na mensagem o que mudou."""


class SugestaoTexto(BaseModel):
    mensagem: str
    titulo: str
    cena: str
    gancho: str
    problema: str
    solucao: str
    cta: str
    legenda: str


def _historico(pedido: PedidoSugestao) -> str:
    linhas = [
        f"Título: {pedido.nicho.strip()}",
        f"O que a pessoa quer: {pedido.produto.strip()}",
    ]
    if pedido.origem == "clonador" and pedido.transcricao.strip():
        linhas.append(f"Transcrição do vídeo de referência:\n{pedido.transcricao.strip()}")
    if pedido.mensagens:
        linhas.append("Conversa:")
        for mensagem in pedido.mensagens[-8:]:
            quem = "Pessoa" if mensagem.papel == "pessoa" else "Sugestão anterior"
            linhas.append(f"{quem}: {mensagem.texto.strip()}")
    return "\n".join(linhas)


async def sugerir_video(pedido: PedidoSugestao) -> SugestaoVideo:
    resposta = await cliente_openai().with_options(timeout=60).chat.completions.parse(
        model=get_settings().openai_model,
        messages=[
            {"role": "system", "content": SISTEMA},
            {"role": "user", "content": _historico(pedido)},
        ],
        response_format=SugestaoTexto,
    )
    mensagem = resposta.choices[0].message
    if mensagem.refusal:
        raise ValueError(mensagem.refusal)
    if mensagem.parsed is None or not mensagem.parsed.mensagem.strip():
        raise ValueError("A sugestão não veio.")
    texto = mensagem.parsed
    return SugestaoVideo(**texto.model_dump(), transcricao=pedido.transcricao.strip())
