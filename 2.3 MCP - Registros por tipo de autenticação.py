# Databricks notebook source
# MAGIC %md
# MAGIC # 2.3 MCP - Registro por tipo de autenticação
# MAGIC
# MAGIC Ao registrar um MCP externo, a HTTP Connection define **como o gateway se autentica no servidor** —
# MAGIC e isso determina, entre outras coisas, **qual identidade chega ao MCP**. Este notebook mostra **um
# MAGIC exemplo de cada tipo**, numerado, com uma explicação curta e o mesmo passo de registro/teste.
# MAGIC
# MAGIC | Tipo | Como autentica | Identidade no MCP | Quando usar |
# MAGIC |---|---|---|---|
# MAGIC | Bearer token | header `Authorization: Bearer <token>` fixo | única (a do token) | API key de serviço (ex.: SlideSpeak) |
# MAGIC | OAuth M2M | client credentials (client_id/secret) | única (o service principal) | automação server-to-server |
# MAGIC | OAuth U2M Shared | um usuário autoriza uma vez; credencial compartilhada | única (quem autorizou) | serviço sem client credentials, identidade única aceitável |
# MAGIC | OAuth U2M Per-User | cada usuário autoriza individualmente | **a do usuário que invoca** | acesso/auditoria por usuário (on-behalf-of) |
# MAGIC | DCR | Databricks registra o client OAuth automaticamente (RFC 7591) | por usuário | provedor que suporta Dynamic Client Registration |
# MAGIC
# MAGIC **Qual escolher, na prática:**
# MAGIC - MCP hospedado em **Databricks App** → **M2M** (exemplo 2).
# MAGIC - MCP próprio com **IdP corporativo** (ex.: ACI + Entra ID — o caso da Ero) → **U2M Per-User** (exemplo 4).
# MAGIC - MCP **SaaS** cujo provedor suporta registro dinâmico (ex.: HuggingFace) → **DCR** (exemplo 5).

# COMMAND ----------

# MAGIC %run "./0.0 Setup - Parâmetros do cliente"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Parâmetros e helper de registro
# MAGIC Um único helper cria a HTTP Connection, cria o MCP Service e faz `tools/list`. Cada exemplo abaixo
# MAGIC apenas monta as `options` do seu tipo e chama esse helper — assim o passo de registro é sempre igual.

# COMMAND ----------

dbutils.widgets.text("mcp_host", "https://SEU-MCP.exemplo.com", "Host do MCP externo")
dbutils.widgets.text("mcp_base_path", "/mcp", "Base path do MCP")
MCP_HOST = dbutils.widgets.get("mcp_host").rstrip("/")
MCP_BASE_PATH = dbutils.widgets.get("mcp_base_path")

BASE_OPTS = {"host": MCP_HOST, "port": "443", "base_path": MCP_BASE_PATH, "is_mcp_connection": "true"}


def registrar_e_testar(options, comment, svc_id):
    """Cria (ou recria) a connection + MCP Service com as `options` dadas e faz tools/list."""
    conn_name = f"{CONN_PREFIX}{svc_id}_conn"
    for c in rest("GET", "/api/2.1/unity-catalog/connections").get("connections", []):
        if c.get("name") == conn_name:
            rest("DELETE", f"/api/2.1/unity-catalog/connections/{conn_name}")
    conn = rest("POST", "/api/2.1/unity-catalog/connections",
                body={"name": conn_name, "connection_type": "HTTP", "comment": comment, "options": options})
    print("connection:", conn.get("name"), "| credential_type:", conn.get("credential_type"))

    try:
        rest("DELETE", f"/api/2.1/unity-catalog/mcp-services/{CATALOG}.{SCHEMA}.{svc_id}")
    except Exception:
        pass
    rest("POST", "/api/2.1/unity-catalog/mcp-services",
         params={"parent": f"schemas/{CATALOG}.{SCHEMA}", "mcp_service_id": svc_id},
         body={"comment": comment, "config": {"source_connection": {"name": f"connections/{conn_name}"},
                                              "include_tool_selectors": []}})
    sc, resp = mcp_call(gateway_mcp_url(f"{CATALOG}.{SCHEMA}.{svc_id}"), "tools/list", _id=2)
    tools = resp.get("result", {}).get("tools")
    print("MCP Service:", f"{CATALOG}.{SCHEMA}.{svc_id}")
    print("tools/list:", [t["name"] for t in tools] if tools else resp.get("error"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Bearer token
# MAGIC O gateway envia um header `Authorization: Bearer <token>` fixo. É o modo de uma **API key de
# MAGIC serviço** (ex.: SlideSpeak). Simples, mas a identidade é única (a do token) e o token não se renova.
# MAGIC Guarde a chave em um **Databricks Secret**, nunca em texto plano.

# COMMAND ----------

options_bearer = {
    **BASE_OPTS,
    "bearer_token": get_secret("mcp_api_key"),   # chave no Databricks Secret (scope do setup)
}
# registrar_e_testar(options_bearer, "MCP externo - Bearer", "mcp_bearer")
print("Bearer -> options prontas (preencha o secret 'mcp_api_key' e descomente a linha acima).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. OAuth M2M (service principal)
# MAGIC Fluxo *client credentials*: o gateway usa um **service principal** (client_id + secret) para obter
# MAGIC tokens. Não há usuário nem login — as chamadas correm como o service principal, e o Databricks
# MAGIC renova o token sozinho. É o modo indicado para **automação** e para um **MCP em Databricks App**
# MAGIC (o SP precisa de `CAN USE` no App). O `client_secret` vem de um Databricks Secret.

# COMMAND ----------

options_m2m = {
    **BASE_OPTS,
    "token_endpoint": f"{HOST}/oidc/v1/token",        # p/ App Databricks; para outro IdP, o token endpoint dele
    "client_id": "<client_id do service principal>",
    "client_secret": get_secret("sp_oauth_secret"),   # secret do SP no Databricks Secret
    "oauth_scope": "all-apis",
}
# registrar_e_testar(options_m2m, "MCP externo - OAuth M2M", "mcp_m2m")
print("M2M -> preencha client_id do SP + secret 'sp_oauth_secret' e descomente para registrar.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. OAuth U2M Shared
# MAGIC Um usuário autoriza **uma vez** (fluxo authorization code) e essa credencial passa a valer **para
# MAGIC todos os chamadores** da connection. Útil quando o provedor não oferece client credentials e uma
# MAGIC identidade única é aceitável. Menos granularidade de auditoria do que o Per-User.

# COMMAND ----------

options_u2m_shared = {
    **BASE_OPTS,
    "authorization_endpoint": "<AUTHORIZE do provedor>",   # ex.: https://login.microsoftonline.com/<tenant>/oauth2/v2.0/authorize
    "token_endpoint": "<TOKEN do provedor>",
    "client_id": "<client_id do app OAuth>",
    "client_secret": get_secret("mcp_oauth_secret"),
    "oauth_scope": "openid profile offline_access",
}
# registrar_e_testar(options_u2m_shared, "MCP externo - U2M Shared", "mcp_u2m_shared")
print("U2M Shared -> preencha endpoints/client do provedor. O modo Shared é escolhido na criação (UI).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. OAuth U2M Per-User  ← o caso da Ero (MCP próprio + Entra ID)
# MAGIC **Cada usuário autoriza individualmente** e o Databricks guarda/renova o token **por usuário**,
# MAGIC injetando o token do usuário correto a cada chamada. É o único modo que **propaga a identidade de
# MAGIC quem invoca** (on-behalf-of, auditoria por usuário). Ideal quando o MCP é protegido por um IdP
# MAGIC corporativo (Entra ID). As `options` são as mesmas do U2M Shared; o **modo Per-User** é escolhido na
# MAGIC criação (assistente Catalog → Connections). Na 1ª chamada de cada usuário vem `-32042 Login required`
# MAGIC (ver seção "Consentimento" abaixo).

# COMMAND ----------

# Exemplo mapeado para Microsoft Entra ID (tenant do cliente):
TENANT = "<tenant-id>"
options_u2m_peruser = {
    **BASE_OPTS,
    "authorization_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize",
    "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
    "client_id": "<client_id do app registration no Entra>",
    "client_secret": get_secret("entra_oauth_secret"),
    "oauth_scope": "api://<app-id>/.default offline_access",
}
# registrar_e_testar(options_u2m_peruser, "MCP externo - U2M Per-User (Entra)", "mcp_u2m_peruser")
print("U2M Per-User -> preencha tenant/app do Entra; allowliste o redirect (abaixo) no app registration.")
print("Redirect a allowlistar no provedor:", REDIRECT_URI)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. DCR (Dynamic Client Registration)
# MAGIC O Databricks **registra o client OAuth automaticamente** no provedor (RFC 7591) — você não cria um
# MAGIC app OAuth à mão. Só funciona se o provedor suportar DCR (ex.: HuggingFace). Informa-se os endpoints
# MAGIC e os scopes; o Databricks cuida do resto e conduz o consentimento por usuário.

# COMMAND ----------

options_dcr = {
    **BASE_OPTS,
    "authorization_endpoint": "<AUTHORIZE do provedor>",   # ex.: https://huggingface.co/oauth/authorize
    "token_endpoint": "<TOKEN do provedor>",
    "oauth_scope": "openid profile",                       # sem client_id: o Databricks registra o client
}
# registrar_e_testar(options_dcr, "MCP externo - DCR", "mcp_dcr")
print("DCR -> preencha os endpoints do provedor (que suporte DCR). Sem client_id: o Databricks registra.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Consentimento e reautenticação (U2M / DCR)
# MAGIC No primeiro uso por usuário, o gateway responde `-32042 Login required` (ou "Login required" no
# MAGIC Playground) com um link. Para autenticar: **abra o link → autorize no provedor → repita a chamada**.
# MAGIC A partir daí o Databricks guarda e **renova** o token; o login só reaparece se a autorização for
# MAGIC revogada. Sendo o IdP o **Entra**, esse consentimento é o **SSO da empresa** — uma vez por usuário,
# MAGIC transparente. Pré-requisitos no provedor: registrar `authorization_endpoint`/`token_endpoint`,
# MAGIC o `client_id`/`client_secret` do app OAuth, **allowlistar o redirect** `{HOST}/login/oauth/http.html`
# MAGIC e conceder os scopes.
# MAGIC
# MAGIC > **Atenção:** um `bearer_token` estático (token de usuário) **não se renova** — expira (~1h) e volta
# MAGIC > a pedir login. Para estabilidade, use OAuth (M2M ou U2M), não token estático.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Nota sobre MCP em Databricks App
# MAGIC Um App Databricks é protegido pelo OAuth do workspace. **U2M usando o client do próprio App não
# MAGIC funciona** — o redirect da connection não fica registrado nesse client (exigiria uma *custom OAuth
# MAGIC app integration*, de nível de conta). Por isso, para um MCP em App, o caminho adequado é **M2M**
# MAGIC (exemplo 2) com um service principal que tenha `CAN USE` no App. Ver notebooks 2.2 e 5.1.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Limpeza (opcional)

# COMMAND ----------

# for svc in ["mcp_bearer","mcp_m2m","mcp_u2m_shared","mcp_u2m_peruser","mcp_dcr"]:
#     try: rest("DELETE", f"/api/2.1/unity-catalog/mcp-services/{CATALOG}.{SCHEMA}.{svc}")
#     except Exception: pass
#     try: rest("DELETE", f"/api/2.1/unity-catalog/connections/{CONN_PREFIX}{svc}_conn")
#     except Exception: pass
