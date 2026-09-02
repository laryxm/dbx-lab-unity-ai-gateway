# Databricks notebook source
# MAGIC %md
# MAGIC # 5.1 Skills — Skill que EXECUTA código, exposta como tool no gateway
# MAGIC
# MAGIC > **Pilar 5 tem dois notebooks complementares.** Este (5.1) trata de skills que **executam código**
# MAGIC > (gerar PDF, rodar uma análise via `ai_query`), expostas como **tool** via App MCP ou UC Function. O
# MAGIC > notebook **5.2** trata de skills que **instruem** o agente (`SKILL.md` + arquivos de referência),
# MAGIC > governadas nativamente como **UC Skills** (securable do Unity Catalog). Regra: se a skill precisa
# MAGIC > rodar algo, use 5.1; se ela orienta com instruções e referências, use 5.2.
# MAGIC
# MAGIC ## Skill do Genie Code x tool do AI Gateway
# MAGIC Uma skill do Genie Code e uma tool do AI Gateway são mecanismos diferentes:
# MAGIC
# MAGIC | | Skill (Genie Code) | Tool (AI Gateway) |
# MAGIC |---|---|---|
# MAGIC | Onde mora | filesystem `Workspace/.assistant/skills/` | securable de MCP no Unity Catalog |
# MAGIC | Como é usada | carregada por contexto num coding agent | invocada por qualquer agente via gateway |
# MAGIC | Agente externo consegue usar? | não | sim |
# MAGIC | Testável no Playground? | não | sim (adicionada como tool) |
# MAGIC | Governança | Git folders + ACL da pasta | securable UC + `EXECUTE` por principal + service policy |
# MAGIC
# MAGIC Para que a capacidade de uma skill seja usada por um agente externo e testada no Playground, ela
# MAGIC precisa ser exposta como uma tool via MCP. O conteúdo da skill mapeia diretamente:
# MAGIC - instrução da skill → descrição da tool (o agente lê e decide usá-la)
# MAGIC - script da skill → corpo da tool
# MAGIC - arquivo de referência → catálogo/dados que a tool consulta
# MAGIC
# MAGIC ## Duas formas de expor a skill como tool
# MAGIC | Opção | Quando usar | Custo/infra |
# MAGIC |---|---|---|
# MAGIC | **A — MCP próprio como Databricks App** | skill com bibliotecas binárias ou I/O (ex.: gerar PDF) | requer um App |
# MAGIC | **B — UC Function + managed MCP** | função **inteligente** (LLM via `ai_query`) ou determinística; sem libs binárias | sem app; o mais governado e nativo no Playground |
# MAGIC
# MAGIC Este notebook implementa as duas. A Parte A empacota uma skill de geração de PDF num App. A Parte B
# MAGIC mostra uma **função inteligente** (um LLM embutido via `ai_query`) exposta como tool — para
# MAGIC **comparar com uma skill do Genie Code**: a skill deixa um coding agent inteligente por instruções;
# MAGIC a função embute a inteligência e fica governada e chamável por qualquer agente.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Parâmetros

# COMMAND ----------

# MAGIC %run "./0.0 Setup - Parâmetros do cliente"

# COMMAND ----------

# CATALOG, SCHEMA, APP_NAME, CONN_PREFIX, HOST/TOKEN/HEADERS/GWH, rest(), parse_sse() vêm do setup.
# Aqui, apenas o que é específico deste notebook:
VOLUME = "skill_artifacts"
CONNECTION_NAME = f"{CONN_PREFIX}skill_pdf_mcp_conn"
MCP_SERVICE_ID = "skill_pdf_mcp"     # Parte A (App)
FUNCTION_NAME = "analisar_operacao"  # Parte B (UC Function inteligente, com ai_query)
print("Alvo:", f"{CATALOG}.{SCHEMA}", "| app:", APP_NAME, "| MCP Service:", MCP_SERVICE_ID)

# COMMAND ----------

# MAGIC %md
# MAGIC # Parte A — MCP próprio como Databricks App
# MAGIC Indicada quando a skill precisa de bibliotecas binárias ou de escrever arquivos (ex.: gerar um PDF
# MAGIC com `reportlab` e persistir num Volume). O servidor MCP (FastMCP, Streamable HTTP) é hospedado
# MAGIC como Databricks App e registrado no gateway como um MCP Service.

# COMMAND ----------

# MAGIC %md
# MAGIC ## A1. Deploy do App (rodar no terminal local)
# MAGIC O código do App está em `skill_pdf_mcp_app/` (app.py, app.yaml, requirements.txt).
# MAGIC
# MAGIC ```bash
# MAGIC cd 04_projects_active/poc_ero_unity_ai_gateway/skill_pdf_mcp_app
# MAGIC P=<perfil>   # perfil da Databricks CLI
# MAGIC DEST=/Workspace/Users/<seu-usuário>/poc_ero_unity_ai_gateway/skill_pdf_mcp_app
# MAGIC databricks --profile $P apps create skill-pdf-mcp          # idempotente
# MAGIC databricks --profile $P workspace import-dir . "$DEST" --overwrite
# MAGIC databricks --profile $P apps deploy skill-pdf-mcp --source-code-path "$DEST"
# MAGIC databricks --profile $P apps get skill-pdf-mcp             # pega URL e service principal
# MAGIC ```
# MAGIC
# MAGIC Requisitos do servidor FastMCP em App:
# MAGIC - `app.yaml` com `command: [python, app.py]`; o FastMCP roda o próprio servidor via
# MAGIC   `mcp.run(transport="http", ..., stateless_http=True)` (um `uvicorn app:app` externo resulta em 502).
# MAGIC - rota `GET /` de health (o proxy do Apps a exige, senão 502).
# MAGIC - não versionar `__pycache__`.

# COMMAND ----------

# MAGIC %md
# MAGIC ## A2. Volume governado + permissões do service principal do App
# MAGIC O App roda como um service principal próprio, que precisa de `USE CATALOG/SCHEMA` e `WRITE VOLUME`
# MAGIC para persistir o PDF.

# COMMAND ----------

spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{VOLUME}")

app = w.apps.get(name=APP_NAME)
APP_URL = app.url.rstrip("/")
APP_SP = app.service_principal_client_id
print("App URL:", APP_URL, "| service principal:", APP_SP)

for stmt in [
    f"GRANT USE CATALOG ON CATALOG {CATALOG} TO `{APP_SP}`",
    f"GRANT USE SCHEMA ON SCHEMA {CATALOG}.{SCHEMA} TO `{APP_SP}`",
    f"GRANT WRITE VOLUME ON VOLUME {CATALOG}.{SCHEMA}.{VOLUME} TO `{APP_SP}`",
]:
    try:
        spark.sql(stmt)
        print("ok:", stmt)
    except Exception as e:
        print("aviso:", stmt, "->", str(e)[:120])

# COMMAND ----------

# MAGIC %md
# MAGIC ## A3. Registrar o MCP no gateway (HTTP Connection + MCP Service)
# MAGIC Para um MCP hospedado em Databricks App, a forma **adequada em produção é OAuth M2M** com um
# MAGIC service principal que tenha `CAN USE` no App: sem login, renovação automática, e o segredo fica em
# MAGIC um Databricks Secret (nunca no código). O bloco M2M de referência está comentado abaixo.
# MAGIC
# MAGIC **Neste lab**, para simplificar, usamos a **autenticação do próprio notebook**: o `bearer_token`
# MAGIC recebe o token de runtime (`ctx.apiToken()`). Nenhum token fica no código — é resolvido em
# MAGIC execução. Como o token de usuário expira (~1h), se der erro de auth basta **rodar esta célula de
# MAGIC novo** para renovar a connection.
# MAGIC
# MAGIC > U2M por usuário é adequado quando o MCP tem **IdP próprio** (ex.: Entra ID no MCP da Ero), não
# MAGIC > para um App Databricks. Ver notebook `2.3`.

# COMMAND ----------

for c in rest("GET", "/api/2.1/unity-catalog/connections").get("connections", []):
    if c.get("name") == CONNECTION_NAME:
        rest("DELETE", f"/api/2.1/unity-catalog/connections/{CONNECTION_NAME}")

# LAB: autenticação do notebook. `TOKEN` vem de ctx.apiToken() em runtime — nenhum token no código.
rest("POST", "/api/2.1/unity-catalog/connections", body={
    "name": CONNECTION_NAME, "connection_type": "HTTP",
    "comment": "Skill PDF (App) - auth do notebook (lab)",
    "options": {"host": APP_URL, "port": "443", "base_path": "/mcp", "bearer_token": TOKEN},
})

# PRODUÇÃO (recomendado) — OAuth M2M com service principal que tenha CAN USE no App.
# Sem login, renovação automática; o segredo vem de um Databricks Secret, nunca em texto plano:
# rest("POST", "/api/2.1/unity-catalog/connections", body={
#     "name": CONNECTION_NAME, "connection_type": "HTTP", "comment": "Skill PDF (App) - OAuth M2M",
#     "options": {"host": APP_URL, "port": "443", "base_path": "/mcp", "is_mcp_connection": "true",
#         "token_endpoint": f"{HOST}/oidc/v1/token",
#         "client_id": "<client_id do service principal>",
#         "client_secret": get_secret("sp_oauth_secret"),
#         "oauth_scope": "all-apis"},
# })

try:
    rest("DELETE", f"/api/2.1/unity-catalog/mcp-services/{CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}")
except Exception:
    pass
rest("POST", "/api/2.1/unity-catalog/mcp-services",
     params={"parent": f"schemas/{CATALOG}.{SCHEMA}", "mcp_service_id": MCP_SERVICE_ID},
     body={"comment": "Skill de geração de PDF exposta como MCP tool",
           "config": {"source_connection": {"name": f"connections/{CONNECTION_NAME}"},
                      "include_tool_selectors": []}})
print("MCP Service:", f"{CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## A4. Testar pelo endpoint do gateway
# MAGIC A resposta do gateway para um MCP em App vem como SSE (`event: message` + `data: {...}`).
# MAGIC
# MAGIC > Se retornar erro de autenticação (token do lab expirado), rode a célula A3 de novo para renovar
# MAGIC > a connection.

# COMMAND ----------

gw = f"{HOST}/ai-gateway/mcp-services/{CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}"


def gw_call(method, params=None, _id=1):
    r = requests.post(gw, headers=GWH,
                      data=json.dumps({"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}))
    return r.status_code, r.text


print("initialize:", *gw_call("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                              "clientInfo": {"name": "nb", "version": "1.0"}}))
print("\ntools/list:", *gw_call("tools/list", {}, _id=2))
print("\ntools/call gerar_pdf:", *gw_call("tools/call", {
    "name": "gerar_pdf",
    "arguments": {"titulo": "Relatório de Produção",
                  "conteudo": "Produção diária por mina e alertas de equipamento.",
                  "template": "relatorio_operacional"},
}, _id=3))

# COMMAND ----------

# MAGIC %md
# MAGIC ## A5. Testar no AI Playground
# MAGIC 1. AI Playground → escolher um modelo (ex.: `databricks-claude-sonnet-4-6`).
# MAGIC 2. Tools → Add tool → MCP → selecionar `larissa_xm.mcps.skill_pdf_mcp`.
# MAGIC 3. Pedir em linguagem natural, ex.: "Gere um PDF de sumário executivo com o título 'Status Semanal'
# MAGIC    resumindo a produção das minas."
# MAGIC 4. O modelo chama `gerar_pdf` e retorna o caminho do PDF no Volume.
# MAGIC
# MAGIC O PDF gerado fica em `/Volumes/larissa_xm/mcps/skill_artifacts/` (visível no Catalog Explorer).

# COMMAND ----------

# MAGIC %md
# MAGIC # Parte B — Função inteligente como UC Function + managed MCP (sem App)
# MAGIC Aqui a capacidade é uma **UC Function que embute um LLM** (via `ai_query`): um analista de operação
# MAGIC que interpreta a pergunta e devolve recomendações. O Databricks expõe a função automaticamente por
# MAGIC um MCP gerenciado — sem App, sem HTTP Connection e sem registrar MCP Service.
# MAGIC
# MAGIC **Comparação com uma skill do Genie Code:** a skill torna um *coding agent* inteligente via
# MAGIC instruções + arquivos de referência; a função inteligente **embute** a inteligência (o LLM) e fica
# MAGIC como um objeto **governado** do Unity Catalog, chamável por **qualquer** agente via gateway.

# COMMAND ----------

# MAGIC %md
# MAGIC ## B1. Criar a função inteligente (UC Function com `ai_query`)
# MAGIC A instrução vai no `COMMENT` (o agente lê para decidir usar); o "cérebro" é a chamada `ai_query` a
# MAGIC um Foundation Model. Ajuste o `LLM_ENDPOINT` para um modelo disponível no workspace.

# COMMAND ----------

LLM_ENDPOINT = "databricks-claude-sonnet-4-5"   # Foundation Model disponível no workspace

spark.sql(f"""
CREATE OR REPLACE FUNCTION {CATALOG}.{SCHEMA}.{FUNCTION_NAME}(pergunta STRING)
RETURNS STRING
COMMENT 'Analista inteligente de operação de mineração: interpreta a pergunta e devolve uma análise objetiva com recomendações acionáveis. Use para análises, diagnósticos e recomendações sobre produção, equipamentos e alertas.'
RETURN ai_query(
  '{LLM_ENDPOINT}',
  CONCAT('Você é um analista sênior de operações de mineração. Responda de forma objetiva, ',
         'técnica e acionável, em português, em no máximo 6 linhas. Pergunta: ', pergunta)
)
""")
print(f"Função inteligente criada: {CATALOG}.{SCHEMA}.{FUNCTION_NAME}  (LLM: {LLM_ENDPOINT})")

# COMMAND ----------

# MAGIC %md
# MAGIC ## B2. Controle de acesso por principal
# MAGIC A governança é o `GRANT EXECUTE` na função. Só quem tem `EXECUTE` (mais `USE` no catálogo/schema)
# MAGIC consegue invocá-la pelo managed MCP.

# COMMAND ----------

# Exemplo: liberar para um grupo específico.
# spark.sql(f"GRANT EXECUTE ON FUNCTION {CATALOG}.{SCHEMA}.{FUNCTION_NAME} TO `grupo_agentes`")
print("Controle: GRANT EXECUTE ON FUNCTION", f"{CATALOG}.{SCHEMA}.{FUNCTION_NAME}", "TO <principal>")

# COMMAND ----------

# MAGIC %md
# MAGIC ## B3. Testar pelo managed MCP de functions
# MAGIC Endpoint: `/api/2.0/mcp/functions/{catalog}/{schema}` — expõe todas as funções do schema como tools.
# MAGIC Pontos de atenção confirmados:
# MAGIC - o nome da tool é qualificado: `catalog__schema__função` (exatamente dois `__`);
# MAGIC - a resposta vem como JSON (não SSE), com o retorno da função em `rows[0][0]`.

# COMMAND ----------

mmcp = f"{HOST}/api/2.0/mcp/functions/{CATALOG}/{SCHEMA}"
tool_name = f"{CATALOG}__{SCHEMA}__{FUNCTION_NAME}"


def mmcp_call(method, params=None, _id=1):
    r = requests.post(mmcp, headers=GWH,
                      data=json.dumps({"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}))
    return r.status_code, r.json()

print("initialize:", *mmcp_call("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                               "clientInfo": {"name": "nb", "version": "1.0"}}))
print("\ntools/list:", *mmcp_call("tools/list", {}, _id=2))
sc, resp = mmcp_call("tools/call", {
    "name": tool_name,
    "arguments": {"pergunta": "A produção da MINA-NORTE caiu 15%. Quais hipóteses investigar?"},
}, _id=3)
print("\ntools/call:", sc)
rows = resp.get("result", {}).get("structuredContent", {}).get("rows")
print(rows[0][0] if rows else json.dumps(resp.get("error"), ensure_ascii=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## B4. Testar no AI Playground
# MAGIC 1. AI Playground → escolher um modelo.
# MAGIC 2. Tools → Add tool → Unity Catalog function → selecionar `larissa_xm.mcps.analisar_operacao`.
# MAGIC 3. Pedir: "A perfuratriz PER-007 está com vibração acima do limite. O que fazer?"
# MAGIC 4. O modelo chama a função (que por dentro consulta um LLM) e retorna a análise.

# COMMAND ----------

# MAGIC %md
# MAGIC # Consumo por um agente externo (produção)
# MAGIC Um agente externo chama o mesmo endpoint, autenticando com OAuth M2M de um service principal que
# MAGIC tenha permissão de execução:
# MAGIC
# MAGIC - **Opção A (App):** `POST {host}/ai-gateway/mcp-services/larissa_xm.mcps.skill_pdf_mcp`
# MAGIC   — service principal com `EXECUTE` no MCP Service; resposta SSE.
# MAGIC - **Opção B (UC Function):** `POST {host}/api/2.0/mcp/functions/larissa_xm/mcps`
# MAGIC   — service principal com `EXECUTE` na função; resposta JSON.
# MAGIC
# MAGIC Cabeçalhos: `Authorization: Bearer <token M2M>` e `Accept: application/json, text/event-stream`;
# MAGIC corpo em JSON-RPC (`initialize` / `tools/list` / `tools/call`). O cliente oficial
# MAGIC `DatabricksMCPClient(server_url=<endpoint>, workspace_client=w)` cuida do handshake e do parsing.

# COMMAND ----------

# MAGIC %md
# MAGIC # Governança (critérios do pilar)
# MAGIC | Critério | Opção A (App) | Opção B (UC Function) |
# MAGIC |---|---|---|
# MAGIC | Skill como capacidade governada | MCP Service `skill_pdf_mcp` (securable UC) | função `analisar_operacao` (securable UC) |
# MAGIC | Acesso por principal | `EXECUTE` no MCP Service | `EXECUTE` na função |
# MAGIC | Registro no UC | HTTP Connection + MCP Service | automático (função no schema) |
# MAGIC | Versionamento | código do App em Git folder | `CREATE OR REPLACE FUNCTION` versionada |
# MAGIC | Artefato de referência | Volume `skill_artifacts` | tabelas/volumes que a função consulta |
# MAGIC
# MAGIC ```sql
# MAGIC -- GRANT EXECUTE ON MCP SERVICE larissa_xm.mcps.skill_pdf_mcp TO `grupo_agentes`;
# MAGIC -- GRANT EXECUTE ON FUNCTION   larissa_xm.mcps.analisar_operacao TO `grupo_agentes`;
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC # Limpeza (opcional)

# COMMAND ----------

# Parte A
# rest("DELETE", f"/api/2.1/unity-catalog/mcp-services/{CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}")
# rest("DELETE", f"/api/2.1/unity-catalog/connections/{CONNECTION_NAME}")
# Parte B
# spark.sql(f"DROP FUNCTION IF EXISTS {CATALOG}.{SCHEMA}.{FUNCTION_NAME}")
