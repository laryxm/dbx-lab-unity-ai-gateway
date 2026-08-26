# Databricks notebook source
# MAGIC %md
# MAGIC # 1.1 Observabilidade — Registro de modelos e rastreamento de uso e custo
# MAGIC
# MAGIC Pilar 1. Registra um modelo externo e um modelo servido pela Databricks sob a mesma governança do
# MAGIC Unity AI Gateway, ativa o rastreamento de uso, custo e o log de payload (inference tables), e mostra
# MAGIC como consultar tudo isso.
# MAGIC
# MAGIC ## Conceito
# MAGIC O AI Gateway atua sobre um **serving endpoint**. Um mesmo padrão de governança vale para:
# MAGIC - **modelos externos** (Azure OpenAI/Foundry, OpenAI, Anthropic, Bedrock, Vertex) registrados como endpoint;
# MAGIC - **modelos servidos pela Databricks** (Foundation Model API).
# MAGIC
# MAGIC Controles de observabilidade configurados no bloco `ai_gateway` do endpoint:
# MAGIC - **usage tracking** — requisições, tokens de entrada/saída, status, atribuídos em system tables;
# MAGIC - **inference tables** — todo o conteúdo de entrada e saída persistido como Delta governado.

# COMMAND ----------

# MAGIC %run "./0.0 Setup - Parâmetros do cliente"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Parâmetros do notebook
# MAGIC O provedor e o endpoint são específicos deste notebook. A **API key do provedor** deve estar em um
# MAGIC Databricks Secret (não em texto plano) — o endpoint a referencia por `{{secrets/scope/chave}}`.

# COMMAND ----------

dbutils.widgets.text("endpoint_externo", "poc-modelo-externo", "Endpoint do modelo externo")
dbutils.widgets.dropdown("provider", "azure-openai", ["azure-openai", "openai", "anthropic", "amazon-bedrock"], "Provedor")
dbutils.widgets.text("secret_key", "modelo_externo_api_key", f"Chave no scope '{SECRET_SCOPE}'")
# Específicos de Azure OpenAI / Foundry:
dbutils.widgets.text("azure_base", "https://SEU-RECURSO.openai.azure.com/", "Azure OpenAI base URL")
dbutils.widgets.text("azure_deployment", "gpt-4o", "Azure deployment name")
dbutils.widgets.text("azure_api_version", "2024-08-01-preview", "Azure API version")

EP = dbutils.widgets.get("endpoint_externo")
PROVIDER = dbutils.widgets.get("provider")
SECRET_KEY = dbutils.widgets.get("secret_key")
print(f"Endpoint: {EP} | provedor: {PROVIDER} | key: secret({SECRET_SCOPE}/{SECRET_KEY})")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 0 — Guardar a API key do provedor como Secret (uma vez)
# MAGIC No terminal local (fora do notebook):
# MAGIC ```bash
# MAGIC databricks --profile <perfil> secrets create-scope mcp_poc      # se ainda não existir
# MAGIC databricks --profile <perfil> secrets put-secret mcp_poc modelo_externo_api_key
# MAGIC ```
# MAGIC O endpoint referencia o secret por `{{secrets/mcp_poc/modelo_externo_api_key}}` — a chave nunca
# MAGIC aparece em texto plano na configuração.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 1 — Registrar o modelo externo (com usage tracking + inference table já ativos)
# MAGIC O corpo abaixo cria o endpoint do modelo externo e, no mesmo passo, liga o rastreamento de uso e a
# MAGIC inference table. Ajuste o bloco `external_model` conforme o provedor.

# COMMAND ----------

SECRET_REF = f"{{{{secrets/{SECRET_SCOPE}/{SECRET_KEY}}}}}"  # vira {{secrets/scope/chave}}

# bloco external_model por provedor
if PROVIDER == "azure-openai":
    external_model = {
        "name": dbutils.widgets.get("azure_deployment"),
        "provider": "openai",
        "task": "llm/v1/chat",
        "openai_config": {
            "openai_api_type": "azure",
            "openai_api_key": SECRET_REF,
            "openai_api_base": dbutils.widgets.get("azure_base"),
            "openai_deployment_name": dbutils.widgets.get("azure_deployment"),
            "openai_api_version": dbutils.widgets.get("azure_api_version"),
        },
    }
elif PROVIDER == "openai":
    external_model = {"name": "gpt-4o", "provider": "openai", "task": "llm/v1/chat",
                      "openai_config": {"openai_api_key": SECRET_REF}}
elif PROVIDER == "anthropic":
    external_model = {"name": "claude-3-5-sonnet", "provider": "anthropic", "task": "llm/v1/chat",
                      "anthropic_config": {"anthropic_api_key": SECRET_REF}}
else:  # amazon-bedrock
    external_model = {"name": "claude-3-5-sonnet", "provider": "amazon-bedrock", "task": "llm/v1/chat",
                      "amazon_bedrock_config": {"aws_region": "us-east-1",
                                                "bedrock_access_key_id": SECRET_REF,
                                                "bedrock_secret_access_key": f"{{{{secrets/{SECRET_SCOPE}/bedrock_secret}}}}"}}

body = {
    "name": EP,
    "config": {"served_entities": [{"name": external_model["name"], "external_model": external_model}]},
    "ai_gateway": {
        "usage_tracking_config": {"enabled": True},
        "inference_table_config": {
            "enabled": True, "catalog_name": CATALOG, "schema_name": SCHEMA,
            "table_name_prefix": EP.replace("-", "_"),
        },
    },
}

# cria (ou atualiza) o endpoint. Descomente para aplicar quando a secret estiver configurada.
existe = any(e.get("name") == EP for e in rest("GET", "/api/2.0/serving-endpoints").get("endpoints", []))
print("Endpoint já existe:", existe)
print(json.dumps({**body, "config": {"served_entities": [{"name": external_model["name"],
      "external_model": {**external_model, **{k: "***" for k in external_model if k.endswith("_config")}}}]}}, indent=2)[:900])
# r = rest("POST" if not existe else "PUT",
#          f"/api/2.0/serving-endpoints" if not existe else f"/api/2.0/serving-endpoints/{EP}/config",
#          body=body if not existe else body["config"])
# print("endpoint:", r.get("name"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 2 — Ativar/ajustar observabilidade num endpoint já existente
# MAGIC Se o endpoint já existe (ou é um modelo servido pela Databricks que você controla), ligue o
# MAGIC rastreamento pelo bloco `ai_gateway` via `PUT .../ai-gateway`.

# COMMAND ----------

gw_obs = {
    "usage_tracking_config": {"enabled": True},
    "inference_table_config": {
        "enabled": True, "catalog_name": CATALOG, "schema_name": SCHEMA,
        "table_name_prefix": EP.replace("-", "_"),
    },
}
# rr = rest("PUT", f"/api/2.0/serving-endpoints/{EP}/ai-gateway", body=gw_obs)
# print(json.dumps(rr, indent=2)[:600])
print("Config de observabilidade pronta para aplicar em:", EP)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 3 — Testar o endpoint (gera tráfego para os logs)

# COMMAND ----------

def perguntar(endpoint, texto):
    r = requests.post(f"{HOST}/serving-endpoints/{endpoint}/invocations", headers=HEADERS,
                      data=json.dumps({"messages": [{"role": "user", "content": texto}], "max_tokens": 80}))
    print("status:", r.status_code)
    if r.status_code == 200:
        print(r.json().get("choices", [{}])[0].get("message", {}).get("content", "")[:300])
    else:
        print(r.text[:300])

# perguntar(EP, "Resuma em uma frase o que é governança de modelos de IA.")
print("Descomente para testar o endpoint", EP, "após criá-lo.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 4 — Ler os logs de uso e custo (system tables)

# COMMAND ----------

try:
    df = spark.sql(f"""
        SELECT requests, served_entity_name, request_time, request_status
        FROM system.serving.endpoint_usage
        WHERE served_entity_name LIKE '%{EP}%'
        ORDER BY request_time DESC LIMIT 20
    """)
    display(df)
except Exception as e:
    print("Ajuste ao schema disponível. Colunas típicas: request_time, served_entity_name, requests,")
    print("input_token_count, output_token_count, request_status. Detalhe:", str(e)[:160])

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 5 — Ler o conteúdo (inference table)
# MAGIC A inference table foi criada em `{CATALOG}.{SCHEMA}.<prefixo>_payload` (o nome exato aparece na aba
# MAGIC AI Gateway do endpoint).

# COMMAND ----------

tabela = f"{CATALOG}.{SCHEMA}.{EP.replace('-', '_')}_payload"
try:
    display(spark.sql(f"SELECT * FROM {tabela} ORDER BY 1 DESC LIMIT 10"))
except Exception as e:
    print(f"Tabela {tabela} ainda sem dados ou com outro sufixo. Confira o nome na aba AI Gateway. {str(e)[:120]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 6 — Governança: tag de projeto no endpoint
# MAGIC Aplicar uma tag permite atribuir uso e custo por iniciativa em painéis cross-workspace.

# COMMAND ----------

# rest("PATCH", f"/api/2.0/serving-endpoints/{EP}/tags",
#      body={"add_tags": [{"key": "projeto", "value": "poc-ai-gateway"}]})
print("Tag de projeto pronta para aplicar em:", EP)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Checklist do pilar coberto aqui
# MAGIC - Registro de modelo externo → Passo 1
# MAGIC - Registro de modelo servido pela Databricks → Passo 2 (mesmo bloco `ai_gateway`)
# MAGIC - Usage tracking e spend → Passos 1/2 e 4
# MAGIC - Inference tables → Passos 1/2 e 5
# MAGIC - Tag de governança → Passo 6
