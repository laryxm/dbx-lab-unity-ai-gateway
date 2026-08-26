# Databricks notebook source
# MAGIC %md
# MAGIC # Registrar o SlideSpeak MCP no Unity AI Gateway
# MAGIC
# MAGIC Notebook para registrar o **SlideSpeak MCP** (gerador de apresentações .pptx) como
# MAGIC MCP Service no Unity AI Gateway.
# MAGIC
# MAGIC ## O que é o SlideSpeak MCP
# MAGIC Um conector MCP que expõe a API da SlideSpeak como tools — um agente gera/edita/baixa
# MAGIC apresentações PowerPoint a partir de linguagem natural. Docs:
# MAGIC `docs.slidespeak.co/v1/quickstart` · `docs.slidespeak.co/v1/integrations` ·
# MAGIC repo `github.com/SlideSpeak/slidespeak-mcp`.
# MAGIC
# MAGIC ## Duas camadas (importante entender antes de registrar)
# MAGIC 1. **API da SlideSpeak** (`api.slidespeak.co`) — o serviço que gera os PPTX (infra SlideSpeak).
# MAGIC 2. **MCP server** — uma casca MCP que traduz chamadas MCP → API da SlideSpeak. Duas formas:
# MAGIC    - **Hosted:** `https://mcp.slidespeak.co/mcp` (roda na infra da SlideSpeak).
# MAGIC    - **Self-host:** imagem Docker `slidespeak/slidespeak-mcp`, executada em infraestrutura
# MAGIC      própria (ex.: Azure Container Instances). Nesse caso o endpoint registrado no gateway é
# MAGIC      o do container (ex.: `https://<mcp-host>.azurecontainer.io/mcp`).
# MAGIC
# MAGIC Em AMBOS os casos, a credencial é a **API key da SlideSpeak** (`slidespeak.co/slidespeak-api`),
# MAGIC enviada como **`Authorization: Bearer <API-KEY>`** — validado ao vivo (o servidor exige esse header).
# MAGIC Isso encaixa direto na HTTP Connection do gateway (bearer_token). NÃO cai no problema do DeepWiki
# MAGIC (que rejeitava Authorization) — aqui o Authorization é EXIGIDO.
# MAGIC
# MAGIC > **Custo:** a API da SlideSpeak é paga por geração. Este notebook registra e faz `tools/list`
# MAGIC > (não consome créditos). O `tools/call` que gera deck consome — rodar só quando for demonstrar.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Parâmetros
# MAGIC Escolha o modo (hosted vs self-host) e informe a API key da SlideSpeak.

# COMMAND ----------

# MAGIC %run "./0.0 Setup - Parâmetros do cliente"

# COMMAND ----------

# CATALOG, SCHEMA, CONN_PREFIX, SECRET_SCOPE, HOST/TOKEN/HEADERS/GWH, rest(), get_secret() vêm do setup.
dbutils.widgets.dropdown("modo", "hosted", ["hosted", "self_host_aci"], "Modo de hospedagem")
dbutils.widgets.text("mcp_service_id", "slidespeak_mcp", "ID do MCP Service")
dbutils.widgets.text("aci_host", "https://SEU-MCP-NO-ACI.azurecontainer.io", "Host do MCP no ACI (só se self_host)")
dbutils.widgets.text("secret_key", "slidespeak_api_key", "Chave da API no scope de secrets")

MODO = dbutils.widgets.get("modo")
MCP_SERVICE_ID = dbutils.widgets.get("mcp_service_id")
CONNECTION_NAME = f"{CONN_PREFIX}slidespeak_conn"
API_KEY = get_secret(dbutils.widgets.get("secret_key"))   # do Databricks Secret, não em texto plano

MCP_HOST = "https://mcp.slidespeak.co" if MODO == "hosted" else dbutils.widgets.get("aci_host").rstrip("/")
MCP_BASE_PATH = "/mcp"

print(f"Modo: {MODO} | endpoint: {MCP_HOST}{MCP_BASE_PATH}")
print(f"Connection: {CONNECTION_NAME} | MCP Service: {CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}")
print("API key encontrada no secret:", "sim" if API_KEY else "não — configure o secret")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Contexto do workspace + helper REST

# COMMAND ----------

# HOST, TOKEN, HEADERS, rest(), w e json/requests vêm do setup (0.0).
print("Workspace:", HOST, "| usuário:", w.current_user.me().user_name)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Criar a HTTP Connection (API key da SlideSpeak como bearer_token)
# MAGIC > **Namespace de connections é compartilhada (metastore-level).** Por isso o prefixo `CONN_PREFIX` do setup.

# COMMAND ----------

assert API_KEY, f"Configure a API key em secret({SECRET_SCOPE}/{dbutils.widgets.get('secret_key')})."

# idempotente: se já existe, ATUALIZA in-place (PATCH) pra não quebrar o MCP Service
existing = [c["name"] for c in rest("GET", "/api/2.1/unity-catalog/connections").get("connections", [])]
opts = {"host": MCP_HOST, "port": "443", "base_path": MCP_BASE_PATH, "bearer_token": API_KEY}

if CONNECTION_NAME in existing:
    print("Connection já existe -> PATCH (preserva connection_id)")
    conn = rest("PATCH", f"/api/2.1/unity-catalog/connections/{CONNECTION_NAME}",
                body={"options": opts})
else:
    conn = rest("POST", "/api/2.1/unity-catalog/connections",
                body={"name": CONNECTION_NAME, "connection_type": "HTTP",
                      "comment": f"SlideSpeak MCP ({MODO})", "options": opts})
print("Connection OK:", conn.get("name"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2b. (Produção) API key via Databricks Secret, em vez de plaintext no widget
# MAGIC ```sql
# MAGIC CREATE CONNECTION lxm_slidespeak_conn TYPE HTTP
# MAGIC OPTIONS (
# MAGIC   host 'https://mcp.slidespeak.co', port '443', base_path '/mcp',
# MAGIC   bearer_token secret('mcp_scope','slidespeak_api_key')
# MAGIC );
# MAGIC ```
# MAGIC (crie o scope: `databricks secrets create-scope mcp_scope` + `put-secret`.)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Sanidade: handshake `initialize` via proxy do UC
# MAGIC Valida que a connection alcança o SlideSpeak e que a API key é aceita.
# MAGIC (`initialize` não gera deck — não consome crédito.)

# COMMAND ----------

proxy = f"{HOST}/api/2.0/unity-catalog/connections/{CONNECTION_NAME}/proxy"
r = requests.post(proxy, headers={**HEADERS, "Accept": "application/json, text/event-stream"},
                  data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                              "clientInfo": {"name": "notebook", "version": "1.0"}}}))
print("initialize:", r.status_code)
print(r.text[:800])
# Se aparecer 'invalid_token' -> API key errada/expirada. Se 'protocolVersion' -> OK.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Criar o MCP Service

# COMMAND ----------

try:
    rest("DELETE", f"/api/2.1/unity-catalog/mcp-services/{CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}")
except Exception:
    pass

svc = rest("POST", "/api/2.1/unity-catalog/mcp-services",
           params={"parent": f"schemas/{CATALOG}.{SCHEMA}", "mcp_service_id": MCP_SERVICE_ID},
           body={"comment": f"SlideSpeak MCP ({MODO})",
                 "config": {"source_connection": {"name": f"connections/{CONNECTION_NAME}"},
                            "include_tool_selectors": []}})
print("MCP Service:", svc.get("name"))

me = w.current_user.me().user_name
rest("PATCH", f"/api/2.1/unity-catalog/permissions/mcp_service/{CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}",
     body={"changes": [{"principal": me, "add": ["EXECUTE"]}]})
print("EXECUTE concedido a", me)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Listar as tools do SlideSpeak (via gateway) — NÃO consome crédito

# COMMAND ----------

gateway = f"{HOST}/ai-gateway/mcp-services/{CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}"
gwh = {**HEADERS, "Accept": "application/json, text/event-stream"}


def _parse_sse(txt):
    for ln in txt.splitlines():
        ln = ln[5:].strip() if ln.startswith("data:") else ln.strip()
        if ln.startswith("{"):
            try:
                o = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if "result" in o or "error" in o:
                return o
    raise RuntimeError(f"resposta inesperada:\n{txt[:400]}")


def gw(method, params=None, _id=1):
    r = requests.post(gateway, headers=gwh,
                      data=json.dumps({"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}))
    return _parse_sse(r.text)


res = gw("tools/list", {}, _id=2)
if "error" in res:
    print("ERRO:", res["error"])
else:
    print("Tools do SlideSpeak:")
    for t in res["result"]["tools"]:
        print(f"  - {t['name']}: {t.get('description','')[:80]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. (OPCIONAL — CONSOME CRÉDITO) Gerar uma apresentação
# MAGIC Só rode quando for demonstrar. Ajuste `name`/`arguments` conforme o `tools/list` acima
# MAGIC (os nomes das tools variam por versão do SlideSpeak MCP).

# COMMAND ----------

# Exemplo genérico — DESCOMENTE e ajuste ao tool real listado acima:
# res = gw("tools/call", {
#     "name": "generate_powerpoint",   # <- confira o nome no tools/list
#     "arguments": {"plain_text": "Visão geral de operação de mineração: 3 minas, produção de cobre e ouro", "length": 5},
# }, _id=3)
# print(json.dumps(res, indent=2)[:2000])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Testar no AI Playground
# MAGIC - Catalog Explorer > `larissa_xm.mcps` > `slidespeak_mcp` > **Try in Playground**.
# MAGIC - Use um modelo **Claude** (evite Gemini — bug do `thought_signature` no tool-calling).
# MAGIC - Peça: *"Gere uma apresentação de 5 slides sobre X"* (consome crédito SlideSpeak).
# MAGIC
# MAGIC ## Considerações de implantação
# MAGIC - **Self-host (recomendado para infraestrutura própria):** executar
# MAGIC   `docker run -e SLIDESPEAK_API_KEY=... slidespeak/slidespeak-mcp`, expor `/mcp` por HTTPS e
# MAGIC   registrar esse endpoint (modo `self_host_aci`, preenchendo `aci_host`). Vantagem: o MCP roda
# MAGIC   na mesma rede da aplicação consumidora e o gateway governa o acesso de forma centralizada.
# MAGIC - **Autenticação:** a API key da SlideSpeak é armazenada na connection (Bearer). Recomenda-se
# MAGIC   referenciá-la via **Databricks Secret** (seção 2b) em vez de texto plano.
# MAGIC - **Identidade do usuário:** a connection Bearer utiliza uma única API key compartilhada — todas
# MAGIC   as requisições usam a mesma conta SlideSpeak. Caso seja necessário rastrear ou limitar por
# MAGIC   usuário, esse controle deve ser feito na aplicação consumidora (o gateway não propaga a
# MAGIC   identidade do usuário final ao serviço externo neste modelo de autenticação por token).
# MAGIC - **Protocolo:** executar a seção 3 (initialize) previamente; se a versão anunciada não estiver
# MAGIC   entre as oficiais (2024-11-05 / 2025-03-26 / 2025-11-25 / 2026-07-28), o SDK do servidor está
# MAGIC   desatualizado — atualizar a imagem.