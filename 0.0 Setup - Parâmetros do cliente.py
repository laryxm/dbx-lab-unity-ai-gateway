# Databricks notebook source
# MAGIC %md
# MAGIC # 0.0 Setup — Parâmetros do cliente
# MAGIC
# MAGIC Notebook central de configuração da POC. **Todos os notebooks começam com** `%run` deste, para
# MAGIC que catálogo, schema, nome do App, prefixos e credenciais fiquem em um único lugar.
# MAGIC
# MAGIC **Como usar:** edite apenas a célula "Parâmetros do cliente". O restante (contexto do workspace e
# MAGIC funções auxiliares) é derivado automaticamente e não precisa ser alterado.
# MAGIC
# MAGIC No topo de cada notebook:
# MAGIC ```python
# MAGIC # MAGIC %run "./0.0 Setup - Parâmetros do cliente"
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## Parâmetros do cliente — EDITE AQUI

# COMMAND ----------

# ============================ EDITE AQUI ============================
CATALOG      = "larissa_xm"      # catálogo onde os objetos da POC vivem
SCHEMA       = "mcps"            # schema para MCP Services e UC Functions
APP_NAME     = "skill-pdf-mcp"   # Databricks App que hospeda um MCP próprio (Pilares 2 e 5)
CONN_PREFIX  = "lxm_"            # prefixo das HTTP Connections (a namespace é metastore-level, compartilhada)
SECRET_SCOPE = "mcp_poc"         # scope de secrets para tokens/API keys
# ===================================================================

# COMMAND ----------

# MAGIC %md
# MAGIC ## Contexto do workspace e funções auxiliares (derivado — não editar)

# COMMAND ----------

import json
import requests
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

# Host e token vêm do contexto do notebook (não precisa colar PAT).
try:
    _ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    HOST = _ctx.apiUrl().get().rstrip("/")
    TOKEN = _ctx.apiToken().get()
except Exception:
    HOST = w.config.host.rstrip("/")
    TOKEN = None

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
GWH = {**HEADERS, "Accept": "application/json, text/event-stream"}          # p/ endpoints MCP (SSE)
REDIRECT_URI = f"{HOST}/login/oauth/http.html"                             # allowlist OAuth do provedor


def rest(method, path, params=None, body=None):
    """Chamada REST à API do workspace com o token do contexto."""
    r = requests.request(method, f"{HOST}{path}", headers=HEADERS, params=params,
                         data=json.dumps(body) if body is not None else None)
    if not r.ok:
        raise RuntimeError(f"{method} {path} -> {r.status_code}\n{r.text}")
    return r.json() if r.text else {}


def parse_sse(txt):
    """Extrai o objeto JSON-RPC de uma resposta que pode vir como SSE (event/data) ou JSON puro."""
    for ln in txt.splitlines():
        s = ln[5:].strip() if ln.startswith("data:") else ln.strip()
        if s.startswith("{"):
            try:
                o = json.loads(s)
            except json.JSONDecodeError:
                continue
            if "result" in o or "error" in o:
                return o
    try:
        return json.loads(txt)
    except Exception:
        return {"raw": txt[:400]}


def mcp_call(url, method, params=None, _id=1):
    """Chamada JSON-RPC a um endpoint MCP (gateway ou managed). Retorna (status, objeto)."""
    r = requests.post(url, headers=GWH,
                      data=json.dumps({"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}))
    return r.status_code, parse_sse(r.text)


def gateway_mcp_url(service_full_name):
    """URL do gateway para um MCP Service registrado (catalog.schema.service)."""
    return f"{HOST}/ai-gateway/mcp-services/{service_full_name}"


def managed_functions_mcp_url(catalog=None, schema=None):
    """URL do managed MCP que expõe as UC Functions de um schema."""
    return f"{HOST}/api/2.0/mcp/functions/{catalog or CATALOG}/{schema or SCHEMA}"


def get_secret(key, default=""):
    """Lê um secret do SECRET_SCOPE; retorna default se não existir (útil em teste)."""
    try:
        return dbutils.secrets.get(SECRET_SCOPE, key)
    except Exception:
        return default


print("Setup OK")
print(f"  workspace : {HOST}")
print(f"  catálogo  : {CATALOG}.{SCHEMA}")
print(f"  app       : {APP_NAME}")
print(f"  prefixo   : {CONN_PREFIX}  | secret scope: {SECRET_SCOPE}")
