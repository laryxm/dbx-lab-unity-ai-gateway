# Databricks notebook source
# MAGIC %md
# MAGIC # 0.2 Framework de POC — Unity AI Gateway
# MAGIC
# MAGIC Este notebook enquadra a POC: **por que ela existe**, **como saber que deu certo** (critérios de
# MAGIC sucesso por pilar), **em que ordem executar** e **como a arquitetura se encaixa**. É o mapa que
# MAGIC transforma os notebooks de pilar (1.1, 2.x, 3.1, 4.1, 5.x) em uma prova de conceito reproduzível
# MAGIC por qualquer cliente — todos os valores são parametrizados no `0.0 Setup`.
# MAGIC
# MAGIC Use este notebook na abertura da POC (para alinhar escopo e critérios com o cliente) e no
# MAGIC fechamento (para validar, pilar a pilar, o que foi atingido). O acompanhamento do dia a dia,
# MAGIC com percentual de conclusão, fica no `0.1 Status e plano`.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Por que esta POC
# MAGIC Empresas adotando GenAI acumulam modelos (externos e servidos pela Databricks), servidores MCP e
# MAGIC agentes sem uma camada comum de **governança, controle e observabilidade**. O Unity AI Gateway é
# MAGIC essa camada: um ponto único onde modelos e tools são registrados no Unity Catalog, o consumo é
# MAGIC rastreado e atribuído, o tráfego é roteado e limitado, e o conteúdo passa por guardrails.
# MAGIC
# MAGIC A POC demonstra esse padrão de ponta a ponta em cinco pilares, sempre com dados sintéticos e
# MAGIC valores de cliente parametrizados.

# COMMAND ----------

# MAGIC %md-sandbox
# MAGIC <div style="font-family:'DM Sans',Arial,sans-serif;color:#1A2B3C;">
# MAGIC   <h3 style="margin:0 0 12px 0;color:#2C5A8C;">Os cinco pilares</h3>
# MAGIC   <table style="border-collapse:collapse;width:100%;max-width:820px;font-size:14px;">
# MAGIC     <tr style="background:#1B3A5B;color:#fff;">
# MAGIC       <th style="text-align:left;padding:8px 12px;">Pilar</th>
# MAGIC       <th style="text-align:left;padding:8px 12px;">Pergunta que responde</th>
# MAGIC       <th style="text-align:left;padding:8px 12px;">Notebook</th>
# MAGIC     </tr>
# MAGIC     <tr style="background:#F2F6FB;"><td style="padding:8px 12px;">1 · Observabilidade</td><td style="padding:8px 12px;">Quanto se consome, com que custo, e com que qualidade?</td><td style="padding:8px 12px;">1.1</td></tr>
# MAGIC     <tr><td style="padding:8px 12px;">2 · MCP</td><td style="padding:8px 12px;">Como registrar e governar tools (internas, externas, próprias)?</td><td style="padding:8px 12px;">2.1–2.6</td></tr>
# MAGIC     <tr style="background:#F2F6FB;"><td style="padding:8px 12px;">3 · Roteamento</td><td style="padding:8px 12px;">Como rotear entre modelos e impor limites?</td><td style="padding:8px 12px;">3.1</td></tr>
# MAGIC     <tr><td style="padding:8px 12px;">4 · Guardrails</td><td style="padding:8px 12px;">Como filtrar PII e conteúdo inseguro na borda?</td><td style="padding:8px 12px;">4.1</td></tr>
# MAGIC     <tr style="background:#F2F6FB;"><td style="padding:8px 12px;">5 · Skills</td><td style="padding:8px 12px;">Como expor e governar capacidades como tools?</td><td style="padding:8px 12px;">5.1–5.2</td></tr>
# MAGIC   </table>
# MAGIC </div>

# COMMAND ----------

# MAGIC %md
# MAGIC ## Critérios de sucesso (exit criteria) por pilar
# MAGIC O que precisa estar **demonstrado e validado** para considerar cada pilar concluído. Use como
# MAGIC checklist de aceite com o cliente.

# COMMAND ----------

# MAGIC %md-sandbox
# MAGIC <div style="font-family:'DM Sans',Arial,sans-serif;color:#1A2B3C;">
# MAGIC   <table style="border-collapse:collapse;width:100%;max-width:900px;font-size:13px;">
# MAGIC     <tr style="background:#1B3A5B;color:#fff;">
# MAGIC       <th style="text-align:left;padding:8px 12px;">Pilar</th>
# MAGIC       <th style="text-align:left;padding:8px 12px;">Critério de sucesso</th>
# MAGIC     </tr>
# MAGIC     <tr style="background:#F2F6FB;"><td style="padding:8px 12px;vertical-align:top;">1 · Observabilidade</td><td style="padding:8px 12px;">Modelo externo e Databricks sob o mesmo `ai_gateway`; usage + inference tables populando; custo atribuível por projeto (tag); hard spend cap via rate limit + alerta de gasto por grupo; agente rastreado com MLflow + LLM-as-judge.</td></tr>
# MAGIC     <tr><td style="padding:8px 12px;vertical-align:top;">2 · MCP</td><td style="padding:8px 12px;">MCP próprio (App), externo/SaaS e managed (UC Function) registrados e invocados por um agente; um exemplo de cada tipo de auth; conectividade de rede definida (quando privada); permissão por UC + service policy; payload logging habilitado.</td></tr>
# MAGIC     <tr style="background:#F2F6FB;"><td style="padding:8px 12px;vertical-align:top;">3 · Roteamento</td><td style="padding:8px 12px;">Endpoint multi-modelo com o modelo escolhido na chamada; rate limit por identidade; fallback entre modelos demonstrado; traffic split (A/B ou por custo) aplicado.</td></tr>
# MAGIC     <tr><td style="padding:8px 12px;vertical-align:top;">4 · Guardrails</td><td style="padding:8px 12px;">Config do endpoint inspecionada; guardrails nativos (PII/safety/keywords/topics) exercitados; reconhecedor próprio para PII fora da lista nativa (ex.: CPF) aplicado na borda.</td></tr>
# MAGIC     <tr style="background:#F2F6FB;"><td style="padding:8px 12px;vertical-align:top;">5 · Skills</td><td style="padding:8px 12px;">Skill que EXECUTA exposta como tool MCP (App ou UC Function) e consumida por agente; skill que INSTRUI publicada como UC Skill governada; acesso por `GRANT`; versionamento por Git folder.</td></tr>
# MAGIC   </table>
# MAGIC   <p style="font-size:12px;color:#6B7280;margin-top:8px;">Pilares 1, 2, 3 e 5 são obrigatórios; o Pilar 4 (guardrails) é recomendado, não pré-requisito de sucesso.</p>
# MAGIC </div>

# COMMAND ----------

# MAGIC %md
# MAGIC ## Checklist de execução da POC
# MAGIC
# MAGIC **Pré-requisitos (uma vez)**
# MAGIC - Workspace com Unity Catalog e Model Serving / AI Gateway habilitados.
# MAGIC - Privilégios no schema alvo: `CREATE CONNECTION`, `CREATE FUNCTION`, `EXECUTE`.
# MAGIC - Databricks CLI autenticado (deploy de Apps e criação de secrets).
# MAGIC - Chaves de provedor/API guardadas como **secrets** (nunca em texto plano).
# MAGIC - Para conectividade privada (2.5): CLI em nível de conta + acesso à subscription do ACI/AKS.
# MAGIC
# MAGIC **Sequência sugerida**
# MAGIC 1. `0.0 Setup` — preencher catálogo, schema, app, prefixos, secret scope.
# MAGIC 2. `1.1 Observabilidade` — registrar modelos, ligar tracking/inference, spend cap e tracing.
# MAGIC 3. `3.1 Roteamento` — multi-modelo, rate limit, fallback, traffic split.
# MAGIC 4. `2.1`→`2.6 MCP` — MCP próprio, externo, tipos de auth, rede, governança da tool.
# MAGIC 5. `4.1 Guardrails` — inspecionar, testar, aplicar guardrail próprio de PII.
# MAGIC 6. `5.1`/`5.2 Skills` — skill como tool MCP e UC Skill governada.
# MAGIC 7. `0.1 Status e plano` — validar os critérios de sucesso, pilar a pilar.
# MAGIC
# MAGIC **Validar ao vivo com o cliente**
# MAGIC - Um agente no AI Playground chamando uma tool MCP registrada.
# MAGIC - O dashboard/system tables mostrando consumo e custo do endpoint.
# MAGIC - Um guardrail bloqueando/mascarando PII em tempo real.
# MAGIC - `GRANT EXECUTE` controlando quem consegue invocar a tool.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Arquitetura
# MAGIC O agente (interno ou externo) fala **sempre** com o serving endpoint governado pelo AI Gateway. O
# MAGIC gateway aplica roteamento, rate limit e guardrails, chama o modelo (externo ou Databricks) e/ou as
# MAGIC tools MCP registradas no Unity Catalog, e emite usage/inference/payload logs para observabilidade.
# MAGIC
# MAGIC ```mermaid
# MAGIC flowchart LR
# MAGIC   A["Agente / App / Playground"] --> GW["Serving endpoint\n(Unity AI Gateway)"]
# MAGIC   GW -->|roteamento · rate limit · guardrails| MODELS["Modelos\n(externo + Databricks)"]
# MAGIC   GW -->|tools registradas| MCP["MCP Services / UC Functions"]
# MAGIC   MCP --- UC["Unity Catalog\n(EXECUTE · service policy)"]
# MAGIC   GW --> OBS["Observabilidade\n(usage · inference · payload · MLflow)"]
# MAGIC ```

# COMMAND ----------

# MAGIC %md-sandbox
# MAGIC <div style="font-family:'DM Sans',Arial,sans-serif;color:#1A2B3C;max-width:900px;">
# MAGIC   <table style="border-collapse:collapse;width:100%;font-size:13px;">
# MAGIC     <tr style="background:#1B3A5B;color:#fff;">
# MAGIC       <th style="text-align:left;padding:8px 12px;">Camada</th>
# MAGIC       <th style="text-align:left;padding:8px 12px;">Papel na arquitetura</th>
# MAGIC     </tr>
# MAGIC     <tr style="background:#F2F6FB;"><td style="padding:8px 12px;">Agente / App / Playground</td><td style="padding:8px 12px;">Consome o endpoint governado; nunca chama o modelo/MCP diretamente.</td></tr>
# MAGIC     <tr><td style="padding:8px 12px;">Serving endpoint (Gateway)</td><td style="padding:8px 12px;">Ponto único: roteamento, rate limit, fallback, traffic split e guardrails.</td></tr>
# MAGIC     <tr style="background:#F2F6FB;"><td style="padding:8px 12px;">Modelos</td><td style="padding:8px 12px;">Externos (Azure/OpenAI/Anthropic/Bedrock/Vertex) e Databricks (FMAPI) sob a mesma governança.</td></tr>
# MAGIC     <tr><td style="padding:8px 12px;">MCP Services / UC Functions</td><td style="padding:8px 12px;">Tools registradas no UC; acesso por `EXECUTE` + service policy.</td></tr>
# MAGIC     <tr style="background:#F2F6FB;"><td style="padding:8px 12px;">Observabilidade</td><td style="padding:8px 12px;">usage tracking, inference tables, payload logging e MLflow tracing/LLM-judge.</td></tr>
# MAGIC   </table>
# MAGIC </div>
