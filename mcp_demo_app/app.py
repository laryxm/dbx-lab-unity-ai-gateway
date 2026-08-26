"""
MCP server de demonstração para o Unity AI Gateway — contexto de mineração (fictício).

Expõe tools de negócio via Streamable HTTP em /mcp usando FastMCP.
É stateless (sem Mcp-Session-Id obrigatório) para operar corretamente atrás do
proxy governado do Unity Catalog.

Exemplo de servidor MCP próprio, hospedado como Databricks App e registrado no
Unity AI Gateway. Dados 100% sintéticos (empresa fictícia).
"""
from datetime import datetime, timezone

from fastmcp import FastMCP

mcp = FastMCP(name="demo-mcp-larissa")


# --- dados sintéticos (empresa de mineração fictícia) ---------------------
_MINAS = {
    "MINA-NORTE": {"minerio": "cobre", "producao_ton_dia": 4200, "teor_pct": 1.85, "status": "operando"},
    "MINA-SUL": {"minerio": "cobre", "producao_ton_dia": 3100, "teor_pct": 2.10, "status": "operando"},
    "MINA-LESTE": {"minerio": "ouro", "producao_ton_dia": 180, "teor_pct": 0.04, "status": "manutencao"},
}

_EQUIPAMENTOS = {
    "CAM-114": {"tipo": "caminhao_fora_estrada", "mina": "MINA-NORTE", "saude": 0.92, "alerta": None},
    "PER-007": {"tipo": "perfuratriz", "mina": "MINA-SUL", "saude": 0.61, "alerta": "vibracao_acima_do_limite"},
    "MOI-003": {"tipo": "moinho_SAG", "mina": "MINA-NORTE", "saude": 0.48, "alerta": "temperatura_alta_no_mancal"},
}


@mcp.tool
def producao_mina(mina_id: str) -> dict:
    """
    Retorna a produção diária, o minério, o teor e o status operacional de uma mina.
    IDs disponíveis: MINA-NORTE, MINA-SUL, MINA-LESTE.
    """
    m = _MINAS.get(mina_id.upper())
    return {"mina": mina_id.upper(), **m} if m else {"error": f"mina {mina_id} não encontrada"}


@mcp.tool
def listar_minas(minerio: str = "") -> list:
    """
    Lista as minas operadas. Se `minerio` for informado (ex.: 'cobre'), filtra por ele.
    """
    out = []
    for mid, m in _MINAS.items():
        if not minerio or m["minerio"].lower() == minerio.lower():
            out.append({"mina": mid, **m})
    return out


@mcp.tool
def status_equipamento(equipamento_id: str) -> dict:
    """
    Consulta a saúde (0-1) e alertas de um equipamento. IDs: CAM-114, PER-007, MOI-003.
    Simula uma tool de negócio ligada a um sistema de manutenção (APM).
    """
    e = _EQUIPAMENTOS.get(equipamento_id.upper())
    return {"equipamento": equipamento_id.upper(), **e} if e else {"error": f"equipamento {equipamento_id} não encontrado"}


@mcp.tool
def alertas_ativos(mina_id: str = "") -> list:
    """
    Lista os equipamentos com alerta ativo. Se `mina_id` for informado, filtra por mina.
    Útil pra perguntas tipo 'quais equipamentos precisam de atenção?'.
    """
    out = []
    for eid, e in _EQUIPAMENTOS.items():
        if e["alerta"] and (not mina_id or e["mina"] == mina_id.upper()):
            out.append({"equipamento": eid, "mina": e["mina"], "saude": e["saude"], "alerta": e["alerta"]})
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
