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
# MAGIC Um Databricks App servindo Streamable HTTP em `/mcp` com tools de negócio sintéticas genéricas
# MAGIC (`consultar_produto`, `listar_produtos`, `status_pedido`, `pedidos_com_alerta`, `now`).
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
Tools de negócio sintéticas genéricas. Dados 100% fictícios."""
from datetime import datetime, timezone
from fastmcp import FastMCP

mcp = FastMCP(name="demo-mcp")

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
    """Nome, categoria, estoque e status de um produto. SKUs: SKU-1001, SKU-1002, SKU-1003."""
    p = _PRODUTOS.get(sku.upper())
    return {"sku": sku.upper(), **p} if p else {"error": f"produto {sku} não encontrado"}


@mcp.tool
def listar_produtos(categoria: str = "") -> list:
    """Lista os produtos do catálogo. Se `categoria` for informada, filtra por ela."""
    return [{"sku": sku, **p} for sku, p in _PRODUTOS.items()
            if not categoria or p["categoria"].lower() == categoria.lower()]


@mcp.tool
def status_pedido(pedido_id: str) -> dict:
    """SKU, quantidade, região, status e alertas de um pedido. IDs: PED-5001, PED-5002, PED-5003."""
    o = _PEDIDOS.get(pedido_id.upper())
    return {"pedido": pedido_id.upper(), **o} if o else {"error": f"pedido {pedido_id} não encontrado"}


@mcp.tool
def pedidos_com_alerta(regiao: str = "") -> list:
    """Pedidos com alerta ativo. Se `regiao` for informada, filtra por ela."""
    return [{"pedido": pid, "regiao": o["regiao"], "status": o["status"], "alerta": o["alerta"]}
            for pid, o in _PEDIDOS.items()
            if o["alerta"] and (not regiao or o["regiao"] == regiao.lower())]


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

dbutils.widgets.text("app_name", "demo-mcp", "Nome do App")
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
