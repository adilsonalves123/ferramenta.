import asyncio
import json
import urllib.error
import urllib.request

from app.config import get_settings
from app.schemas import EmpresaRadar, PedidoRadar, RadarResposta

NOMES_REDE = {
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "facebook": "Facebook",
    "google": "Google Maps",
}


def _bairro(componentes: list) -> str:
    for tipo in ("neighborhood", "sublocality_level_1", "sublocality"):
        for item in componentes:
            if tipo in (item.get("types") or []):
                nome = str(item.get("longText") or "").strip()
                if nome:
                    return nome
    return ""


CAMPOS = ",".join(
    [
        "places.displayName",
        "places.formattedAddress",
        "places.nationalPhoneNumber",
        "places.websiteUri",
        "places.googleMapsUri",
        "places.rating",
        "places.userRatingCount",
        "places.primaryTypeDisplayName",
        "places.addressComponents",
    ]
)

MOTIVOS = {
    "BILLING_DISABLED": "O faturamento do Google Cloud não está ativo. Ative o faturamento no projeto da chave.",
    "SERVICE_DISABLED": "A Places API (New) não está ativada no Google Cloud. Ative ela no projeto da chave.",
    "API_KEY_INVALID": "A chave do Google Maps não é válida. Confira a chave no Render.",
    "API_KEY_EXPIRED": "A chave do Google Maps expirou. Gere uma nova e coloque no Render.",
    "API_KEY_SERVICE_BLOCKED": "A chave do Google Maps não tem permissão para a Places API (New). Libere essa API na restrição da chave.",
    "API_KEY_HTTP_REFERRER_BLOCKED": "A chave do Google Maps está restrita a sites. Tire a restrição de sites da chave.",
    "API_KEY_IP_ADDRESS_BLOCKED": "A chave do Google Maps está restrita por IP e bloqueou o servidor.",
    "RATE_LIMIT_EXCEEDED": "O limite de buscas do Google Maps acabou por agora. Tente de novo mais tarde.",
    "RESOURCE_EXHAUSTED": "O limite de buscas do Google Maps acabou por agora. Tente de novo mais tarde.",
}


def _mensagem_google(codigo: int, texto: str) -> str:
    try:
        erro = json.loads(texto).get("error") or {}
    except (json.JSONDecodeError, AttributeError):
        erro = {}
    motivos = [str(item.get("reason") or "") for item in erro.get("details") or [] if isinstance(item, dict)]
    for motivo in motivos:
        if motivo in MOTIVOS:
            return MOTIVOS[motivo]
    status = str(erro.get("status") or "")
    mensagem = str(erro.get("message") or "").lower()
    if "billing" in mensagem:
        return MOTIVOS["BILLING_DISABLED"]
    if "has not been used" in mensagem or "is disabled" in mensagem:
        return MOTIVOS["SERVICE_DISABLED"]
    if "api key not valid" in mensagem or "api key expired" in mensagem:
        return MOTIVOS["API_KEY_INVALID"]
    if codigo == 429 or status == "RESOURCE_EXHAUSTED":
        return MOTIVOS["RESOURCE_EXHAUSTED"]
    if codigo == 403 or status == "PERMISSION_DENIED":
        return "O Google recusou a chave. Confira se ela está liberada para a Places API (New)."
    if codigo == 400:
        return "O Google não entendeu a busca. Mude a cidade ou o que buscar e tente de novo."
    return "O Google Maps não completou a busca agora. Tente de novo em alguns minutos."


def buscar_mapa(consulta: str) -> list[EmpresaRadar]:
    chave = get_settings().google_places_api_key.strip()
    if not chave:
        raise RuntimeError("Falta a chave do Google Maps. Sem ela o radar não inventa nomes.")
    corpo = json.dumps(
        {
            "textQuery": consulta[:200],
            "languageCode": "pt-BR",
            "regionCode": "BR",
            "pageSize": 20,
        }
    ).encode()
    pedido = urllib.request.Request(
        "https://places.googleapis.com/v1/places:searchText",
        data=corpo,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": chave,
            "X-Goog-FieldMask": CAMPOS,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(pedido, timeout=30) as resposta:
            dados = json.loads(resposta.read().decode())
    except urllib.error.HTTPError as erro:
        texto = erro.read().decode(errors="replace")
        raise RuntimeError(_mensagem_google(erro.code, texto)) from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("Não consegui falar com o Google Maps agora. Tente de novo em alguns minutos.") from None
    except json.JSONDecodeError:
        raise RuntimeError("O Google Maps respondeu de um jeito estranho. Tente de novo.") from None
    empresas: list[EmpresaRadar] = []
    for lugar in dados.get("places") or []:
        nome = str((lugar.get("displayName") or {}).get("text") or "").strip()
        if not nome:
            continue
        categoria = str((lugar.get("primaryTypeDisplayName") or {}).get("text") or "Estabelecimento")
        endereco = str(lugar.get("formattedAddress") or "Endereço não informado pelo Google")
        telefone = str(lugar.get("nationalPhoneNumber") or "Sem telefone no Google")
        bairro = _bairro(lugar.get("addressComponents") or []) or "Bairro não informado"
        nota = lugar.get("rating")
        avaliacoes = lugar.get("userRatingCount")
        empresas.append(
            EmpresaRadar(
                nome=nome,
                categoria=categoria,
                bairro=bairro,
                endereco=endereco,
                telefone=telefone,
                porque="Este lugar apareceu no Google Maps para esta busca.",
                site=str(lugar.get("websiteUri") or ""),
                mapa=str(lugar.get("googleMapsUri") or ""),
                nota=float(nota) if isinstance(nota, (int, float)) else None,
                avaliacoes=int(avaliacoes) if isinstance(avaliacoes, int) else None,
            )
        )
    return empresas


async def mapear(pedido: PedidoRadar) -> RadarResposta:
    cidade = pedido.cidade.strip()
    tags = pedido.tags.strip()
    termo = tags if pedido.tipo_busca == "determinada" and len(tags) >= 2 else pedido.categoria.strip()
    if len(termo) < 2:
        termo = "empresas"
    consulta = f"{termo} em {cidade}"
    empresas = await asyncio.to_thread(buscar_mapa, consulta)
    aviso = "Estes lugares vieram do Google Maps."
    if "google" not in pedido.redes:
        aviso = "A busca real é no Google Maps. Instagram, TikTok e Facebook ainda não têm busca de lugares."
    if not empresas:
        aviso = "O Google Maps não encontrou lugares para esta busca."
    return RadarResposta(
        cidade=cidade,
        categoria=termo,
        redes=[NOMES_REDE.get(rede, rede) for rede in pedido.redes],
        tipo_busca="Busca determinada" if pedido.tipo_busca == "determinada" else "Busca geral",
        tags=tags,
        aviso=aviso,
        empresas=empresas,
    )
