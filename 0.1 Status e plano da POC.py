# Databricks notebook source
# MAGIC %md
# MAGIC # 0.1 Status e plano da POC — Unity AI Gateway
# MAGIC
# MAGIC Painel de acompanhamento da prova de conceito. Para cada pilar: o que está concluído, o que está em
# MAGIC curso e o que falta — com a **ação instruída** e o notebook correspondente para concluir cada item.
# MAGIC
# MAGIC **Legenda:** ✓ feito e validado · » em curso · ○ a fazer
# MAGIC
# MAGIC **Escopo:** Skills entrou como Pilar 5 (novo) e Guardrails (Pilar 4) é opcional — não é pré-requisito
# MAGIC de sucesso, então fica fora do cálculo de conclusão. O percentual considera os itens dos pilares
# MAGIC obrigatórios (1, 2, 3 e 5).

# COMMAND ----------

# MAGIC %md-sandbox
# MAGIC <div style="font-family:'DM Sans',Arial,sans-serif;color:#1A2B3C;">
# MAGIC   <h3 style="margin:0 0 12px 0;color:#2C5A8C;">Onde estamos — 42% dos critérios obrigatórios concluídos</h3>
# MAGIC   <table style="border-collapse:collapse;width:100%;max-width:640px;font-size:14px;">
# MAGIC     <tr style="background:#1B3A5B;color:#fff;">
# MAGIC       <th style="text-align:left;padding:8px 12px;">Pilar</th>
# MAGIC       <th style="text-align:left;padding:8px 12px;">Conclusão</th>
# MAGIC       <th style="text-align:left;padding:8px 12px;">Situação</th>
# MAGIC     </tr>
# MAGIC     <tr style="background:#F2F6FB;"><td style="padding:8px 12px;">1 · Observabilidade</td><td style="padding:8px 12px;">60%</td><td style="padding:8px 12px;">em curso</td></tr>
# MAGIC     <tr><td style="padding:8px 12px;">2 · MCP</td><td style="padding:8px 12px;">17%</td><td style="padding:8px 12px;">prioridade</td></tr>
# MAGIC     <tr style="background:#F2F6FB;"><td style="padding:8px 12px;">3 · AI Gateway</td><td style="padding:8px 12px;">57%</td><td style="padding:8px 12px;">em curso</td></tr>
# MAGIC     <tr><td style="padding:8px 12px;">5 · Skills</td><td style="padding:8px 12px;">a iniciar</td><td style="padding:8px 12px;">base pronta</td></tr>
# MAGIC     <tr style="background:#F2F6FB;color:#6B7280;"><td style="padding:8px 12px;">4 · Guardrails</td><td style="padding:8px 12px;">29%</td><td style="padding:8px 12px;">opcional</td></tr>
# MAGIC   </table>
# MAGIC </div>

# COMMAND ----------

# MAGIC %md
# MAGIC ## Pilar 1 — Observabilidade e integração com modelos externos · 60%
# MAGIC
# MAGIC **Concluído**
# MAGIC - ✓ Registro de modelo externo — Microsoft AI Foundry (GPT), testado via curl, browser e Python.
# MAGIC - ✓ Registro de modelo servido pela Databricks — Claude Haiku, para comparação de uso e custo.
# MAGIC - ✓ Usage Tracking ativo nos dois modelos (requests, tokens, status, request ID).
# MAGIC - ✓ Spend Tracking ativo — custo por requisição do modelo externo.
# MAGIC - ✓ Inference Tables configuradas nos dois modelos.
# MAGIC - ✓ Dashboard de governança a nível de conta, cross-workspace.
# MAGIC - ✓ Tag de governança de projeto aplicada aos modelos.
# MAGIC
# MAGIC **Ações que faltam**
# MAGIC - » **Popular o dashboard com uso real em dev.** Ação: direcionar tráfego do agente em dev ao endpoint
# MAGIC   por alguns dias; as inference/usage tables alimentam o dashboard automaticamente. Notebook: `1.1`.
# MAGIC - » **Atribuição de custo/uso por usuário via SSO.** Ação: confirmar que o consumo chega com a
# MAGIC   identidade do usuário (depende de a chamada propagar o usuário — ver Pilar 2/OBO). Notebook: `1.1`.
# MAGIC - ○ **Hard spend cap com alerta por grupo.** Ação: configurar o limite de gasto (hard cap) no endpoint
# MAGIC   e o alerta por grupo de usuários. Notebook: `3.1` (controles do gateway).
# MAGIC - ○ **Agente (modelo + MCP) rastreado ponta a ponta com LLM-as-judge.** Ação: instrumentar o agente com
# MAGIC   MLflow tracing e adicionar um scorer LLM-as-judge sobre os traces. Notebook: `1.1`.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Pilar 2 — MCP interno, externo e customizado · 17% (prioridade para acelerar)
# MAGIC
# MAGIC **Concluído**
# MAGIC - ✓ Schema dedicado para MCPs criado no catálogo.
# MAGIC
# MAGIC **Ações que faltam**
# MAGIC - » **MCP externo/SaaS — SlideSpeak.** Ação: registrado como HTTP connection; falta invocar (requer API
# MAGIC   key paga da SlideSpeak). Notebook: `2.4`.
# MAGIC - » **MCP customizado interno no ACI.** Ação: definir a conectividade de rede (Private Link/NCC) e o
# MAGIC   modo de auth (U2M Per-User, já validado). Notebooks: `2.2`, `2.5`, `2.6`.
# MAGIC - ○ **MCP interno gerenciado (Genie / UC Functions) invocado por agente.** Ação: criar a UC Function e
# MAGIC   invocá-la pelo managed MCP (`/api/2.0/mcp/functions/...`) a partir de um agente. Notebook: `5.1` (Parte B).
# MAGIC - ○ **Permissões de ferramenta pelo Unity Catalog + service policy.** Ação: aplicar `EXECUTE`/`READ` no
# MAGIC   securable e uma contextual service policy (allow/deny/approval). Notebook: `2.3`.
# MAGIC - ○ **Payload logging de MCP para auditoria.** Ação: habilitar o payload logging no MCP Service.
# MAGIC   Notebook: `2.2`.
# MAGIC
# MAGIC > **Este é o pilar mais atrasado — o foco para acelerar.** Os dois itens de maior alavancagem: fechar o
# MAGIC > **managed MCP invocado por agente** (base já pronta no 5.1) e as **permissões por UC + service policy**.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Pilar 3 — AI Gateway e roteamento de modelos · 57%
# MAGIC
# MAGIC **Concluído**
# MAGIC - ✓ Endpoint único roteando múltiplos modelos, com o modelo especificado na chamada.
# MAGIC - ✓ Modelo externo (Foundry/GPT) e Databricks (Claude Haiku) coexistindo sob o mesmo padrão.
# MAGIC - ✓ Rate limit por identidade (10 requisições / 1.000 tokens), por minuto e por hora.
# MAGIC - ✓ Registro automático no Unity Catalog, com controle de acesso por principal.
# MAGIC
# MAGIC **Ações que faltam**
# MAGIC - » **Endpoint integrado à plataforma de agentes em dev.** Ação: apontar o agente ao endpoint (agente
# MAGIC   GPT criado, YAML do Claude Haiku validado); confirmar o consumo ponta a ponta. Notebook: `3.1`.
# MAGIC - ○ **Fallback entre modelos ao vivo.** Ação: configurar a lista de fallback no endpoint e demonstrar a
# MAGIC   troca ao falhar o primário. Notebook: `3.1`.
# MAGIC - ○ **Traffic split e smart routing por custo.** Ação: configurar o split de tráfego e o roteamento por
# MAGIC   custo. Notebook: `3.1`.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Pilar 5 — Skills: registro, uso e governança · a iniciar (base pronta)
# MAGIC
# MAGIC Reestruturado em dois caminhos, conforme a natureza da skill:
# MAGIC - **Skill que INSTRUI** (`SKILL.md` + referências) → **UC Skills** governadas no Unity Catalog (Beta).
# MAGIC   Notebook: `5.2`.
# MAGIC - **Skill que EXECUTA** (gerar documento, análise via LLM) → **tool MCP** (App ou UC Function).
# MAGIC   Notebook: `5.1` (App de PDF e UC Function inteligente já validados).
# MAGIC
# MAGIC **Ações que faltam**
# MAGIC - ○ **Habilitar o preview Unity AI** (Account Console → Previews) — pré-requisito. Responsável: account
# MAGIC   admin. Notebook: `5.2` (Passo 1).
# MAGIC - ○ **Instalar/conectar o `ucode`** e registrar o `databricks-skill-registry`. Notebook: `5.2` (Passo 2).
# MAGIC - ○ **Criar a skill de identidade visual de documento** (`SKILL.md` + referências) e publicá-la no
# MAGIC   schema. Notebook: `5.2` (Passos 3-4).
# MAGIC - ○ **Compartilhar via `GRANT READ VOLUME`** para o grupo do agente; validar uso ao vivo (interno e
# MAGIC   externo). Notebook: `5.2` (Passo 5).
# MAGIC - ○ **Versionamento via Git folders** + notebook de sync do repositório de skills. Notebook: `5.2`.
# MAGIC - ○ **Skill que executa (documento com layout)** exposta como App MCP, se o caso exigir geração de
# MAGIC   binário. Notebook: `5.1` (Parte A).
# MAGIC
# MAGIC > **Nota de governança (importante para o compete):** UC Skills torna a skill um securable do Unity
# MAGIC > Catalog (`catalog.schema.skill`), com acesso por privilégios de Volume (`READ VOLUME`) e auditoria
# MAGIC > nativa — o time cria com o próprio agente, versiona em Git e compartilha por `GRANT`. Recurso em Beta.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Pilar 4 — Guardrails · opcional · 29%
# MAGIC Não é pré-requisito de sucesso da POC; fora do cálculo de conclusão.
# MAGIC
# MAGIC **Concluído**
# MAGIC - ✓ Política de PII Blocking no input, aplicada a todos os usuários do endpoint.
# MAGIC - ✓ Catálogo de guardrails mapeado com o time (PII, conteúdo inseguro, jailbreak, hallucination, custom).
# MAGIC
# MAGIC **Ações que faltam (opcionais)**
# MAGIC - » Teste ao vivo do PII Blocking com dado real do dia a dia. Notebook: `4.1`.
# MAGIC - ○ PII redaction na resposta · ○ Jailbreak protection no input · ○ Hallucination guardrail na saída
# MAGIC   · ○ Custom guardrail para um caso do negócio. Notebook: `4.1`.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Sequência recomendada para acelerar
# MAGIC 1. **Pilar 2 (gargalo em 17%)** — fechar managed MCP invocado por agente + permissões por UC/service
# MAGIC    policy + payload logging.
# MAGIC 2. **Pilar 5** — habilitar o preview Unity AI e publicar a primeira UC Skill (base pronta no 5.1/5.2).
# MAGIC 3. **Pilar 3** — fallback ao vivo + traffic split/smart routing.
# MAGIC 4. **Pilar 1** — hard spend cap + tracing ponta a ponta com LLM-as-judge; popular o dashboard.
# MAGIC 5. **Pilar 4 (opcional)** — apenas se houver tempo/interesse após os obrigatórios.
