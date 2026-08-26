# Databricks notebook source
# MAGIC %md
# MAGIC # Criar e hospedar um MCP server como Databricks App
# MAGIC
# MAGIC Este notebook documenta, passo a passo, a construção de um **MCP server próprio** (FastMCP,
# MAGIC Streamable HTTP) hospedado como **Databricks App**, para posterior registro no Unity AI
# MAGIC Gateway (ver o notebook `registrar_mcp_unity_ai_gateway`).
# MAGIC
# MAGIC ## Por que um servidor próprio
# MAGIC Servidores MCP públicos frequentemente apresentam incompatibilidades atrás do proxy governado
# MAGIC do Unity Catalog — por exemplo, rejeição do header `Authorization` (sempre injetado pelo proxy),
# MAGIC exigência de token proprietário, bloqueio de tráfego servidor-a-servidor, ou exigência de sessão
# MAGIC (`Mcp-Session-Id`) não gerenciada pelo gateway. Para uma prova de conceito confiável, um servidor
# MAGIC próprio permite controlar autenticação e comportamento de ponta a ponta.
# MAGIC
# MAGIC ## O que este notebook produz
# MAGIC Um Databricks App servindo Streamable HTTP em `/mcp` com tools de negócio sintéticas de operação
# MAGIC de mineração (`producao_mina`, `listar_minas`, `status_equipamento`, `alertas_ativos`, `now`).
# MAGIC O deploy é feito via SDK a partir do próprio notebook (seções 6 a 9).

# COMMAND ----------

# MAGIC %run "./0.0 Setup - Parâmetros do cliente"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Anatomia do app
# MAGIC Três arquivos numa pasta (`mcp_demo_app/`):
# MAGIC - `app.py` — o servidor MCP (FastMCP)
# MAGIC - `app.yaml` — como o Databricks App inicia o processo
# MAGIC - `requirements.txt` — dependências extras (fastmcp)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. `app.py` — o servidor MCP
# MAGIC Pontos-chave (cada um é um requisito de implantação a observar):
# MAGIC
# MAGIC 1. **`stateless_http=True` vai no `run()`**, NÃO no construtor `FastMCP()`. A API nova rejeita
# MAGIC    `FastMCP(stateless_http=True)` com `TypeError`. Stateless evita exigir `Mcp-Session-Id`
# MAGIC    (que quebrou o GitMCP atrás do gateway).
# MAGIC 2. **Health check na raiz**: o proxy do Databricks Apps faz `GET /`. Sem uma rota lá, o app
# MAGIC    fica 502. Adicionamos via `@mcp.custom_route("/", methods=["GET"])`.
# MAGIC 3. **Usar `mcp.run(...)` num `if __name__=="__main__"`**, e NÃO expor `app` pro uvicorn externo.
# MAGIC    Com `uvicorn app:app` o lifespan/session manager do FastMCP fica pendurado: o app reporta
# MAGIC    "started successfully" mas dá **502 em todas as rotas** (a request nem chega ao processo).
# MAGIC    Deixar o FastMCP rodar o próprio servidor resolve.

# COMMAND ----------

APP_PY = r'''
"""MCP server de demonstração para o Unity AI Gateway (Streamable HTTP em /mcp).
Tools de negócio sintéticas de operação de mineração. Dados 100% fictícios."""
from datetime import datetime, timezone
from fastmcp import FastMCP

mcp = FastMCP(name="demo-mcp-larissa")

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
    """Produção diária, minério, teor e status de uma mina. IDs: MINA-NORTE, MINA-SUL, MINA-LESTE."""
    m = _MINAS.get(mina_id.upper())
    return {"mina": mina_id.upper(), **m} if m else {"error": f"mina {mina_id} não encontrada"}


@mcp.tool
def listar_minas(minerio: str = "") -> list:
    """Lista as minas operadas. Se `minerio` for informado, filtra por ele."""
    return [{"mina": mid, **m} for mid, m in _MINAS.items()
            if not minerio or m["minerio"].lower() == minerio.lower()]


@mcp.tool
def status_equipamento(equipamento_id: str) -> dict:
    """Saúde (0-1) e alertas de um equipamento. IDs: CAM-114, PER-007, MOI-003."""
    e = _EQUIPAMENTOS.get(equipamento_id.upper())
    return {"equipamento": equipamento_id.upper(), **e} if e else {"error": f"equipamento {equipamento_id} não encontrado"}


@mcp.tool
def alertas_ativos(mina_id: str = "") -> list:
    """Equipamentos com alerta ativo. Se `mina_id` for informado, filtra por mina."""
    return [{"equipamento": eid, "mina": e["mina"], "saude": e["saude"], "alerta": e["alerta"]}
            for eid, e in _EQUIPAMENTOS.items()
            if e["alerta"] and (not mina_id or e["mina"] == mina_id.upper())]


@mcp.tool
def now(timezone_name: str = "UTC") -> str:
    """Retorna a data/hora atual em ISO 8601 (UTC)."""
    return datetime.now(timezone.utc).isoformat()


# Ponto de atenção 2: health check na raiz para o proxy do Databricks Apps (senão 502)
from starlette.requests import Request
from starlette.responses import JSONResponse


@mcp.custom_route("/", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "mcp": "/mcp"})


# Ponto de atenção 3: o FastMCP roda o próprio servidor (uvicorn externo -> 502)
if __name__ == "__main__":
    import os
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=int(os.getenv("DATABRICKS_APP_PORT", "8080")),
        path="/mcp",
        stateless_http=True,  # ponto de atenção 1: aqui, não no construtor
    )
'''
print(APP_PY)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. `app.yaml` — comando de inicialização
# MAGIC Como usamos `mcp.run()` no `__main__`, o comando é `python app.py` (e NÃO `uvicorn app:app`).

# COMMAND ----------

APP_YAML = '''command:
  - "python"
  - "app.py"
'''
print(APP_YAML)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. `requirements.txt`
# MAGIC `fastmcp` não é pré-instalado no runtime de Apps; `uvicorn` sim, mas listamos por garantia.

# COMMAND ----------

REQUIREMENTS = '''fastmcp>=2.0.0
uvicorn
'''
print(REQUIREMENTS)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Parâmetros e cliente
# MAGIC Deploy feito **do próprio notebook** via SDK (`WorkspaceClient` já autenticado pelo contexto).

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.apps import App, AppDeployment

w = WorkspaceClient()
me = w.current_user.me().user_name

dbutils.widgets.text("app_name", "demo-mcp-larissa", "Nome do App")
APP_NAME = dbutils.widgets.get("app_name")

# Nota: o deploy de apps exige o path absoluto COM prefixo /Workspace.
# (o w.workspace.upload aceita ambos, mas apps.deploy exige /Workspace/...)
SRC_PATH = f"/Workspace/Users/{me}/apps/{APP_NAME}"
print("App:", APP_NAME)
print("Source path:", SRC_PATH)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Escrever os 3 arquivos direto no WORKSPACE
# MAGIC Gravamos `app.py`, `app.yaml` e `requirements.txt` no `SRC_PATH` via API do workspace.

# COMMAND ----------

from databricks.sdk.service.workspace import ImportFormat

w.workspace.mkdirs(SRC_PATH)

files = {
    "app.py": APP_PY.lstrip(),
    "app.yaml": APP_YAML,
    "requirements.txt": REQUIREMENTS,
}
for name, content in files.items():
    w.workspace.upload(
        path=f"{SRC_PATH}/{name}",
        content=content.encode("utf-8"),
        format=ImportFormat.AUTO,
        overwrite=True,
    )
    print("gravado:", f"{SRC_PATH}/{name}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Criar o app (se ainda não existe) — provisiona compute (~1-2 min)

# COMMAND ----------

try:
    app = w.apps.get(name=APP_NAME)
    print("App já existe:", app.name, "| status:", app.compute_status.state if app.compute_status else "?")
except Exception:
    print("Criando app (aguardando compute)...")
    app = w.apps.create_and_wait(app=App(name=APP_NAME))
    print("App criado:", app.name)

print("URL:", app.url)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Deploy do código no app

# COMMAND ----------

deployment = w.apps.deploy_and_wait(
    app_name=APP_NAME,
    app_deployment=AppDeployment(source_code_path=SRC_PATH),
)
print("Deployment state:", deployment.status.state if deployment.status else "?")
print("Mensagem:", deployment.status.message if deployment.status else "")

# refrescar a URL
app = w.apps.get(name=APP_NAME)
APP_URL = app.url
print("APP_URL:", APP_URL)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Verificar que o app está no ar (do próprio notebook)
# MAGIC Chamamos `GET /` (health) e `tools/list` em `/mcp` usando o token do contexto.

# COMMAND ----------

import json
import requests

ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
TOKEN = ctx.apiToken().get()
h = {"Authorization": f"Bearer {TOKEN}"}

# health
r = requests.get(f"{APP_URL}/", headers=h, timeout=30)
print("GET / ->", r.status_code, r.text[:120])

# tools/list
r = requests.post(
    f"{APP_URL}/mcp",
    headers={**h, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
    data=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
    timeout=30,
)
print("tools/list ->", r.status_code)
print(r.text[:1500])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10. Redeploy (quando mudar o código)
# MAGIC Reexecute as células 6 (regrava os arquivos) e 8 (`deploy_and_wait`). Ou via CLI:
# MAGIC ```bash
# MAGIC databricks apps deploy $APP --source-code-path "/Workspace/Users/$USER/apps/$APP" --profile $PROFILE
# MAGIC ```
# MAGIC
# MAGIC ## 11. Debug — ver logs
# MAGIC No notebook: `w.apps.get(name=APP_NAME)` mostra `compute_status`/`app_status`. Logs detalhados via CLI:
# MAGIC ```bash
# MAGIC databricks apps logs $APP --tail-lines 100 --profile $PROFILE
# MAGIC ```
# MAGIC Padrões: `[BUILD]` = deploy/instalação; `[APP]` = saída do processo. Procurar tracebacks e
# MAGIC `Uvicorn running on ...`. Se `started successfully` mas 502 em todas as rotas → ver o ponto de atenção 3 (lifespan).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 12. Próximo passo — registrar no Unity AI Gateway
# MAGIC Com o app no ar, siga o notebook **`registrar_mcp_unity_ai_gateway`**:
# MAGIC - cria HTTP Connection apontando pra `https://<app>.aws.databricksapps.com` + base_path `/mcp`
# MAGIC   (bearer_token = token válido; produção = OAuth M2M de SP com CAN USE no app);
# MAGIC - cria MCP Service referenciando a connection;
# MAGIC - testa `tools/call` pelo gateway e por um agente.
# MAGIC
# MAGIC ## Resumo dos pontos de atenção (para qualquer MCP hospedado como App)
# MAGIC | # | Sintoma | Causa | Correção |
# MAGIC |---|---------|-------|----------|
# MAGIC | 1 | `TypeError: no longer accepts stateless_http` | parâmetro no construtor | passar no `run()`/`http_app()` |
# MAGIC | 2 | 502 em `GET /` | proxy do App faz health na raiz | `@mcp.custom_route("/", ...)` |
# MAGIC | 3 | "started successfully" mas 502 em todas as rotas | uvicorn externo deixa o lifespan pendente | usar `mcp.run(...)` + `command: [python, app.py]` |
# MAGIC | 4 | comportamento inconsistente no deploy | `.pyc` local divergente (≠3.11) | remover `__pycache__` antes de subir |
