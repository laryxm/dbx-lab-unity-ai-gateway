"""
MCP server de demonstração para o Unity AI Gateway (dados fictícios).

Expõe tools de negócio genéricas via Streamable HTTP em /mcp usando FastMCP.
É stateless (sem Mcp-Session-Id obrigatório) para operar corretamente atrás do
proxy governado do Unity Catalog.

Exemplo de servidor MCP próprio, hospedado como Databricks App e registrado no
Unity AI Gateway. Dados 100% sintéticos (empresa fictícia). As tools abaixo
(catálogo de produtos, pedidos e status de recurso) são ilustrativas — troque
pelas tools do seu domínio mantendo o mesmo padrão.
"""
from datetime import datetime, timezone

from fastmcp import FastMCP

mcp = FastMCP(name="demo-mcp")


# --- dados sintéticos (empresa fictícia) ----------------------------------
_PRODUTOS = {
    "SKU-1001": {"nome": "Teclado mecânico", "categoria": "perifericos", "estoque": 320, "status": "disponivel"},
    "SKU-1002": {"nome": "Monitor 27\"", "categoria": "monitores", "estoque": 45, "status": "disponivel"},
    "SKU-1003": {"nome": "Webcam 4K", "categoria": "perifericos", "estoque": 0, "status": "esgotado"},
}

_PEDIDOS = {
    "PED-5001": {"sku": "SKU-1001", "qtd": 12, "regiao": "sudeste", "status": "faturado", "alerta": None},
    "PED-5002": {"sku": "SKU-1002", "qtd": 3, "regiao": "sul", "status": "em_separacao", "alerta": "estoque_baixo"},
    "PED-5003": {"sku": "SKU-1003", "qtd": 5, "regiao": "nordeste", "status": "pendente", "alerta": "item_esgotado"},
}


@mcp.tool
def consultar_produto(sku: str) -> dict:
    """
    Retorna o nome, a categoria, o estoque e o status de um produto.
    SKUs disponíveis: SKU-1001, SKU-1002, SKU-1003.
    """
    p = _PRODUTOS.get(sku.upper())
    return {"sku": sku.upper(), **p} if p else {"error": f"produto {sku} não encontrado"}


@mcp.tool
def listar_produtos(categoria: str = "") -> list:
    """
    Lista os produtos do catálogo. Se `categoria` for informada (ex.: 'perifericos'), filtra por ela.
    """
    out = []
    for sku, p in _PRODUTOS.items():
        if not categoria or p["categoria"].lower() == categoria.lower():
            out.append({"sku": sku, **p})
    return out


@mcp.tool
def status_pedido(pedido_id: str) -> dict:
    """
    Consulta o SKU, a quantidade, a região, o status e alertas de um pedido.
    IDs: PED-5001, PED-5002, PED-5003.
    Simula uma tool de negócio ligada a um sistema de gestão de pedidos (ERP).
    """
    o = _PEDIDOS.get(pedido_id.upper())
    return {"pedido": pedido_id.upper(), **o} if o else {"error": f"pedido {pedido_id} não encontrado"}


@mcp.tool
def pedidos_com_alerta(regiao: str = "") -> list:
    """
    Lista os pedidos com alerta ativo. Se `regiao` for informada, filtra por ela.
    Útil pra perguntas tipo 'quais pedidos precisam de atenção?'.
    """
    out = []
    for pid, o in _PEDIDOS.items():
        if o["alerta"] and (not regiao or o["regiao"] == regiao.lower()):
            out.append({"pedido": pid, "regiao": o["regiao"], "status": o["status"], "alerta": o["alerta"]})
    return out


@mcp.tool
def now(timezone_name: str = "UTC") -> str:
    """Retorna a data/hora atual em ISO 8601 (UTC). Útil pra sanity check."""
    return datetime.now(timezone.utc).isoformat()


# Rota de health na raiz: o proxy do Databricks Apps faz health check em "/".
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402


@mcp.custom_route("/", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "mcp": "/mcp"})


# Deixa o FastMCP rodar seu próprio servidor (gerencia o lifespan/session manager
# internamente). uvicorn externo com `app:app` deixava o lifespan pendurado -> 502.
if __name__ == "__main__":
    import os

    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.getenv("DATABRICKS_APP_PORT", "8080")),
        path="/mcp",
        stateless_http=True,
    )
