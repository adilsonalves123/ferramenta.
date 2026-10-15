from app.schemas import EmpresaRadar, PedidoRadar, RadarResposta

EXEMPLOS = (
    ("Ponto da Esquina", "Centro", "Rua das Flores, 120", "(00) 90000-0001"),
    ("Casa do Lote", "Bairro Alto", "Avenida Central, 455", "(00) 90000-0002"),
    ("Estação do Viaduto", "Vila Nova", "Travessa do Mercado, 18", "(00) 90000-0003"),
)


NOMES_REDE = {
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "facebook": "Facebook",
    "google": "Google Maps",
}


def mapear(pedido: PedidoRadar) -> RadarResposta:
    cidade = pedido.cidade.strip()
    tags = pedido.tags.strip()
    if pedido.tipo_busca == "determinada" and len(tags) < 2:
        tags = pedido.categoria.strip()
    categoria = tags if pedido.tipo_busca == "determinada" and tags else "empresas do nicho"
    onde = ", ".join(NOMES_REDE.get(rede, rede) for rede in pedido.redes)
    empresas = [
        EmpresaRadar(
            nome=nome,
            categoria=categoria,
            bairro=bairro,
            endereco=f"{endereco}, {cidade}",
            telefone=telefone,
            porque=f"Exemplo em {onde}, no bairro {bairro}.",
        )
        for nome, bairro, endereco, telefone in EXEMPLOS
    ]
    return RadarResposta(
        cidade=cidade,
        categoria=categoria,
        redes=[NOMES_REDE.get(rede, rede) for rede in pedido.redes],
        tipo_busca="Busca determinada" if pedido.tipo_busca == "determinada" else "Busca geral",
        tags=tags,
        aviso="Estes três nomes são exemplos fictícios para validar a tela. Ainda não é uma busca real na cidade.",
        empresas=empresas,
    )
