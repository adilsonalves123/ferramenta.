from pydantic import BaseModel

from app.config import get_settings
from app.schemas import PedidoSugestao, SugestaoVideo
from app.services.openai_campanha import cliente_openai

SISTEMA = """Você é um diretor de vídeo curto para um negócio real, em português do Brasil.
A pessoa descreve o que quer. Você devolve o roteiro detalhado e diz o que pretende fazer no vídeo.
A história tem começo, meio e fim, com gente de verdade no lugar de trabalho.
Use só o que a pessoa pediu. Não invente nome de cliente, depoimento, número de vendas, cidade, telefone ou preço.
mensagem: escreva o roteiro cena por cena, com a fala de cada momento. Depois um parágrafo começando com "No vídeo eu pretendo" explicando quem aparece, o que a câmera mostra e como a história anda. Não numere as opções dentro da mensagem.
titulo: uma frase curta.
roteiro: o texto que será narrado, contínuo, sem rótulos.
cenas: exatamente 3. narracao é a fala daquela cena. imagem descreve pessoas reais: idade aproximada, roupa de trabalho, o que as mãos fazem, a luz do lugar. Proibido apresentador sorrindo para a câmera, pele lisa de anúncio, boneco, desenho ou 3D.
legenda: duas ou três frases para o post.
opcoes: exatamente 4 caminhos. titulo curto. detalhe em uma frase. Uma das opções é o dono contracenando com um cliente. As outras três mudam o jeito da história.
Se a pessoa já escolheu um caminho, mantenha esse caminho, reescreva o roteiro nele e ofereça 4 ajustes em cima dele.
Se houver transcrição, a história segue os fatos dela e só corrige o português."""


class CenaTexto(BaseModel):
    narracao: str
    imagem: str


class OpcaoTexto(BaseModel):
    titulo: str
    detalhe: str = ""


class SugestaoTexto(BaseModel):
    mensagem: str
    titulo: str
    roteiro: str
    cenas: list[CenaTexto]
    legenda: str
    opcoes: list[OpcaoTexto]


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
        opcoes=[
            {"titulo": opcao.titulo.strip()[:80], "detalhe": opcao.detalhe.strip()[:240]}
            for opcao in texto.opcoes
            if len(opcao.titulo.strip()) >= 2
        ][:4],
    )
