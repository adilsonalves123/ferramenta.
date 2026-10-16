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

SISTEMA_FOTO = """Você planeja um post com foto para um negócio real, em português do Brasil.
A pessoa descreve o negócio e pode mandar o texto lido do app ou do site. Use só o que ela escreveu e o que está nesse texto.
Não invente preço, depoimento, número de clientes, cidade, telefone ou recurso que não apareça. Preço de item dentro de uma tela de exemplo não é o preço do app. Não copie esses valores para a legenda.
mensagem: descreva a foto e a legenda. Comece com "Na foto eu pretendo". Depois um parágrafo começando com "No post eu pretendo" com o texto da legenda. Não numere as opções dentro da mensagem.
titulo: uma frase curta.
roteiro: a legenda pronta, duas ou três frases, e no final de 5 a 8 hashtags.
cenas: exatamente 1. narracao é a legenda curta. imagem descreve a fotografia real: o que a câmera mostra, o lugar e a luz. Se o assunto for um aplicativo, mostre o celular ou o tablet na mão de quem trabalha, ou sobre o balcão, com a tela sem palavras. Proibido texto legível, letra, logotipo, interface com palavras e comida inventada quando o negócio não for de comida.
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
    if pedido.origem == "clonador" and pedido.transcricao.strip():
        linhas.append(f"Transcrição do vídeo de referência:\n{pedido.transcricao.strip()}")
    if pedido.origem == "foto":
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


def _montar(pedido: PedidoSugestao, texto: SugestaoTexto, minimo_cenas: int) -> SugestaoVideo:
    cenas = [cena for cena in texto.cenas if cena.narracao.strip() and cena.imagem.strip()][:3]
    if len(cenas) < minimo_cenas:
        raise ValueError("A sugestão não veio completa.")
    if minimo_cenas == 1:
        cenas = cenas[:1]
    partes = [cena.narracao.strip() for cena in cenas]
    while len(partes) < 4:
        partes.append(partes[-1])
    return SugestaoVideo(
        mensagem=texto.mensagem.strip(),
        titulo=texto.titulo.strip() or pedido.nicho.strip(),
        cena=cenas[0].imagem.strip()[:800],
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


async def _pedir(pedido: PedidoSugestao, sistema: str) -> SugestaoTexto:
    resposta = await cliente_openai().with_options(timeout=60).chat.completions.parse(
        model=get_settings().openai_model,
        messages=[
            {"role": "system", "content": sistema},
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
    if pedido.origem == "foto":
        return _montar(pedido, await _pedir(pedido, SISTEMA_FOTO), 1)
    return _montar(pedido, await _pedir(pedido, SISTEMA), 3)
