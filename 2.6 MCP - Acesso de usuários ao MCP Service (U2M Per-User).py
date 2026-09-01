# Databricks notebook source
# MAGIC %md
# MAGIC # 2.6 MCP - Acesso de usuários ao MCP Service (U2M Per-User)
# MAGIC
# MAGIC Num MCP registrado com **OAuth U2M Per-User**, o gateway propaga a identidade **de quem invoca**.
# MAGIC Isso é o que dá auditoria e autorização por usuário (on-behalf-of) fim a fim — mas cria pré-condições
# MAGIC por consumidor:
# MAGIC
# MAGIC 1. **Existir como identidade no workspace onde o gateway/MCP Service vivem.** A identidade é
# MAGIC    provisionada na **account** (via IdP/SCIM) e precisa estar **atribuída ao workspace**
# MAGIC    (*workspace assignment*). Estar apenas na account **não basta**.
# MAGIC 2. **Ter `EXECUTE` no MCP Service** — permissão específica do securable do serviço (não `USE CONNECTION`).
# MAGIC 3. **Ter `CAN_QUERY` nos serving endpoints** dos modelos que o agente usa via gateway (um modelo
# MAGIC    registrado no AI Gateway é um *serving endpoint*; a permissão de invocação é `CAN_QUERY`).
# MAGIC 4. **Autorizar uma vez** — na primeira invocação o usuário recebe `-32042 Login required` com um link;
# MAGIC    ao autorizar no IdP (SSO), o Databricks renova o token daquele usuário automaticamente.
# MAGIC
# MAGIC Este notebook executa o ciclo completo de habilitação de acesso, em ordem:
# MAGIC
# MAGIC 1. Cria o grupo de consumidores.
# MAGIC 2. Adiciona os e-mails informados como membros do grupo.
# MAGIC 3. Atribui o grupo ao workspace.
# MAGIC 4. Concede `EXECUTE` ao grupo no MCP Service.
# MAGIC 5. Concede `CAN_QUERY` ao grupo nos modelos publicados no gateway.
# MAGIC
# MAGIC Ao final, documenta o fluxo de primeiro login para orientar os usuários.
# MAGIC
# MAGIC **Dois planos de API.** Criar grupo, gerenciar membros e o *workspace assignment* são operações de
# MAGIC **account** (Account SCIM/Assignment API) e exigem credencial de account admin. Os grants de `EXECUTE`
# MAGIC e `CAN_QUERY` são de **workspace**. As seções estão separadas por plano; sem credencial de account, as
# MAGIC seções de account imprimem o passo equivalente para execução no Account Console.
# MAGIC
# MAGIC **Escala.** Conceder e atribuir por grupo é preferível a fazer por usuário: um novo consumidor entra
# MAGIC no grupo no IdP e herda o assignment e as permissões, sem passo adicional. Apenas o primeiro login
# MAGIC OAuth permanece individual, por ser uma autorização pessoal.

# COMMAND ----------

# MAGIC %run "./0.0 Setup - Parâmetros do cliente"

# COMMAND ----------

# MAGIC %md-sandbox
# MAGIC <div style="background:#EAF2FB;border-left:6px solid #4C82C3;border-radius:6px;padding:18px 22px;font-family:'DM Sans',Arial,sans-serif;color:#1A2B3C;">
# MAGIC   <h3 style="margin:0 0 10px 0;color:#2C5A8C;">Como funciona a autorização</h3>
# MAGIC   <p style="margin:0 0 12px 0;line-height:1.5;">
# MAGIC     No modo OAuth U2M Per-User, o gateway repassa ao servidor MCP a identidade do usuário que faz a
# MAGIC     chamada. Isso preserva a autoria e a auditoria por pessoa de ponta a ponta. Em contrapartida, a
# MAGIC     autorização é pessoal: cada usuário concede o consentimento uma única vez.
# MAGIC   </p>
# MAGIC   <p style="margin:0 0 8px 0;line-height:1.5;">
# MAGIC     O contato do usuário com o Databricks se resume a esse consentimento inicial. Não há uso de
# MAGIC     notebooks nem da interface de dados. O fluxo, na primeira chamada de cada usuário, é:
# MAGIC   </p>
# MAGIC   <ol style="margin:0 0 12px 22px;line-height:1.6;">
# MAGIC     <li>A invocação retorna <code>-32042 Login required</code> acompanhado de um link de autorização.</li>
# MAGIC     <li>O usuário abre o link e autentica pelo login corporativo (SSO).</li>
# MAGIC     <li>O usuário concede o consentimento uma vez.</li>
# MAGIC   </ol>
# MAGIC   <p style="margin:0;line-height:1.5;">
# MAGIC     A partir daí, as chamadas pela aplicação são transparentes e o token de cada usuário é renovado
# MAGIC     automaticamente pelo Databricks, sem nova intervenção.
# MAGIC   </p>
# MAGIC </div>

# COMMAND ----------

# MAGIC %md
# MAGIC ## Parâmetros
# MAGIC Edite os valores desta célula antes de executar. Cada parâmetro está comentado. O `WORKSPACE_ID` é o
# MAGIC identificador numérico do workspace onde o MCP Service está registrado (aparece na URL do workspace,
# MAGIC após `?o=`). As operações de account exigem credencial de account admin — o token é lido de um secret,
# MAGIC nunca escrito em texto.

# COMMAND ----------

# Identificador do MCP Service dentro de CATALOG.SCHEMA (definidos no Setup).
MCP_SERVICE_ID = "mcp_ontology"

# Serving endpoints dos modelos publicados no gateway (camada de serving → CAN_QUERY).
# Deixe a lista vazia se o agente usa apenas as tools do MCP.
MODEL_ENDPOINTS = [
    # "poc-modelo-externo",
    # "databricks-claude-sonnet-4-5",
]

# Modelos servidos pela Databricks (pay-per-token / Foundation Model API). No Unity Catalog
# aparecem como system.ai.<modelo>. O acesso governado é EXECUTE ON MODEL.
FMAPI_MODELS = [
    # "databricks-claude-sonnet-4-5",
    # "databricks-gpt-5",
]

# Modelos externos (ex.: Azure AI Foundry / OpenAI) registrados como Model Service no Unity
# Catalog (catalog.schema.service). O acesso governado é USE CATALOG + USE SCHEMA + EXECUTE ON SERVICE.
MODEL_SERVICES = [
    # "meu_catalog.meu_schema.foundry_gpt4o",
]

# Grupo de consumidores a ser criado e usado em todos os grants.
GRUPO = "consumidores-mcp"

# E-mails que entrarão no grupo (um por linha).
EMAILS = [
    # "usuario1@empresa.com",
    # "usuario2@empresa.com",
]

# Identidade de account (para criar grupo, membros e o workspace assignment).
ACCOUNT_HOST = "https://accounts.azuredatabricks.net"
ACCOUNT_ID   = ""   # Account ID
WORKSPACE_ID = ""   # Workspace ID numérico (o valor após ?o= na URL do workspace)

SERVICE_FULL_NAME = f"{CATALOG}.{SCHEMA}.{MCP_SERVICE_ID}"

# Token de account admin via secret (não colar em texto). Chave sugerida: "account_admin_token".
ACCOUNT_TOKEN = get_secret("account_admin_token")
HAS_ACCOUNT = bool(ACCOUNT_TOKEN and ACCOUNT_ID)
ACCOUNT_HEADERS = {"Authorization": f"Bearer {ACCOUNT_TOKEN}", "Content-Type": "application/json"}


def acct(method, path, body=None):
    """Chamada à Account API (SCIM/Assignment). Requer credencial de account admin."""
    r = requests.request(method, f"{ACCOUNT_HOST}/api/2.0/accounts/{ACCOUNT_ID}{path}",
                         headers=ACCOUNT_HEADERS,
                         data=json.dumps(body) if body is not None else None)
    if not r.ok:
        raise RuntimeError(f"{method} {path} -> {r.status_code}\n{r.text}")
    return r.json() if r.text else {}


print("MCP Service :", SERVICE_FULL_NAME)
print("Grupo       :", GRUPO)
print("Membros     :", len(EMAILS), "e-mail(s)")
print("Account API :", "disponível" if HAS_ACCOUNT else "AUSENTE (seções 1-3 só imprimirão o passo manual)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Criar o grupo e adicionar os membros (account)
# MAGIC Cria o grupo na account e adiciona cada e-mail como membro. Usuários que ainda não existirem na
# MAGIC account são criados (SCIM Users). Idempotente: se o grupo já existir, apenas garante os membros.

# COMMAND ----------

def _find_group(display_name):
    res = acct("GET", f"/scim/v2/Groups?filter=displayName eq \"{display_name}\"")
    grps = res.get("Resources", [])
    return grps[0] if grps else None


def _ensure_user(email):
    res = acct("GET", f"/scim/v2/Users?filter=userName eq \"{email}\"")
    users = res.get("Resources", [])
    if users:
        return users[0]["id"]
    created = acct("POST", "/scim/v2/Users",
                   body={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                         "userName": email, "emails": [{"value": email, "primary": True}]})
    return created["id"]


if HAS_ACCOUNT:
    grp = _find_group(GRUPO)
    if not grp:
        grp = acct("POST", "/scim/v2/Groups",
                   body={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"], "displayName": GRUPO})
        print("Grupo criado:", GRUPO, "| id:", grp["id"])
    else:
        print("Grupo já existe:", GRUPO, "| id:", grp["id"])
    GROUP_ID = grp["id"]

    ja_membros = {m.get("value") for m in grp.get("members", [])}
    ops = []
    for email in EMAILS:
        uid = _ensure_user(email)
        if uid not in ja_membros:
            ops.append({"value": uid})
    if ops:
        acct("PATCH", f"/scim/v2/Groups/{GROUP_ID}",
             body={"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                   "Operations": [{"op": "add", "path": "members", "value": ops}]})
    print(f"Membros garantidos: +{len(ops)} adicionado(s), {len(EMAILS)} no total desejado.")
else:
    print("Sem credencial de account. Peça ao account admin (Account Console → User management):")
    print(f"  1. Criar o grupo '{GRUPO}'.")
    print(f"  2. Adicionar os membros: {', '.join(EMAILS) if EMAILS else '(preencher lista)'}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Atribuir o grupo ao workspace (account)
# MAGIC O grupo precisa estar **atribuído ao workspace** onde o MCP Service vive — só assim os membros têm
# MAGIC identidade nele e conseguem invocar o gateway. Estar apenas na account não é suficiente.

# COMMAND ----------

if HAS_ACCOUNT and WORKSPACE_ID:
    acct("PUT", f"/workspaces/{WORKSPACE_ID}/permissionassignments/principals/{GROUP_ID}",
         body={"permissions": ["USER"]})
    print(f"Grupo '{GRUPO}' atribuído ao workspace {WORKSPACE_ID} (permissão USER).")
elif not WORKSPACE_ID:
    print("workspace_id não informado — preencha o widget para atribuir o grupo ao workspace.")
else:
    print("Sem credencial de account. Peça ao account admin (Account Console → Workspaces → Permissions):")
    print(f"  Atribuir o grupo '{GRUPO}' ao workspace com permissão de usuário.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Conferir o assignment (account)
# MAGIC Lista os principals atribuídos ao workspace para confirmar que o grupo consta.

# COMMAND ----------

if HAS_ACCOUNT and WORKSPACE_ID:
    res = acct("GET", f"/workspaces/{WORKSPACE_ID}/permissionassignments")
    achou = False
    for a in res.get("permission_assignments", []):
        p = a.get("principal", {})
        nome = p.get("group_name") or p.get("user_name") or p.get("service_principal_name") or p.get("principal_id")
        if p.get("group_name") == GRUPO:
            achou = True
            print(f"  {nome} -> {', '.join(a.get('permissions', []))}  [grupo alvo]")
    if not achou:
        print(f"Grupo '{GRUPO}' ainda não aparece nos assignments do workspace {WORKSPACE_ID}.")
else:
    print("Verificação de assignment requer credencial de account.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Confirmar que o MCP Service é U2M Per-User (workspace)
# MAGIC A propagação de identidade por usuário só ocorre quando a connection de origem é `OAUTH_U2M_MAPPING`.

# COMMAND ----------

svc = rest("GET", f"/api/2.1/unity-catalog/mcp-services/{SERVICE_FULL_NAME}")
conn_ref = svc.get("config", {}).get("source_connection", {}).get("name", "")
conn_name = conn_ref.split("/", 1)[1] if conn_ref.startswith("connections/") else conn_ref
print("MCP Service :", svc.get("full_name") or SERVICE_FULL_NAME)
print("Connection  :", conn_name)

conn = rest("GET", f"/api/2.1/unity-catalog/connections/{conn_name}")
cred = conn.get("credential_type")
print("credential_type:", cred)
if cred != "OAUTH_U2M_MAPPING":
    print("\nAVISO: connection não é OAUTH_U2M_MAPPING — a identidade do usuário NÃO é propagada. "
          "Reveja o registro (notebook 2.3) se o requisito for acesso/auditoria por usuário.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Conceder EXECUTE ao grupo no MCP Service (workspace)
# MAGIC Habilita todos os membros do grupo a invocar o serviço pelo gateway (cada um ainda faz o próprio
# MAGIC login OAuth na primeira chamada).

# COMMAND ----------

rest("PATCH", f"/api/2.1/unity-catalog/mcp-services/{SERVICE_FULL_NAME}/permissions",
     body={"changes": [{"principal": GRUPO, "add": ["EXECUTE"]}]})
print(f"EXECUTE concedido a '{GRUPO}' em {SERVICE_FULL_NAME}")

perms = rest("GET", f"/api/2.1/unity-catalog/mcp-services/{SERVICE_FULL_NAME}/permissions")
for a in perms.get("privilege_assignments", []):
    print(f"  {a.get('principal'):40s} -> {', '.join(a.get('privileges', []))}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Conceder acesso aos modelos do AI Gateway (workspace)
# MAGIC Com o Unity AI Gateway, o acesso aos modelos é governado pelo **Unity Catalog** — os modelos são
# MAGIC securables, não apenas serving endpoints. Há duas camadas, e o notebook cobre as duas:
# MAGIC
# MAGIC **Camada de serving (`CAN_QUERY` no endpoint).** Todo modelo publicado é servido por um serving
# MAGIC endpoint. `CAN_QUERY` habilita a invocação na camada de serving:
# MAGIC
# MAGIC | Permissão do endpoint | O que habilita | Para quem |
# MAGIC |---|---|---|
# MAGIC | `CAN_QUERY` | invocar o modelo (inferência) | consumidores do agente |
# MAGIC | `CAN_VIEW` | ver configuração/metadados | leitura, sem invocar |
# MAGIC | `CAN_MANAGE` | editar/excluir o endpoint e permissões | administradores |
# MAGIC
# MAGIC **Camada de governança (Unity Catalog).** É onde o gateway centraliza o controle de acesso:
# MAGIC
# MAGIC 1. **Modelos servidos pela Databricks (pay-per-token / Foundation Model API)** aparecem como
# MAGIC    `system.ai.<modelo>`. O privilégio de invocação é `EXECUTE ON MODEL`:
# MAGIC    `GRANT EXECUTE ON MODEL system.ai.<modelo> TO <grupo>`.
# MAGIC 2. **Modelos externos (ex.: Azure AI Foundry, OpenAI)** são registrados como **Model Service** no
# MAGIC    Unity Catalog (`catalog.schema.service`). A invocação exige `USE CATALOG` + `USE SCHEMA` no
# MAGIC    container e `EXECUTE ON SERVICE`.
# MAGIC
# MAGIC As três listas de parâmetros correspondem a essas camadas: `MODEL_ENDPOINTS` (serving),
# MAGIC `FMAPI_MODELS` (pay-per-token) e `MODEL_SERVICES` (externos). Preencha as que se aplicam.
# MAGIC
# MAGIC > Governança complementar (no próprio endpoint, notebook 3.1): *rate limits* por usuário, roteamento
# MAGIC > e fallback. Permissão de invocação e limite de uso são distintos — o grant habilita, o rate limit modula.

# COMMAND ----------

# MAGIC %md
# MAGIC ### 6a. Serving endpoints — CAN_QUERY

# COMMAND ----------

def _endpoint_id(name):
    return rest("GET", f"/api/2.0/serving-endpoints/{name}").get("id")


if not MODEL_ENDPOINTS:
    print("Nenhum serving endpoint informado — pule se o acesso for feito só pela camada de UC abaixo.")
for ep in MODEL_ENDPOINTS:
    try:
        eid = _endpoint_id(ep)
        rest("PATCH", f"/api/2.0/permissions/serving-endpoints/{eid}",
             body={"access_control_list": [{"group_name": GRUPO, "permission_level": "CAN_QUERY"}]})
        print(f"CAN_QUERY concedido a '{GRUPO}' no endpoint '{ep}'")
        acl = rest("GET", f"/api/2.0/permissions/serving-endpoints/{eid}").get("access_control_list", [])
        for a in acl:
            quem = a.get("group_name") or a.get("user_name") or a.get("service_principal_name")
            niveis = ", ".join(p.get("permission_level") for p in a.get("all_permissions", []))
            print(f"    {quem} -> {niveis}")
    except Exception as e:
        print(f"Falha no endpoint '{ep}': {str(e)[:200]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### 6b. Modelos pay-per-token — EXECUTE ON MODEL (system.ai.*)

# COMMAND ----------

if not FMAPI_MODELS:
    print("Nenhum modelo pay-per-token informado em FMAPI_MODELS.")
for modelo in FMAPI_MODELS:
    try:
        spark.sql(f"GRANT EXECUTE ON MODEL system.ai.`{modelo}` TO `{GRUPO}`")
        print(f"EXECUTE ON MODEL system.ai.{modelo} concedido a '{GRUPO}'")
    except Exception as e:
        print(f"Falha no modelo '{modelo}': {str(e)[:200]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### 6c. Modelos externos (Model Service) — USE CATALOG/SCHEMA + EXECUTE ON SERVICE

# COMMAND ----------

if not MODEL_SERVICES:
    print("Nenhum model service externo informado em MODEL_SERVICES.")
for svc_fqn in MODEL_SERVICES:
    try:
        cat, sch, _ = svc_fqn.split(".")
        spark.sql(f"GRANT USE CATALOG ON CATALOG `{cat}` TO `{GRUPO}`")
        spark.sql(f"GRANT USE SCHEMA ON SCHEMA `{cat}`.`{sch}` TO `{GRUPO}`")
        spark.sql(f"GRANT EXECUTE ON SERVICE `{cat}`.`{sch}`.`{svc_fqn.split('.')[2]}` TO `{GRUPO}`")
        print(f"USE CATALOG/SCHEMA + EXECUTE ON SERVICE {svc_fqn} concedidos a '{GRUPO}'")
    except ValueError:
        print(f"Formato inválido em MODEL_SERVICES: '{svc_fqn}' — use catalog.schema.service.")
    except Exception as e:
        print(f"Falha no model service '{svc_fqn}': {str(e)[:200]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Referências para operação
# MAGIC O fluxo de primeiro login está descrito na célula "Como funciona a autorização" no início do notebook.
# MAGIC As pré-condições habilitadas uma única vez (não por usuário) são:
# MAGIC
# MAGIC 1. Grupo criado, com membros e atribuído ao workspace (seções 1 a 3).
# MAGIC 2. `EXECUTE` do grupo no MCP Service (seção 5).
# MAGIC 3. Acesso do grupo aos modelos (seção 6): `CAN_QUERY` no serving endpoint, `EXECUTE ON MODEL` nos
# MAGIC    pay-per-token (`system.ai.*`) e `USE CATALOG`/`USE SCHEMA`/`EXECUTE ON SERVICE` nos externos.
# MAGIC 4. Redirect do Databricks na allowlist do IdP (valor abaixo).
# MAGIC
# MAGIC A célula seguinte imprime o redirect a manter na allowlist e o endpoint do gateway para este serviço.

# COMMAND ----------

print("Redirect a manter na allowlist do IdP (app registration):")
print(" ", REDIRECT_URI)
print()
print("Endpoint do gateway para este MCP Service:")
print(" ", gateway_mcp_url(SERVICE_FULL_NAME))
