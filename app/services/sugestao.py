from pydantic import BaseModel

from app.config import get_settings
from app.schemas import PedidoSugestao, SugestaoVideo
from app.services.openai_campanha import cliente_openai

SISTEMA = """Você escreve o roteiro de um vídeo curto como uma história de um dia de trabalho, em português do Brasil.
A história tem começo, meio e fim: uma pessoa num lugar concreto, fazendo uma tarefa, o aperto aparece na cena, a virada acontece, e a última cena fecha.
Use só o que a pessoa pediu. Não invente nome de cliente, depoimento, número de vendas, cidade, telefone ou preço.
Não escreva anúncio dividido em gancho, problema, solução e chamada.
mensagem: conte a história inteira, em texto corrido, do jeito que será narrada.
titulo: uma frase curta.
roteiro: o mesmo texto narrado, sem rótulos e sem títulos de cena.
cenas: exatamente 3. narracao é o trecho falado naquela cena. imagem descreve o que a câmera vê, com ação, objeto e luz. Sem texto na imagem e sem alguém olhando para a câmera.
legenda: duas ou três frases para o post.
Se houver transcrição, a história segue os fatos dela e só corrige o português.
Se pedirem um ajuste, reescreva a história e diga o que mudou."""


class CenaTexto(BaseModel):
    narracao: str
    imagem: str


class SugestaoTexto(BaseModel):
    mensagem: str
    titulo: str
    roteiro: str
    cenas: list[CenaTexto]
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
    cenas = [cena for cena in texto.cenas if cena.narracao.strip() and cena.imagem.strip()][:3]
    if len(cenas) < 3:
        raise ValueError("A história não veio com as três cenas.")
    partes = [cena.narracao.strip() for cena in cenas]
    while len(partes) < 4:
        partes.append(partes[-1])
    return SugestaoVideo(
        mensagem=texto.mensagem.strip(),
        titulo=texto.titulo.strip() or pedido.nicho.strip(),
        cena=cenas[0].imagem.strip(),
        gancho=partes[0],
        problema=partes[1],
        solucao=partes[2],
        cta=partes[3],
        legenda=texto.legenda.strip(),
        transcricao=pedido.transcricao.strip(),
        roteiro=(texto.roteiro.strip() or " ".join(partes[:3]))[:2500],
        cenas=[
            {"narracao": cena.narracao.strip()[:800], "imagem": cena.imagem.strip()[:800]}
            for cena in cenas
        ],
    )
