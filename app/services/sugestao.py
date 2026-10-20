from pydantic import BaseModel

from app.config import get_settings
from app.schemas import PedidoSugestao, SugestaoVideo
from app.services.openai_campanha import cliente_openai

SISTEMA_FOTO = """Você planeja um post com foto para um negócio real, em português do Brasil.
A pessoa descreve o negócio e pode mandar o texto lido do site dela. Use só o que ela escreveu e o que está nesse texto.
Não invente preço, depoimento, número de clientes, cidade, telefone ou recurso que não apareça. Valor de exemplo numa imagem ou tela não é preço real. Não copie esses valores para a legenda.
mensagem: descreva a foto e a legenda. Comece com "Na foto eu pretendo". Depois um parágrafo começando com "No post eu pretendo" com o texto da legenda. Não numere as opções dentro da mensagem.
titulo: uma frase curta.
roteiro: a legenda pronta, duas ou três frases, e no final de 5 a 8 hashtags.
cenas: exatamente 1. narracao é a legenda curta. imagem descreve a fotografia real: o que a câmera mostra, o lugar e a luz. Se o assunto for digital (site ou serviço on-line), mostre o aparelho em uso com a tela sem palavras. Proibido texto legível, letra, logotipo, interface com palavras e objeto que não combine com o negócio.
legenda: igual ao roteiro.
opcoes: exatamente 4 posts diferentes. titulo curto. detalhe em uma frase. Cada opção muda o assunto com base em um recurso que realmente está na página ou no pedido.
Se a pessoa já escolheu um caminho, mantenha esse caminho, reescreva a foto e a legenda nele e ofereça 4 ajustes."""


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
    if pedido.link.strip():
        linhas.append(f"Link da página: {pedido.link.strip()}")
    if pedido.transcricao.strip():
        linhas.append(f"Texto lido da página:\n{pedido.transcricao.strip()}")
    if pedido.mensagens:
        linhas.append("Conversa:")
        for mensagem in pedido.mensagens[-8:]:
            quem = "Pessoa" if mensagem.papel == "pessoa" else "Sugestão anterior"
            linhas.append(f"{quem}: {mensagem.texto.strip()}")
    return "\n".join(linhas)


def _montar(pedido: PedidoSugestao, texto: SugestaoTexto) -> SugestaoVideo:
    cenas = [cena for cena in texto.cenas if cena.narracao.strip() and cena.imagem.strip()][:1]
    if not cenas:
        raise ValueError("A sugestão não veio completa.")
    return SugestaoVideo(
        mensagem=texto.mensagem.strip(),
        titulo=texto.titulo.strip() or pedido.nicho.strip(),
        cena=cenas[0].imagem.strip()[:800],
        legenda=texto.legenda.strip(),
        transcricao=pedido.transcricao.strip(),
        roteiro=(texto.roteiro.strip() or cenas[0].narracao.strip())[:2500],
        cenas=[{"narracao": cena.narracao.strip()[:800], "imagem": cena.imagem.strip()[:800]} for cena in cenas],
        opcoes=[
            {"titulo": opcao.titulo.strip()[:80], "detalhe": opcao.detalhe.strip()[:240]}
            for opcao in texto.opcoes
            if len(opcao.titulo.strip()) >= 2
        ][:4],
    )


async def _pedir(pedido: PedidoSugestao) -> SugestaoTexto:
    resposta = await cliente_openai().with_options(timeout=60).chat.completions.parse(
        model=get_settings().openai_model,
        messages=[
            {"role": "system", "content": SISTEMA_FOTO},
            {"role": "user", "content": _historico(pedido)},
        ],
        response_format=SugestaoTexto,
    )
    mensagem = resposta.choices[0].message
    if mensagem.refusal:
        raise ValueError(mensagem.refusal)
    if mensagem.parsed is None or not mensagem.parsed.mensagem.strip():
        raise ValueError("A sugestão não veio.")
    return mensagem.parsed


async def sugerir_video(pedido: PedidoSugestao) -> SugestaoVideo:
    return _montar(pedido, await _pedir(pedido))
