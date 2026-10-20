from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.services.exclusao import apagar_contas_meta, criar_codigo, ler_codigo, ler_pedido_assinado

router = APIRouter()

ATUALIZADO = "20 de outubro de 2026"

MOLDE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{titulo} · {produto}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <style>
    :root {{ --fundo: #eef3fb; --papel: #ffffff; --tinta: #1a1d23; --mudo: #5c6b82; --linha: #e3e8f2; --azul: #4f46e5; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, Segoe UI, sans-serif;
      background:
        radial-gradient(900px 420px at 0% 0%, #dbe4ff 0%, transparent 60%),
        radial-gradient(700px 380px at 100% 0%, #ffe7d6 0%, transparent 55%),
        var(--fundo);
      color: var(--tinta);
      font-size: 15px;
      line-height: 1.6;
    }}
    main {{
      max-width: 760px;
      margin: 32px auto 48px;
      padding: 32px;
      background: var(--papel);
      border: 1px solid var(--linha);
      border-top: 3px solid var(--azul);
      border-radius: 16px;
      box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04), 0 12px 32px rgba(16, 24, 40, 0.06);
    }}
    h1 {{ font-size: 26px; letter-spacing: -0.02em; margin: 0 0 4px; }}
    h2 {{ font-size: 17px; margin: 28px 0 6px; }}
    p, li {{ color: #2b3140; }}
    ul {{ padding-left: 20px; }}
    .mudo {{ color: var(--mudo); font-size: 13px; margin: 0 0 20px; }}
    .caixa {{ background: #f6f8fc; border: 1px solid var(--linha); border-radius: 12px; padding: 14px 16px; }}
    a {{ color: var(--azul); font-weight: 600; }}
    code {{ background: #f1f3f9; border-radius: 6px; padding: 2px 6px; font-size: 13px; }}
    nav {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--linha); font-size: 13px; color: var(--mudo); }}
    nav a {{ margin-right: 14px; }}
    @media (max-width: 800px) {{ main {{ margin: 0; border-radius: 0; padding: 22px; }} }}
  </style>
</head>
<body>
  <main>
    <h1>{titulo}</h1>
    <p class="mudo">{produto} · Criação e agendamento de posts para Instagram e Facebook · Atualizado em {atualizado}</p>
    {corpo}
    <nav>
      <a href="/privacidade">Política de Privacidade</a>
      <a href="/termos">Termos de Uso</a>
      <a href="/exclusao-de-dados">Exclusão de dados</a>
      <a href="/">Abrir o {produto}</a>
    </nav>
  </main>
</body>
</html>
"""


def _dados() -> dict[str, str]:
    ajustes = get_settings()
    email = ajustes.contato_email.strip()
    cnpj = ajustes.empresa_cnpj.strip()
    empresa = escape(ajustes.empresa_nome.strip() or "Risca Aí")
    produto = escape(ajustes.produto_nome.strip() or "Risca Aí Posts")
    empresa_cnpj = f"{empresa}, CNPJ {escape(cnpj)}" if cnpj else empresa
    site = escape(ajustes.app_url.rstrip("/"))
    return {
        "empresa": empresa,
        "produto": produto,
        "operador": f"{produto} é um produto de {empresa_cnpj}",
        "email": f'<a href="mailto:{escape(email)}">{escape(email)}</a>' if email else "[SEU E-MAIL]",
        "site": f'<a href="{site}">{site}</a>',
    }


def _pagina(titulo: str, corpo: str, status: int = 200) -> HTMLResponse:
    dados = _dados()
    html = MOLDE.format(
        titulo=escape(titulo),
        produto=dados["produto"],
        atualizado=ATUALIZADO,
        corpo=corpo.format(**dados),
    )
    return HTMLResponse(html, status_code=status)


PRIVACIDADE = """
<p>O {produto} é uma ferramenta para criar e agendar posts no Instagram e em Páginas do Facebook. Esta política
explica quais dados o {produto} acessa quando você liga uma conta do Instagram ou uma Página do Facebook, para
que usa esses dados, como guarda e como você pode apagar tudo.</p>
<p>{operador}. A mesma empresa também oferece o Risca Aí, um sistema de gestão para bares e adegas
(<a href="https://www.riscaai.com.br">www.riscaai.com.br</a>), que é um produto separado.</p>

<h2>1. O que o {produto} faz</h2>
<p>O {produto} ajuda você a escrever posts e a publicar ou agendar no Instagram profissional e na Página do
Facebook que você ligou. Uma publicação só sai quando você clica em <strong>Publicar agora</strong> ou quando chega
o dia e o horário que você mesmo agendou. Quando você pede para a IA preencher um dia, o post aparece na fila,
com a foto, antes do horário, e você pode revisar e cancelar antes de sair. O {produto} nunca publica nada por
conta própria.</p>

<h2>2. Dados do Instagram que acessamos</h2>
<p>Quando você clica em ligar o Instagram, você entra pelo login oficial do Instagram e autoriza as permissões
<code>instagram_business_basic</code> e <code>instagram_business_content_publish</code>. Com isso guardamos:</p>
<ul>
  <li>o ID da sua conta profissional do Instagram e o ID de usuário que o Instagram cria para este app;</li>
  <li>o nome de usuário (@) da conta, para mostrar qual conta está ligada;</li>
  <li>o token de acesso que o Instagram entrega, usado só para publicar os seus posts.</li>
</ul>

<h2>3. Dados do Facebook que acessamos</h2>
<p>Quando você liga o Facebook, você entra pelo Login do Facebook e autoriza <code>pages_show_list</code>,
<code>pages_read_engagement</code> e <code>pages_manage_posts</code>. Com isso guardamos:</p>
<ul>
  <li>o ID de usuário que o Facebook cria para este app;</li>
  <li>a lista das Páginas que você administra (ID e nome), só para você escolher em qual publicar;</li>
  <li>o ID e o nome da Página escolhida;</li>
  <li>o token de acesso do usuário e o token da Página, usados só para publicar os seus posts.</li>
</ul>

<h2>4. O que não acessamos</h2>
<p>Não vemos nem guardamos a sua senha. Não lemos mensagens, comentários, curtidas, seguidores, lista de amigos,
dados de anúncios nem posts antigos. Não usamos os seus dados para anúncios.</p>

<h2>5. Para que usamos os dados</h2>
<ul>
  <li>Mostrar no {produto} qual conta e qual Página estão ligadas.</li>
  <li>Publicar no feed e nos stories os posts (texto, foto ou vídeo) que você criou e mandou publicar ou agendou.</li>
</ul>
<p>Não vendemos, não alugamos e não compartilhamos esses dados. Eles passam apenas pelos serviços que fazem o
{produto} funcionar: a hospedagem do site e o banco de dados. Os textos e imagens podem ser criados com ajuda de
inteligência artificial (OpenAI) a partir do que você escreve; nunca enviamos tokens, IDs ou dados das suas contas
para esse serviço.</p>

<h2>6. Como guardamos</h2>
<p>Os dados trafegam sempre por conexão segura (HTTPS) e ficam num banco de dados com acesso restrito à equipe
responsável pelo {produto}. Os tokens nunca aparecem na tela nem são enviados a outras pessoas.</p>

<h2>7. Por quanto tempo guardamos</h2>
<p>Guardamos os dados da conexão enquanto a conta estiver ligada à marca no {produto}. Apagamos na hora quando:</p>
<ul>
  <li>você apaga a marca dentro do {produto};</li>
  <li>você remove o app nas configurações do Instagram ou do Facebook (a Meta nos avisa automaticamente);</li>
  <li>você pede a exclusão (veja a página <a href="/exclusao-de-dados">Exclusão de dados</a>).</li>
</ul>
<p>Os posts que já foram publicados ficam no seu Instagram ou na sua Página. Você pode apagá-los por lá a
qualquer momento. Os posts agendados ficam na fila até você cancelar.</p>

<h2>8. Como tirar o acesso</h2>
<ul>
  <li><strong>Instagram:</strong> no app, abra Configurações e privacidade › Permissões do site › Apps e sites,
  escolha {produto} e toque em Remover.</li>
  <li><strong>Facebook:</strong> abra Configurações e privacidade › Configurações › Integrações comerciais
  (ou Apps e sites), escolha {produto} e clique em Remover.</li>
</ul>
<p>Depois disso, o {produto} não consegue mais publicar e apaga os dados da conexão.</p>

<h2>9. Seus direitos (LGPD)</h2>
<p>Você pode pedir para ver, corrigir, levar ou apagar os seus dados, conforme a Lei Geral de Proteção de Dados
(Lei 13.709/2018). Basta escrever para {email}. Respondemos em até 15 dias.</p>

<h2>10. Cookies</h2>
<p>O {produto} não usa cookies de publicidade nem de rastreamento.</p>

<h2>11. Mudanças nesta política</h2>
<p>Se esta política mudar, a nova versão aparece nesta página com a data atualizada.</p>

<h2>12. Contato</h2>
<p class="caixa">{operador}<br />E-mail: {email}<br />Site: {site}</p>
"""

TERMOS = """
<p>Estes termos valem para quem usa o {produto}, uma ferramenta para criar e agendar posts no Instagram e em
Páginas do Facebook. {operador}. Ao usar o {produto}, você concorda com estes termos.</p>

<h2>1. O serviço</h2>
<p>O {produto} ajuda a criar textos e imagens para posts e publica, no Instagram profissional e na Página do
Facebook que você ligar, os posts que você mandar publicar ou agendar.</p>

<h2>2. Suas contas</h2>
<ul>
  <li>Você só pode ligar contas do Instagram e Páginas do Facebook que você administra ou que tem autorização
  para administrar.</li>
  <li>Você pode tirar o acesso a qualquer momento, como explica a <a href="/privacidade">Política de Privacidade</a>.</li>
</ul>

<h2>3. O conteúdo é seu</h2>
<p>Você é responsável por tudo o que publica. Revise o texto e a imagem antes de publicar ou agendar. O conteúdo
precisa seguir a lei brasileira, os Termos de Uso e as Diretrizes da Comunidade do Instagram e do Facebook.</p>

<h2>4. O que não é permitido</h2>
<ul>
  <li>Publicar spam, conteúdo enganoso, ilegal, ofensivo ou que viole direitos de outras pessoas.</li>
  <li>Ligar contas de outras pessoas sem autorização.</li>
  <li>Tentar burlar limites das plataformas ou usar o {produto} para automação abusiva.</li>
</ul>

<h2>5. Disponibilidade</h2>
<p>O {produto} depende do Instagram, do Facebook e de outros serviços. Se algum deles ficar fora do ar ou
mudar as regras, uma publicação pode atrasar ou não sair. Avisamos no próprio {produto} quando um post não sair.</p>

<h2>6. Responsabilidade</h2>
<p>Fazemos o possível para o {produto} funcionar bem, mas ele é oferecido como está. Não respondemos por perdas
causadas por falhas das plataformas, por conteúdo publicado por você ou por uso fora destes termos.</p>

<h2>7. Encerramento</h2>
<p>Você pode parar de usar o {produto} quando quiser. Podemos suspender o acesso de quem violar estes termos.</p>

<h2>8. Mudanças</h2>
<p>Se estes termos mudarem, a nova versão aparece nesta página com a data atualizada.</p>

<h2>9. Lei aplicável</h2>
<p>Estes termos seguem as leis do Brasil.</p>

<h2>10. Contato</h2>
<p class="caixa">{operador}<br />E-mail: {email}<br />Site: {site}</p>
"""

EXCLUSAO = """
<p>Você pode apagar a qualquer momento os dados que o {produto} guarda das suas contas do Instagram e do
Facebook. São apagados: os IDs da conta e da Página, o nome de usuário, o nome da Página e os tokens de acesso.</p>

<h2>Jeito 1: remover o app no Instagram ou no Facebook (automático)</h2>
<ul>
  <li><strong>Instagram:</strong> Configurações e privacidade › Permissões do site › Apps e sites › escolha
  {produto} › Remover.</li>
  <li><strong>Facebook:</strong> Configurações e privacidade › Configurações › Integrações comerciais (ou Apps e
  sites) › escolha {produto} › Remover. Se quiser, marque também a opção de apagar a atividade.</li>
</ul>
<p>A Meta nos avisa na hora e os dados da conexão são apagados automaticamente. Se você pedir a exclusão pelo
Facebook, recebe um código de confirmação para acompanhar o pedido.</p>

<h2>Jeito 2: apagar a marca no {produto}</h2>
<p>Ao apagar uma marca dentro do {produto}, as contas do Instagram e do Facebook ligadas a ela são apagadas junto.</p>

<h2>Jeito 3: pedir por e-mail</h2>
<p>Escreva para {email} com o @ do Instagram ou o nome da Página. Apagamos em até 15 dias e respondemos
confirmando.</p>

<p class="caixa">Os posts que já foram publicados ficam no seu Instagram ou na sua Página. Para apagar um post
publicado, apague direto no Instagram ou no Facebook.</p>
"""


@router.get("/privacidade", response_class=HTMLResponse, include_in_schema=False)
async def get_privacidade() -> HTMLResponse:
    return _pagina("Política de Privacidade", PRIVACIDADE)


@router.get("/termos", response_class=HTMLResponse, include_in_schema=False)
async def get_termos() -> HTMLResponse:
    return _pagina("Termos de Uso", TERMOS)


@router.get("/exclusao-de-dados", response_class=HTMLResponse, include_in_schema=False)
async def get_exclusao() -> HTMLResponse:
    return _pagina("Exclusão de dados", EXCLUSAO)


@router.get("/exclusao-de-dados/status", response_class=HTMLResponse, include_in_schema=False)
async def get_status_exclusao(codigo: str = "") -> HTMLResponse:
    lido = ler_codigo(codigo)
    if lido is None:
        corpo = (
            "<p>Não encontramos esse código de confirmação. Confira se copiou o código inteiro "
            "ou escreva para {email}.</p>"
        )
        return _pagina("Pedido de exclusão", corpo, status=404)
    momento, apagadas = lido
    resultado = (
        f"Apagamos {apagadas} registro(s) de conexão ligados a essa conta."
        if apagadas
        else "Não havia nenhum dado dessa conta guardado no {produto}."
    )
    corpo = (
        f'<p class="caixa"><strong>Status: concluído.</strong><br />'
        f"Código: <code>{escape(codigo.strip())}</code><br />"
        f"Pedido recebido em {momento:%d/%m/%Y} às {momento:%H:%M} (horário de Brasília).<br />"
        f"{resultado}</p>"
        "<p>Os posts que já foram publicados continuam no Instagram ou na Página. "
        "Dúvidas: {email}.</p>"
    )
    return _pagina("Pedido de exclusão", corpo)


def _pedido_valido(signed_request: str) -> tuple[list[str], str]:
    lido = ler_pedido_assinado(signed_request)
    if lido is None:
        raise HTTPException(status_code=400, detail="Pedido sem assinatura válida.")
    return lido


@router.post("/exclusao-de-dados/callback")
async def post_exclusao_callback(
    signed_request: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    redes, usuario = _pedido_valido(signed_request)
    apagadas = await apagar_contas_meta(session, redes, usuario)
    codigo = criar_codigo(redes, apagadas)
    url = get_settings().app_url.rstrip("/") + f"/exclusao-de-dados/status?codigo={codigo}"
    return {"url": url, "confirmation_code": codigo}


@router.post("/contas/desautorizar")
async def post_desautorizar(
    signed_request: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    redes, usuario = _pedido_valido(signed_request)
    await apagar_contas_meta(session, redes, usuario)
    return {"ok": True}
