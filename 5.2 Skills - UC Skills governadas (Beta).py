# Databricks notebook source
# MAGIC %md
# MAGIC # 5.2 Skills — UC Skills governadas no Unity Catalog (Beta)
# MAGIC
# MAGIC Uma **skill** é um pacote de instrução + arquivos de referência que orienta um agente a executar uma
# MAGIC tarefa seguindo um padrão (por exemplo: gerar um documento com uma identidade visual específica,
# MAGIC escrever SQL nas convenções do time, ou aplicar um procedimento de negócio). O formato é o padrão
# MAGIC aberto **Agent Skills** — uma pasta com um `SKILL.md` e os arquivos de apoio.
# MAGIC
# MAGIC Este notebook mostra como **governar** essas skills como **objetos de primeira classe do Unity
# MAGIC Catalog**: publicá-las em um schema (`catalog.schema.skill`), controlar acesso por `GRANT`, e
# MAGIC compartilhá-las para que o agente de qualquer pessoa as use sob os mesmos controles e auditoria que
# MAGIC governam os demais dados. O agente que consome pode ser interno (Genie Code) ou externo.
# MAGIC
# MAGIC > **Referência oficial:** *Create and share Unity Gateway Skills* —
# MAGIC > https://learn.microsoft.com/en-us/azure/databricks/agents/uc-skills/create-share-uc-skills
# MAGIC > e *Govern skills* — https://learn.microsoft.com/en-us/azure/databricks/ai-gateway/govern-skills

# COMMAND ----------

# MAGIC %md-sandbox
# MAGIC <div style="background:#FFF4E5;border-left:6px solid #E08A2B;border-radius:6px;padding:16px 20px;font-family:'DM Sans',Arial,sans-serif;color:#3C2E1A;">
# MAGIC   <h3 style="margin:0 0 8px 0;color:#B5651D;">Recurso em Beta</h3>
# MAGIC   <p style="margin:0;line-height:1.5;">
# MAGIC     UC Skills está em Beta. Antes de qualquer pessoa criar ou usar skills, um administrador da conta
# MAGIC     precisa habilitar o preview <b>Unity AI</b> em Account Console → Previews. Sem isso, os passos de
# MAGIC     publicação falham. Confirme essa habilitação no ambiente antes de rodar este notebook.
# MAGIC   </p>
# MAGIC </div>

# COMMAND ----------

# MAGIC %md
# MAGIC ## Por que UC Skills (e como se diferencia de uma tool MCP)
# MAGIC No notebook 5.1, uma capacidade que **executa código** (gerar PDF, rodar uma análise via `ai_query`)
# MAGIC é exposta como **tool** — via App MCP ou UC Function. Já uma skill é, na essência, **instrução +
# MAGIC conhecimento**: não há código a hospedar. Forçar esse tipo de skill dentro de um App ou de uma
# MAGIC função seria criar infraestrutura para hospedar texto.
# MAGIC
# MAGIC UC Skills resolve isso de forma nativa. A skill vive como um securable no Unity Catalog e o controle
# MAGIC de acesso usa **privilégios de Volume** — coerente com o fato de a skill ser, no fundo, uma pasta de
# MAGIC arquivos governada.
# MAGIC
# MAGIC | | Skill que **executa** (5.1) | Skill que **instrui** (este notebook) |
# MAGIC |---|---|---|
# MAGIC | Conteúdo | script + libs (ex.: gerar PDF) | `SKILL.md` + arquivos de referência |
# MAGIC | Como é exposta | tool MCP (App ou UC Function) | securable UC (`catalog.schema.skill`) |
# MAGIC | Governança | `EXECUTE` no MCP Service / função | privilégios de Volume (`READ VOLUME`) |
# MAGIC | Infra | App ou função | nenhuma — é uma pasta governada |
# MAGIC | Quem consome | qualquer agente via gateway | agente do time (interno ou externo), sob grant + auditoria |
# MAGIC
# MAGIC **Regra prática:** se a skill precisa **rodar algo**, use 5.1 (tool MCP). Se a skill **orienta** o
# MAGIC agente com instruções e referências, use UC Skills (este notebook).

# COMMAND ----------

# MAGIC %md
# MAGIC ## Fluxo em cinco passos
# MAGIC 1. Habilitar o preview e conceder acesso ao schema (administrador).
# MAGIC 2. Instalar e conectar o `ucode` (registra o servidor MCP `databricks-skill-registry`).
# MAGIC 3. Desenvolver a skill localmente (uma pasta com `SKILL.md`).
# MAGIC 4. Publicar a skill no schema do Unity Catalog.
# MAGIC 5. Compartilhar via `GRANT` para o agente de outra pessoa usar.
# MAGIC
# MAGIC Os passos 2, 3 e 4 rodam na **máquina local** (terminal + coding agent), pois envolvem o `ucode` e o
# MAGIC sistema de arquivos local. Os passos 1 e 5 são de governança no workspace. As células abaixo mostram
# MAGIC cada comando e um exemplo real para a POC.

# COMMAND ----------

# MAGIC %run "./0.0 Setup - Parâmetros do cliente"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Parâmetros
# MAGIC O schema que hospeda as skills e o grupo que receberá acesso de leitura. O schema pode ser o mesmo dos
# MAGIC MCPs (`CATALOG.SCHEMA` do Setup) ou um schema dedicado a skills.

# COMMAND ----------

# Schema onde as skills serão publicadas (catalog.schema).
SKILLS_CATALOG = CATALOG
SKILLS_SCHEMA  = SCHEMA          # ex.: um schema dedicado "skills"

# Skill de exemplo da POC.
SKILL_NAME = "documento-identidade-visual"

# Grupo que poderá usar a skill (mesmo grupo de consumidores do agente).
GRUPO = "consumidores-mcp"

# Coding agent usado localmente (claude, codex, gemini, opencode ou copilot).
CODING_AGENT = "claude"

print("Skill alvo :", f"{SKILLS_CATALOG}.{SKILLS_SCHEMA}.{SKILL_NAME}")
print("Grupo      :", GRUPO)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 1 — Habilitar o preview e preparar o schema (administrador)
# MAGIC O administrador da conta habilita o preview **Unity AI** (Account Console → Previews) e concede ao
# MAGIC autor da skill os privilégios necessários no schema alvo: `USE SCHEMA` e `CREATE VOLUME`.

# COMMAND ----------

# MAGIC %md
# MAGIC Substitua `<autor@empresa.com>` pelo autor da skill e execute (requer privilégio de administração no
# MAGIC schema). São os privilégios que a documentação exige para publicar uma skill.

# COMMAND ----------

AUTOR = "<autor@empresa.com>"   # quem vai criar/publicar skills

for stmt in [
    f"GRANT USE CATALOG ON CATALOG {SKILLS_CATALOG} TO `{AUTOR}`",
    f"GRANT USE SCHEMA ON SCHEMA {SKILLS_CATALOG}.{SKILLS_SCHEMA} TO `{AUTOR}`",
    f"GRANT CREATE VOLUME ON SCHEMA {SKILLS_CATALOG}.{SKILLS_SCHEMA} TO `{AUTOR}`",
]:
    print("  ", stmt)
# Descomente para aplicar (requer permissão de administração no schema):
# for stmt in [...]:
#     spark.sql(stmt)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 2 — Instalar e conectar o `ucode` (terminal local)
# MAGIC O `ucode` autentica no workspace e registra o servidor MCP **`databricks-skill-registry`**, que o
# MAGIC coding agent usa para criar e atualizar skills. Requer Python 3.12+ e `uv`.
# MAGIC
# MAGIC ```bash
# MAGIC # 1. Instalar o ucode a partir do repositório oficial
# MAGIC uv tool install git+https://github.com/databricks/ucode
# MAGIC
# MAGIC # 2. Conectar o coding agent ao workspace (abre o browser para login)
# MAGIC ucode configure --agents <coding-agent> --workspaces https://<workspace-host>
# MAGIC
# MAGIC # 3. Registrar as ferramentas de skills no agente
# MAGIC ucode configure skills
# MAGIC ```
# MAGIC
# MAGIC Substitua `<coding-agent>` por `claude`, `codex`, `gemini`, `opencode` ou `copilot`, e
# MAGIC `<workspace-host>` pelo host do workspace (ex.: `my-company.cloud.databricks.com`). Reinicie o agente
# MAGIC depois (`ucode <coding-agent>`) para que ele carregue as novas ferramentas.

# COMMAND ----------

print("Comandos para o terminal local:")
print(f"  uv tool install git+https://github.com/databricks/ucode")
print(f"  ucode configure --agents {CODING_AGENT} --workspaces {HOST.replace('https://', 'https://')}")
print(f"  ucode configure skills")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 3 — Desenvolver a skill localmente
# MAGIC Uma skill é uma pasta: um `SKILL.md` com instruções, mais os arquivos que o agente deve ler. O campo
# MAGIC `description` no frontmatter é o mais importante — é o que os agentes avaliam ao decidir usar a skill,
# MAGIC então deve ser específico sobre **o que** ela cobre e **quando** usá-la.
# MAGIC
# MAGIC Estrutura da skill de exemplo (gerar documento com a identidade visual do time):
# MAGIC ```
# MAGIC documento-identidade-visual/
# MAGIC ├── SKILL.md
# MAGIC ├── identidade_visual.md      (paleta, tipografia, regras de cabeçalho/rodapé)
# MAGIC └── modelo_documento.md       (estrutura padrão do documento)
# MAGIC ```
# MAGIC
# MAGIC Exemplo de `SKILL.md`:
# MAGIC ```markdown
# MAGIC ---
# MAGIC name: documento-identidade-visual
# MAGIC description: Padrão de documento do time — cabeçalho, paleta, tipografia e estrutura. Use ao gerar ou revisar qualquer documento oficial (relatórios, one-pagers, propostas).
# MAGIC ---
# MAGIC
# MAGIC # Documento com identidade visual
# MAGIC
# MAGIC - Cabeçalho fixo com o logotipo à esquerda e o título centralizado.
# MAGIC - Paleta e tipografia conforme `identidade_visual.md`.
# MAGIC - Estrutura de seções conforme `modelo_documento.md`.
# MAGIC - Rodapé com numeração de página e data de emissão.
# MAGIC - Nunca alterar cores fora da paleta oficial.
# MAGIC ```
# MAGIC
# MAGIC O coding agent conectado pode rascunhar a pasta a partir de um pedido em linguagem natural — é a forma
# MAGIC mais rápida de começar e depois refinar.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 4 — Publicar a skill no Unity Catalog
# MAGIC Publicar envia a pasta local para um schema do Unity Catalog como skill governada. Quem publica vira
# MAGIC **owner** da skill, e ela fica **privada** até ser compartilhada.
# MAGIC
# MAGIC No coding agent conectado (linguagem natural):
# MAGIC ```
# MAGIC Publique minha pasta ./documento-identidade-visual no schema <catalog>.<schema> do Databricks.
# MAGIC ```
# MAGIC
# MAGIC O agente chama a ferramenta MCP `create_skill` (e `update_skill` para atualizações posteriores) no
# MAGIC servidor `databricks-skill-registry`. Para atualizar depois, edite a pasta local e peça ao agente
# MAGIC para atualizar a skill a partir dela.
# MAGIC
# MAGIC | Ferramenta (no `databricks-skill-registry`) | Função |
# MAGIC |---|---|
# MAGIC | `create_skill` | publicar uma nova skill |
# MAGIC | `update_skill` | atualizar uma skill existente a partir da pasta local |
# MAGIC | `delete_skill` | remover uma skill |

# COMMAND ----------

# MAGIC %md
# MAGIC ## Passo 5 — Compartilhar a skill (governança por `GRANT`)
# MAGIC Uma skill publicada é privada até você conceder acesso. Compartilhar é um **grant**, não uma cópia: o
# MAGIC agente do destinatário lê a skill **ao vivo**, sob os seus grants e auditoria — não há nada para
# MAGIC manter sincronizado.
# MAGIC
# MAGIC O consumidor precisa de: `USE CATALOG` no catálogo, `USE SCHEMA` no schema e **`READ VOLUME`** na
# MAGIC skill. Para conceder, você precisa ser owner da skill ou ter `MANAGE` sobre ela.

# COMMAND ----------

# MAGIC %md
# MAGIC Pela interface: Catalog Explorer → schema → selecionar a skill → aba **Permissions** → **Grant** →
# MAGIC informar o usuário/grupo → selecionar `READ VOLUME` → **OK**. Também conceda `USE CATALOG`/`USE SCHEMA`
# MAGIC no catálogo e no schema.
# MAGIC
# MAGIC Por SQL, os grants equivalentes (execute como owner/MANAGE da skill):

# COMMAND ----------

grants = [
    f"GRANT USE CATALOG ON CATALOG {SKILLS_CATALOG} TO `{GRUPO}`",
    f"GRANT USE SCHEMA ON SCHEMA {SKILLS_CATALOG}.{SKILLS_SCHEMA} TO `{GRUPO}`",
    f"GRANT READ VOLUME ON VOLUME {SKILLS_CATALOG}.{SKILLS_SCHEMA}.`{SKILL_NAME}` TO `{GRUPO}`",
]
for stmt in grants:
    print("  ", stmt)
# Descomente para aplicar (requer ser owner/MANAGE da skill):
# for stmt in grants:
#     try:
#         spark.sql(stmt)
#         print("ok:", stmt)
#     except Exception as e:
#         print("aviso:", stmt, "->", str(e)[:160])

# COMMAND ----------

# MAGIC %md
# MAGIC > **Nota sobre o securable.** A skill é um securable UC no namespace de três níveis
# MAGIC > (`catalog.schema.skill`) e o acesso de uso é `READ VOLUME` — coerente com a skill ser uma pasta de
# MAGIC > arquivos governada. Se o SQL de `GRANT ... ON VOLUME` sobre a skill retornar erro de tipo, use a aba
# MAGIC > **Permissions** do Catalog Explorer (caminho oficial documentado) para o mesmo efeito.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Uso pelo agente (interno e externo)
# MAGIC Depois de compartilhada, a skill é descoberta e usada a partir do agente de cada pessoa — o agente a
# MAGIC carrega ao vivo do schema, sob o grant e a auditoria. Um agente externo consome da mesma forma: com
# MAGIC `READ VOLUME`, o agente do consumidor lê a skill ao vivo, sob os seus grants e auditoria.
# MAGIC
# MAGIC Para manter um schema sempre atualizado a partir de um repositório Git de skills, a documentação
# MAGIC oferece um notebook de sincronização (clona o repo, cria as novas e atualiza as alteradas; nunca
# MAGIC apaga). Assim os consumidores carregam o schema inteiro ao vivo e sempre recebem a versão publicada.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Versionamento e autoria
# MAGIC - **Fonte de verdade:** a pasta da skill em Git (versionamento, revisão, histórico).
# MAGIC - **Publicação:** `ucode`/`create_skill`/`update_skill` levam a pasta para o schema governado.
# MAGIC - **Quem escreve:** controlado pelos privilégios do schema (`CREATE VOLUME`/`WRITE VOLUME`) e pela
# MAGIC   propriedade da skill.
# MAGIC - **Quem usa:** controlado por `READ VOLUME` + `USE CATALOG`/`USE SCHEMA`.
# MAGIC - **Auditoria:** o uso da skill é registrado sob o grant do owner, como o restante dos dados no UC.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Resumo — como isso atende o objetivo da POC
# MAGIC O time cria a skill com o próprio agente (formato aberto, versionado em Git), publica no Unity Catalog
# MAGIC como securable, e compartilha por `GRANT`. A mesma skill fica governada, rastreada e utilizável por
# MAGIC agentes internos e externos — sem hospedar infraestrutura para skills que apenas instruem. Skills que
# MAGIC precisam executar código continuam no notebook 5.1 (tool MCP via App ou UC Function).
