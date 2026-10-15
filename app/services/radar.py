from app.schemas import EmpresaRadar, PedidoRadar, RadarResposta

EXEMPLOS = (
    ("Ponto da Esquina", "Centro", "Rua das Flores, 120", "(00) 90000-0001"),
    ("Casa do Lote", "Bairro Alto", "Avenida Central, 455", "(00) 90000-0002"),
    ("Estação do Viaduto", "Vila Nova", "Travessa do Mercado, 18", "(00) 90000-0003"),
)


def mapear(pedido: PedidoRadar) -> RadarResposta:
    cidade = pedido.cidade.strip()
    categoria = pedido.categoria.strip()
    empresas = [
        EmpresaRadar(
            nome=nome,
            categoria=categoria,
            bairro=bairro,
            endereco=f"{endereco}, {cidade}",
            telefone=telefone,
            porque=f"Exemplo de {categoria} em {bairro} para ver como o card aparece.",
        )
        for nome, bairro, endereco, telefone in EXEMPLOS
    ]
    return RadarResposta(
        cidade=cidade,
        categoria=categoria,
        aviso="Estes três nomes são exemplos fictícios para validar a tela. Ainda não é uma busca real na cidade.",
        empresas=empresas,
    )
