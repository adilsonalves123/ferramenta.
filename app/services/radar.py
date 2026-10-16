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


def buscar_mapa(consulta: str) -> list[EmpresaRadar]:
    chave = get_settings().google_places_api_key.strip()
    if not chave:
        raise RuntimeError("Falta a chave do Google Maps. Sem ela o radar não inventa nomes.")
    corpo = json.dumps(
        {
            "textQuery": consulta[:200],
            "languageCode": "pt-BR",
            "regionCode": "BR",
            "pageSize": 8,
        }
    ).encode()
    pedido = urllib.request.Request(
        "https://places.googleapis.com/v1/places:searchText",
        data=corpo,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": chave,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.primaryTypeDisplayName,places.addressComponents",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(pedido, timeout=30) as resposta:
            dados = json.loads(resposta.read().decode())
    except urllib.error.HTTPError as erro:
        texto = erro.read().decode(errors="replace")
        try:
            mensagem = json.loads(texto).get("error", {}).get("message", texto)
        except json.JSONDecodeError:
            mensagem = texto
        raise RuntimeError(f"O Google Maps não completou a busca. {mensagem[:180]}") from erro
    empresas: list[EmpresaRadar] = []
    for lugar in dados.get("places") or []:
        nome = str((lugar.get("displayName") or {}).get("text") or "").strip()
        if not nome:
            continue
        categoria = str((lugar.get("primaryTypeDisplayName") or {}).get("text") or "Estabelecimento")
        endereco = str(lugar.get("formattedAddress") or "Endereço não informado pelo Google")
        telefone = str(lugar.get("nationalPhoneNumber") or "Sem telefone no Google")
        bairro = _bairro(lugar.get("addressComponents") or []) or "Bairro não informado"
        empresas.append(
            EmpresaRadar(
                nome=nome,
                categoria=categoria,
                bairro=bairro,
                endereco=endereco,
                telefone=telefone,
                porque="Este lugar apareceu no Google Maps para esta busca.",
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
