# Databricks notebook source
# MAGIC %md
# MAGIC # 3.1 AI Gateway — Roteamento de modelos e controles
# MAGIC
# MAGIC Pilar 3. Configura um endpoint que atende múltiplos modelos sob a mesma governança e aplica os
# MAGIC controles do gateway: rate limit por identidade, fallback entre modelos e divisão de tráfego.
# MAGIC
# MAGIC ## Conceito
# MAGIC Um serving endpoint pode ter mais de um *served entity*. Sobre ele, o bloco `ai_gateway` aplica:
# MAGIC - **rate limits** por usuário ou por endpoint;
# MAGIC - **fallback** — em caso de falha de um modelo, tenta o próximo (modelos externos);
# MAGIC - **traffic split** — percentual de tráfego por modelo (A/B, canário, roteamento por custo).

# COMMAND ----------

# MAGIC %run "./0.0 Setup - Parâmetros do cliente"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Parâmetros do notebook

# COMMAND ----------

dbutils.widgets.text("endpoint", "poc-roteamento", "Endpoint (multi-modelo)")
EP = dbutils.widgets.get("endpoint")

# Nomes lógicos dos served entities (o chamador referencia por estes nomes).
MODELO_A = "gpt-4o"          # ex.: modelo externo (Azure OpenAI / Foundry)
MODELO_B = "gpt-4o-mini"     # ex.: um segundo modelo externo, mais barato
print(f"Endpoint: {EP} | modelos: {MODELO_A}, {MODELO_B}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 1 — Endpoint com múltiplos modelos + divisão de tráfego
# MAGIC Dois *served entities* no mesmo endpoint. `traffic_config.routes` define o percentual de cada um.
# MAGIC Para 100% num modelo e escolha explícita na chamada, ajuste os percentuais (ex.: 100/0).

# COMMAND ----------

SREF = lambda k: f"{{{{secrets/{SECRET_SCOPE}/{k}}}}}"

config = {
    "served_entities": [
        {"name": MODELO_A, "external_model": {
            "name": MODELO_A, "provider": "openai", "task": "llm/v1/chat",
            "openai_config": {"openai_api_key": SREF("modelo_a_api_key")}}},
        {"name": MODELO_B, "external_model": {
            "name": MODELO_B, "provider": "openai", "task": "llm/v1/chat",
            "openai_config": {"openai_api_key": SREF("modelo_b_api_key")}}},
    ],
    "traffic_config": {"routes": [
        {"served_model_name": MODELO_A, "traffic_percentage": 50},
        {"served_model_name": MODELO_B, "traffic_percentage": 50},
    ]},
}
print(json.dumps(config, indent=2).replace(SREF("modelo_a_api_key"), "***").replace(SREF("modelo_b_api_key"), "***"))

existe = any(e.get("name") == EP for e in rest("GET", "/api/2.0/serving-endpoints").get("endpoints", []))
# if existe:
#     rest("PUT", f"/api/2.0/serving-endpoints/{EP}/config", body=config)
# else:
#     rest("POST", "/api/2.0/serving-endpoints", body={"name": EP, "config": config})
print("Endpoint já existe:", existe, "— descomente para aplicar.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 2 — Rate limit por identidade
# MAGIC Limita chamadas por período. `key` = `user` (por identidade UC) ou `endpoint` (agregado).
# MAGIC O `PUT .../ai-gateway` substitui o bloco inteiro — envie sempre a configuração completa.
# MAGIC
# MAGIC > Confirme na UI/doc vigente os valores aceitos de `renewal_period` (minuto/hora) e se há limite por
# MAGIC > tokens além de por requisições — a POC configurou requisições e tokens, por minuto e por hora.

# COMMAND ----------

gw = {
    "rate_limits": [
        {"calls": 10, "renewal_period": "minute", "key": "user"},
    ],
    "usage_tracking_config": {"enabled": True},
}
# rest("PUT", f"/api/2.0/serving-endpoints/{EP}/ai-gateway", body=gw)
print("Rate limit pronto para aplicar:", json.dumps(gw["rate_limits"]))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 3 — Fallback entre modelos
# MAGIC Para modelos externos, o fallback tenta o próximo *served entity* se o primeiro falhar. Ligado no
# MAGIC bloco `ai_gateway`.

# COMMAND ----------

gw_fb = {
    "rate_limits": [{"calls": 10, "renewal_period": "minute", "key": "user"}],
    "usage_tracking_config": {"enabled": True},
    "fallback_config": {"enabled": True},
}
# rest("PUT", f"/api/2.0/serving-endpoints/{EP}/ai-gateway", body=gw_fb)
print("Fallback pronto para aplicar (fallback_config.enabled = True).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 4 — Testar o roteamento

# COMMAND ----------

def chamar(texto, model=None):
    body = {"messages": [{"role": "user", "content": texto}], "max_tokens": 40}
    if model:
        body["model"] = model  # seleção explícita do modelo, quando suportado pelo endpoint
    r = requests.post(f"{HOST}/serving-endpoints/{EP}/invocations", headers=HEADERS, data=json.dumps(body))
    return r.status_code, (r.json().get("choices", [{}])[0].get("message", {}).get("content", "")[:120]
                           if r.status_code == 200 else r.text[:160])

# print(chamar("Diga apenas: ok."))
print("Descomente para testar o endpoint", EP)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 5 — Exercitar o rate limit
# MAGIC Dispara chamadas acima do limite e observa o retorno de bloqueio por limite de taxa (HTTP 429).

# COMMAND ----------

# for i in range(15):
#     sc, _ = chamar("ping")
#     print(i, "->", sc, "(429 = rate limited)")
print("Descomente para disparar o rate limit no endpoint", EP)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Notas
# MAGIC - **Traffic split** (Passo 1): use percentuais para A/B, canário ou roteamento por custo (mais
# MAGIC   tráfego no modelo mais barato).
# MAGIC - **Smart routing por custo** é uma capacidade mais recente do gateway — confirmar disponibilidade
# MAGIC   (GA/recomendações) na doc vigente no momento da POC.
# MAGIC - Rate limit por identidade UC é onde a governança nativa se destaca (limite por usuário, não por
# MAGIC   chave virtual).
# MAGIC
# MAGIC ## Checklist do pilar coberto aqui
# MAGIC - Endpoint único com múltiplos modelos → Passo 1
# MAGIC - Rate limit por identidade → Passo 2
# MAGIC - Fallback entre modelos → Passo 3
# MAGIC - Traffic split / roteamento por custo → Passo 1 + Notas
