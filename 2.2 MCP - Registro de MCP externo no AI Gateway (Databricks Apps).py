# Databricks notebook source
# MAGIC %md
# MAGIC # Registrar um MCP externo no Unity AI Gateway — guia prático
# MAGIC
# MAGIC Este notebook percorre, de ponta a ponta, o registro de um **MCP server externo** no
# MAGIC **Unity AI Gateway** do Databricks.
# MAGIC
# MAGIC ## Modelo mental
# MAGIC O registro tem **duas peças** no Unity Catalog:
# MAGIC
# MAGIC 1. **HTTP Connection** — um *securable* do UC que guarda o **endpoint** do MCP + a **credencial**.
# MAGIC    O Databricks executa um *proxy gerenciado* na frente do servidor e injeta a credencial.
# MAGIC 2. **MCP Service** — um objeto do UC que **referencia** essa connection e passa a ser a *tool*
# MAGIC    que agentes e o Playground conseguem chamar. Concede-se `EXECUTE` para quem pode utilizá-la.
# MAGIC
# MAGIC ## Requisitos do servidor MCP
# MAGIC - Transport **obrigatório = Streamable HTTP** (JSON-RPC em streaming; o header de invocação
# MAGIC   usa `Accept: application/json, text/event-stream`).
# MAGIC - Deve anunciar uma **versão de protocolo oficial** no `initialize`. Válidas (ago/2026):
# MAGIC   `2024-11-05`, `2025-03-26`, `2025-11-25`, `2026-07-28`.
# MAGIC   > **Observação:** se ocorrer `Unsupported protocol version: 2025-06-18`, trata-se de **SDK
# MAGIC   > desatualizado no servidor** — essa data não existe na especificação. A correção é atualizar
# MAGIC   > o SDK e reimplantar o servidor. Não é configuração do Databricks.
# MAGIC
# MAGIC ## Exemplo utilizado — MCP próprio hospedado em Databricks App
# MAGIC **Nota sobre MCPs públicos:** foram avaliados quatro servidores MCP públicos e todos
# MAGIC apresentaram incompatibilidades atrás do proxy governado do Unity Catalog — cada um por um
# MAGIC motivo distinto (rejeição do header Authorization, exigência de token real, bloqueio de tráfego
# MAGIC servidor-a-servidor, ou exigência de sessão que o gateway não gerencia). Para uma prova de
# MAGIC conceito confiável, o recomendado é um servidor MCP próprio, no qual se controla auth e comportamento.
# MAGIC
# MAGIC Este exemplo utiliza um MCP mínimo (FastMCP) hospedado como **Databricks App**, com tools de
# MAGIC negócio sintéticas de operação de mineração. Código do app em `mcp_demo_app/`.
# MAGIC Tools: `producao_mina`, `listar_minas`, `status_equipamento`, `alertas_ativos`, `now`.
# MAGIC
# MAGIC > **Pontos de atenção do MCP em Databricks App** (ver seção final):
# MAGIC > não usar `uvicorn app:app` (lifespan pendura → 502); usar `mcp.run(...)`. Health em `GET /`.
# MAGIC > `stateless_http` vai no `run()`/`http_app()`, não no construtor.
# MAGIC >
# MAGIC > **Reautenticação ("Login required"):** ao chamar um MCP em App, o gateway pode pedir login
# MAGIC > (`-32042`) — fluxo padrão do Databricks: clique no link, autorize e repita. Com token de usuário
# MAGIC > estático o aviso reaparece a cada ~1h; para estabilidade use OAuth M2M. Ver notebook `2.3`, seção 4b.
# MAGIC
# MAGIC ## Docs oficiais
# MAGIC - Register an external MCP server: `docs.databricks.com/aws/en/ai-gateway/register-mcp-service`
# MAGIC - HTTP Connections (tipos de auth): `docs.databricks.com/aws/en/query-federation/http`
# MAGIC - MCP versioning: `modelcontextprotocol.io/specification/versioning`

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Parâmetros
# MAGIC Ajuste catálogo/schema onde os objetos vão residir. Utilize um schema no qual se tenha o
# MAGIC privilégio `CREATE CONNECTION`.

# COMMAND ----------

# MAGIC %run "./0.0 Setup - Parâmetros do cliente"

# COMMAND ----------

# CATALOG, SCHEMA, APP_NAME, CONN_PREFIX, HOST/TOKEN/HEADERS/GWH, rest(), w vêm do setup.
dbutils.widgets.text("mcp_service_id", "demo_mcp_app", "ID do MCP Service")
dbutils.widgets.text("mcp_host", "", "Host do App MCP (vazio = derivar do APP_NAME)")

MCP_SERVICE_ID = dbutils.widgets.get("mcp_service_id")
CONNECTION_NAME = f"{CONN_PREFIX}{MCP_SERVICE_ID}_conn"

# Host do App: usa o widget, ou deriva do APP_NAME configurado no setup.
MCP_HOST = dbutils.widgets.get("mcp_host").strip().rstrip("/") or w.apps.get(name=APP_NAME).url.rstrip("/")
MCP_BASE_PATH = "/mcp"

print(f"Vai registrar: {CATALOG}.{SCHEMA}.{CONNECTION_NAME}  ->  {MCP_HOST}{MCP_BASE_PATH}")
print(f"MCP Service:   {CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Contexto do workspace e cliente REST
# MAGIC Dentro de um notebook Databricks o token e a URL já vêm do contexto — não precisa colar PAT.

# COMMAND ----------

# HOST, TOKEN, HEADERS, rest(), w e json vêm do setup (0.0).
print("Workspace:", HOST, "| usuário:", w.current_user.me().user_name)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Criar a HTTP Connection
# MAGIC O App é protegido pelo OAuth do workspace. Em **produção**, o adequado é **OAuth M2M** com um
# MAGIC service principal que tenha `CAN USE` no App (sem login, renovação automática, segredo em Secret).
# MAGIC O bloco M2M de referência está comentado abaixo.
# MAGIC
# MAGIC **Neste lab**, usamos a **autenticação do notebook**: `bearer_token` = token de runtime
# MAGIC (`ctx.apiToken()`). Nenhum token fica no código; se expirar (~1h), rode esta célula de novo.
# MAGIC
# MAGIC > Para um MCP externo com IdP próprio (ex.: Entra ID), use **U2M** — ver notebook `2.3`.

# COMMAND ----------

# rest() e requests vêm do setup (0.0).
# Cria a connection (idempotente: apaga se já existir)
existing = rest("GET", "/api/2.1/unity-catalog/connections").get("connections", [])
full_conn_name = CONNECTION_NAME
for c in existing:
    if c.get("name") == CONNECTION_NAME:
        print("Connection já existe, recriando...")
        rest("DELETE", f"/api/2.1/unity-catalog/connections/{CONNECTION_NAME}")

# LAB: autenticação do notebook (TOKEN de runtime). Nenhum token no código; renova rodando de novo.
conn_body = {
    "name": CONNECTION_NAME,
    "connection_type": "HTTP",
    "comment": "MCP em Databricks App - auth do notebook (lab)",
    "options": {"host": MCP_HOST, "port": "443", "base_path": MCP_BASE_PATH, "bearer_token": TOKEN},
}
conn = rest("POST", "/api/2.1/unity-catalog/connections", body=conn_body)
print("Connection criada:", conn.get("name"))

# PRODUÇÃO — OAuth M2M com service principal (CAN USE no App); segredo via Databricks Secret:
# conn_body["comment"] = "MCP em Databricks App - OAuth M2M"
# conn_body["options"] = {"host": MCP_HOST, "port": "443", "base_path": MCP_BASE_PATH,
#     "is_mcp_connection": "true", "token_endpoint": f"{HOST}/oidc/v1/token",
#     "client_id": "<client_id do service principal>",
#     "client_secret": get_secret("sp_oauth_secret"), "oauth_scope": "all-apis"}
# conn = rest("POST", "/api/2.1/unity-catalog/connections", body=conn_body)

# COMMAND ----------

# MAGIC %md
# MAGIC ### (Opcional) Usar um Databricks Secret pro token, em vez de plaintext
# MAGIC Prática recomendada quando o MCP exige credencial real. Rode via SQL `CREATE CONNECTION`:
# MAGIC ```sql
# MAGIC CREATE CONNECTION my_mcp_conn TYPE HTTP
# MAGIC OPTIONS (
# MAGIC   host 'https://api.exemplo-mcp.com',
# MAGIC   port '443',
# MAGIC   base_path '/mcp',
# MAGIC   bearer_token secret('meu_scope','mcp_token')
# MAGIC );
# MAGIC ```
# MAGIC (Crie o scope antes: `databricks secrets create-scope meu_scope` + `put-secret`.)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Sanidade: o proxy consegue falar com o MCP? (handshake `initialize`)
# MAGIC Antes de criar o MCP Service, valide que o servidor responde e anuncia uma **versão de protocolo válida**.
# MAGIC Chamamos o servidor **através do proxy do UC** — assim testamos a connection de verdade.

# COMMAND ----------

proxy_url = f"{HOST}/api/2.0/unity-catalog/connections/{CONNECTION_NAME}/proxy"

init_body = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "notebook-teste-larissa", "version": "1.0"},
    },
}

resp = requests.post(
    proxy_url,
    headers={**HEADERS, "Accept": "application/json, text/event-stream"},
    data=json.dumps(init_body),
)
print("Status:", resp.status_code)
print(resp.text[:2000])

# Extrai a protocolVersion anunciada (a resposta pode vir como SSE)
raw = resp.text
if '"protocolVersion"' in raw:
    import re
    m = re.search(r'"protocolVersion"\s*:\s*"([^"]+)"', raw)
    if m:
        ver = m.group(1)
        VALIDAS = {"2024-11-05", "2025-03-26", "2025-11-25", "2026-07-28"}
        print("\nprotocolVersion anunciada:", ver)
        print("OK" if ver in VALIDAS else
              "ATENCAO: versao fora da lista oficial -> SDK desatualizado no servidor")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Criar o MCP Service
# MAGIC O MCP Service referencia a connection e vira o objeto chamável. `include_tool_selectors` vazio
# MAGIC expõe **todas** as tools descobertas; para produção você pode restringir a tools específicas.

# COMMAND ----------

svc_params = {
    "parent": f"schemas/{CATALOG}.{SCHEMA}",
    "mcp_service_id": MCP_SERVICE_ID,
}
svc_body = {
    "comment": "MCP externo DeepWiki (teste)",
    "config": {
        # a HTTP Connection é metastore-level: referenciar SÓ pelo nome simples
        # (connections/<nome>), NÃO connections/<catalog>.<schema>.<nome>
        "source_connection": {
            "name": f"connections/{CONNECTION_NAME}"
        },
        "include_tool_selectors": [],  # [] = expõe todas as tools
    },
}

# idempotente
try:
    rest("DELETE",
         f"/api/2.1/unity-catalog/mcp-services/{CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}")
    print("MCP Service anterior removido.")
except Exception:
    pass

svc = rest("POST", "/api/2.1/unity-catalog/mcp-services",
           params=svc_params, body=svc_body)
print(json.dumps(svc, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Conceder EXECUTE
# MAGIC Dê `EXECUTE` no MCP Service pra quem vai invocar (não conceda `USE CONNECTION` direto pros usuários finais).

# COMMAND ----------

me = w.current_user.me().user_name
grant_body = {
    "changes": [
        {"principal": me, "add": ["EXECUTE"]}
    ]
}
# securable_type = mcp_service ; full_name = catalog.schema.service
rest("PATCH",
     f"/api/2.1/unity-catalog/permissions/mcp_service/{CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}",
     body=grant_body)
print(f"EXECUTE concedido a {me}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Testar o MCP Service — listar tools
# MAGIC Agora chamamos o **endpoint do gateway** (não o proxy da connection). O gateway já fala JSON-RPC MCP.
# MAGIC Fazemos `initialize` e depois `tools/list`.
# MAGIC
# MAGIC > **IMPORTANTE — login OAuth (elicitation):** como o MCP está hospedado num **Databricks App**
# MAGIC > protegido por OAuth do workspace, a primeira chamada pode retornar
# MAGIC > `-32042 Login required ... Open the link to log in`. Isso é esperado: abra o link retornado
# MAGIC > (aponta pro MCP Service no Catalog Explorer), autorize uma vez, e reexecute a célula.
# MAGIC > **Este notebook só funciona INTERATIVAMENTE** (você aberto no workspace). Rodar como **job
# MAGIC > não-interativo NÃO passa** nessa etapa — pra automação/agente deployado, a connection precisa
# MAGIC > de **OAuth M2M com um service principal** (client_id/secret + token_endpoint), não token de usuário.

# COMMAND ----------

gateway_url = f"{HOST}/ai-gateway/mcp-services/{CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}"
gw_headers = {**HEADERS, "Accept": "application/json, text/event-stream"}

def gw_call(method, params=None, _id=1):
    body = {"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}
    r = requests.post(gateway_url, headers=gw_headers, data=json.dumps(body))
    return r.status_code, r.text

# 1) initialize
sc, txt = gw_call("initialize", {
    "protocolVersion": "2025-11-25",
    "capabilities": {},
    "clientInfo": {"name": "notebook", "version": "1.0"},
})
print("initialize:", sc)
print(txt[:1000], "\n")

# 2) tools/list
sc, txt = gw_call("tools/list", {}, _id=2)
print("tools/list:", sc)
print(txt[:3000])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. (Opcional) Chamar uma tool
# MAGIC O App expõe `producao_mina`, `listar_minas`, `status_equipamento`, `alertas_ativos`, `now`.
# MAGIC Ajuste `name` e `arguments` conforme o que apareceu no `tools/list` acima.

# COMMAND ----------

sc, txt = gw_call("tools/call", {
    "name": "producao_mina",
    "arguments": {"mina_id": "MINA-NORTE"},
}, _id=3)
print("producao_mina('MINA-NORTE'):", sc)
print(txt[:800], "\n")

sc, txt = gw_call("tools/call", {
    "name": "alertas_ativos",
    "arguments": {},
}, _id=4)
print("alertas_ativos():", sc)
print(txt[:800])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7.5 Padrão PROGRAMÁTICO — helpers reutilizáveis pra listar/chamar tools
# MAGIC Empacotamos as chamadas do gateway (das células 3-7) em funções `mcp_list_tools` /
# MAGIC `mcp_call_tool` que um agente pode reusar. Usamos `requests` cru contra o endpoint do gateway
# MAGIC — funciona em notebook e em job, sem dependência de event loop.
# MAGIC
# MAGIC > **Existe o cliente oficial `DatabricksMCPClient`** (`pip install databricks-mcp`), que abstrai
# MAGIC > o handshake/SSE. Porém seus métodos síncronos chamam `asyncio.run()` por baixo, o que dá
# MAGIC > `RuntimeError: asyncio.run() cannot be called from a running event loop` dentro do notebook
# MAGIC > Databricks (que já roda num event loop). Em **código de agente deployado** (fora do notebook)
# MAGIC > o `DatabricksMCPClient` funciona direto. Aqui, os helpers via requests são mais robustos.

# COMMAND ----------

def _parse_sse(txt):
    """Extrai o JSON-RPC de uma resposta SSE (ou JSON puro) do gateway.
    A resposta pode vir como 'event: message\\ndata: {...}'. Pegamos o payload
    da última linha 'data:' que contém um JSON-RPC válido com 'result' ou 'error'.
    """
    payload = None
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[len("data:"):].strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "result" in obj or "error" in obj:
                payload = obj
    if payload is None:
        raise RuntimeError(f"resposta inesperada do gateway:\n{txt[:500]}")
    if "error" in payload:
        raise RuntimeError(f"gateway retornou erro: {payload['error']}")
    return payload


def mcp_list_tools():
    sc, txt = gw_call("tools/list", {}, _id=100)
    return _parse_sse(txt)["result"]["tools"]


def mcp_call_tool(name, arguments):
    sc, txt = gw_call("tools/call", {"name": name, "arguments": arguments}, _id=101)
    return _parse_sse(txt)["result"]


# 1) descobrir tools (NUNCA hardcode nome/args — sempre inspecionar em runtime)
tools = mcp_list_tools()
print("Tools disponíveis:")
for t in tools:
    print(f"  - {t['name']}: {t['description'][:70]}")

# 2) chamar uma tool
result = mcp_call_tool("producao_mina", {"mina_id": "MINA-NORTE"})
print("\nResultado:")
print(result["content"])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7.6 Integrar o MCP a um agente com function calling
# MAGIC As tools do MCP são convertidas em specs no formato OpenAI e disponibilizadas a um LLM do
# MAGIC Foundation Model API; o modelo decide quando chamá-las. É o esqueleto de um agente que pode
# MAGIC ser empacotado com MLflow e implantado em Model Serving.

# COMMAND ----------

import json as _json

# tools do MCP no formato OpenAI (function calling). tools são dicts (vindos do gateway).
oai_tools = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["inputSchema"],
        },
    }
    for t in tools
]

# cliente OpenAI apontando pro Foundation Model API do workspace (SDK já disponível)
oai = w.serving_endpoints.get_open_ai_client()
# Usar um modelo Claude ou GPT. Gemini apresenta erro de `thought_signature` no tool-calling.
LLM = "databricks-claude-opus-4-8"  # ajuste pro endpoint disponível no workspace

messages = [
    {"role": "system", "content": "Você é um assistente de operações de mineração. Use as ferramentas disponíveis para responder sobre minas e equipamentos."},
    {"role": "user", "content": "Quais equipamentos precisam de atenção e qual a produção da MINA-NORTE?"},
]

# turno 1: o modelo decide chamar uma tool
resp = oai.chat.completions.create(model=LLM, messages=messages, tools=oai_tools)
msg = resp.choices[0].message

if msg.tool_calls:
    messages.append(msg.model_dump())
    for tc in msg.tool_calls:
        args = _json.loads(tc.function.arguments)
        print(f"Modelo chamou: {tc.function.name}({args})")
        tool_result = mcp_call_tool(tc.function.name, args)  # helper da célula 7.5
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": str(tool_result["content"]),
        })
    # turno 2: o modelo responde usando o resultado da tool
    final = oai.chat.completions.create(model=LLM, messages=messages, tools=oai_tools)
    print("\nResposta final:\n", final.choices[0].message.content)
else:
    print(msg.content)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Onde ver na UI
# MAGIC - **Catalog Explorer** > seu schema > o MCP Service aparece como securable.
# MAGIC - **AI Playground**: adicione o MCP Service como *tool* de um agente e teste em linguagem natural.
# MAGIC
# MAGIC ## 9. Limpeza (rode quando terminar o teste)

# COMMAND ----------

# Descomente pra limpar
# rest("DELETE", f"/api/2.1/unity-catalog/mcp-services/{CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}")
# rest("DELETE", f"/api/2.1/unity-catalog/connections/{CONNECTION_NAME}")
# print("Limpo.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Aplicando a um servidor MCP próprio — pré-requisitos a validar
# MAGIC O fluxo de registro (connection → service → gateway) é idêntico independentemente de onde o
# MAGIC servidor MCP está hospedado. Os pontos a validar no servidor são:
# MAGIC 1. **Transport**: deve ser Streamable HTTP. STDIO (execução local via npm/pip) não é suportado.
# MAGIC 2. **Versão de protocolo**: executar a seção 3 (initialize) antes de criar o service. Deve
# MAGIC    anunciar uma das versões oficiais (2024-11-05 / 2025-03-26 / 2025-11-25 / 2026-07-28). Uma
# MAGIC    versão fora dessa lista indica SDK desatualizado no servidor — atualizar e reimplantar.
# MAGIC 3. **Header Authorization**: o proxy do UC sempre injeta `Authorization: Bearer <token>` (não há
# MAGIC    modo "sem autenticação"). Servidores que rejeitam um Authorization desconhecido são
# MAGIC    incompatíveis — o servidor deve ignorar o header ou exigir autenticação real. Servidores
# MAGIC    corporativos com autenticação são o caso natural (token/OAuth real na connection).
# MAGIC 4. **Sessão**: servidores que exigem `Mcp-Session-Id` obrigatório não são bem gerenciados pelo
# MAGIC    gateway — preferir servidores **stateless** (ex.: FastMCP `stateless_http=True`).
# MAGIC 5. **Rede**: se o servidor estiver em rede privada (ex.: VNet/Private Link), garantir rota de
# MAGIC    saída do workspace até o endpoint.
# MAGIC 6. **Credencial do endpoint**: para MCP hospedado como Databricks App, recomenda-se OAuth M2M
# MAGIC    com service principal (CAN USE no app), pois token de usuário expira. Para endpoints com
# MAGIC    Bearer fixo, usar o token real e referenciá-lo via Databricks Secret em vez de texto plano.
# MAGIC
# MAGIC ### Pontos de atenção ao hospedar o MCP como Databricks App (ver `mcp_demo_app/`)
# MAGIC - Não usar `uvicorn app:app` — deixa o lifespan do FastMCP pendente, resultando em 502 em todas
# MAGIC   as rotas mesmo com "app started successfully". Usar `mcp.run(transport="http", host="0.0.0.0",
# MAGIC   port=8080, path="/mcp", stateless_http=True)` em `if __name__=="__main__"` + app.yaml
# MAGIC   `command: [python, app.py]`.
# MAGIC - `stateless_http` vai no `run()`/`http_app()`, não no construtor `FastMCP()`.
# MAGIC - Health check: o proxy do App consulta `GET /`; adicionar `@mcp.custom_route("/", methods=["GET"])`.
# MAGIC - Não incluir `__pycache__` no deploy (o `.pyc` local pode divergir do runtime 3.11).
