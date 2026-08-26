# POC — Unity AI Gateway

Prova de conceito de governança de modelos, MCP, roteamento, guardrails e skills sobre o
**Unity AI Gateway** do Databricks. Os notebooks são organizados por **pilar** (`<pilar>.<n>`) e
todos compartilham um único ponto de configuração (`0.0 Setup`), de modo que os valores de cliente
(catálogo, schema, nome do App, prefixos, secrets) vivam em um só lugar.

O material foi construído e validado ao vivo em um workspace de laboratório. Os valores de cliente
são sempre parametrizados (widgets / setup / secrets) — nenhum token ou chave aparece em texto plano
no código.

---

## Pré-requisitos

- Workspace Databricks com **Unity Catalog** e **Model Serving / AI Gateway** habilitados.
- Privilégios no schema alvo: `CREATE CONNECTION`, `CREATE FUNCTION`, `EXECUTE`.
- Databricks CLI autenticado (para deploy de Apps e criação de secrets).
- Para conectividade privada (Pilar 2.5): CLI autenticado em **nível de conta** e acesso à
  subscription Azure onde está o ACI/AKS.

## Como começar

1. Importe a pasta inteira no workspace, preservando os nomes dos notebooks.
2. Abra **`0.0 Setup - Parâmetros do cliente`** e edite **apenas** a célula "Parâmetros do cliente":

   ```python
   CATALOG      = "..."   # catálogo onde os objetos da POC vivem
   SCHEMA       = "..."   # schema para MCP Services e UC Functions
   APP_NAME     = "..."   # Databricks App que hospeda um MCP próprio (Pilares 2 e 5)
   CONN_PREFIX  = "..."   # prefixo das HTTP Connections (namespace metastore-level, compartilhada)
   SECRET_SCOPE = "..."   # scope de secrets para tokens/API keys
   ```

3. Guarde as chaves de provedor/API como **secrets** (nunca em texto plano):

   ```bash
   databricks --profile <perfil> secrets create-scope <SECRET_SCOPE>
   databricks --profile <perfil> secrets put-secret <SECRET_SCOPE> modelo_externo_api_key
   ```

4. Rode cada notebook de pilar. Todos começam com `%run "./0.0 Setup - Parâmetros do cliente"`.

---

## Estrutura

Cada notebook começa com `%run` do setup, que deriva automaticamente HOST/TOKEN/HEADERS e expõe os
helpers `rest()`, `parse_sse()`, `mcp_call()`, `gateway_mcp_url()`, `managed_functions_mcp_url()` e
`get_secret()`.

| Notebook | Pilar | O que faz |
|---|---|---|
| `0.0 Setup - Parâmetros do cliente` | — | Configuração central. Único lugar com valores de cliente + helpers derivados. |
| `1.1 Observabilidade - Registro de modelos e rastreamento` | 1 | Registra modelo externo e Foundation Model sob a mesma governança; liga usage tracking + inference tables; mostra como consultar uso, custo e logs. |
| `2.1 MCP - Registro de MCP em Databricks Apps` | 2 | Constrói e hospeda um MCP server próprio (FastMCP, Streamable HTTP) como Databricks App. |
| `2.2 MCP - Registro de MCP externo no AI Gateway (Databricks Apps)` | 2 | Registro ponta a ponta de um MCP externo: HTTP Connection + MCP Service + invocação via gateway. |
| `2.3 MCP - Registros por tipo de autenticação` | 2 | Um exemplo de cada tipo de auth (Bearer, OAuth M2M, U2M Shared, U2M Per-User, DCR) e qual identidade chega ao MCP. |
| `2.4 MCP - Registro de MCP externo (Outros Serviços)` | 2 | Registro de um MCP SaaS (SlideSpeak), nos modos hosted e self-host (ACI). |
| `2.5 MCP - Conectividade de rede no Azure` | 2 | Caminhos de rede Databricks ↔ MCP em ACI/AKS (NCC + Private Link Service, private endpoint nativo, IP allowlist, VNet). |
| `3.1 Gateway - Roteamento de modelos e controles` | 3 | Endpoint multi-modelo com traffic split, rate limit por identidade e fallback. |
| `4.1 Guardrails - Configuração, teste e PII brasileira` | 4 | Inspeciona/testa guardrails (PII, safety, keywords, tópicos) e entrega um guardrail próprio para CPF. |
| `5.1 Skills - Skill como tool no AI Gateway` | 5 | Expõe uma skill como tool via MCP — Parte A (App para PDF) e Parte B (UC Function inteligente com `ai_query`). |

Pastas de apoio:

- `mcp_demo_app/` — código do MCP server de demonstração (tools sintéticas de operação de mineração).
- `skill_pdf_mcp_app/` — código do MCP server que expõe a skill de geração de PDF.

---

## Pilar 1 — Observabilidade

O AI Gateway atua sobre um **serving endpoint**. O mesmo padrão de governança vale para modelos
externos (Azure OpenAI/Foundry, OpenAI, Anthropic, Bedrock, Vertex) e para modelos servidos pela
Databricks (Foundation Model API). O bloco `ai_gateway` do endpoint liga:

- **usage tracking** — requisições, tokens de entrada/saída e status atribuídos em system tables;
- **inference tables** — todo o conteúdo de entrada e saída persistido como Delta governado.

A chave do provedor é referenciada por `{{secrets/scope/chave}}` — nunca em texto plano.

## Pilar 2 — MCP (Model Context Protocol)

O registro de um MCP no Unity AI Gateway tem **duas peças** no Unity Catalog:

1. **HTTP Connection** — securable do UC que guarda o endpoint do MCP + a credencial. O Databricks
   roda um proxy gerenciado na frente do servidor e injeta a credencial.
2. **MCP Service** — objeto do UC que referencia a connection e vira a *tool* que agentes e o
   Playground conseguem chamar. Governança via `EXECUTE` por principal.

**Requisitos do servidor MCP:**

- Transport **obrigatório = Streamable HTTP** (invocação com `Accept: application/json, text/event-stream`).
- Deve anunciar uma **versão de protocolo oficial** no `initialize`: `2024-11-05`, `2025-03-26`,
  `2025-11-25` ou `2026-07-28`. Um erro `Unsupported protocol version: 2025-06-18` indica **SDK
  desatualizado no servidor** (essa data não existe na especificação) — corrige-se atualizando o SDK
  e reimplantando o servidor, não é configuração do Databricks.

**MCP próprio como Databricks App** (2.1) — pontos de implantação que valem observar:

- `stateless_http=True` vai no `run()`/`http_app()`, não no construtor `FastMCP()`.
- Health check na raiz: o proxy do Databricks Apps faz `GET /`; sem uma rota lá o app fica 502.
- Usar `mcp.run(...)` num `if __name__ == "__main__"`; não expor `app` para o uvicorn externo (o
  lifespan do FastMCP fica pendurado e todas as rotas dão 502).

**Autenticação** (2.3) — a HTTP Connection define como o gateway se autentica no servidor, e isso
determina qual identidade chega ao MCP:

| Tipo | Identidade no MCP | Quando usar |
|---|---|---|
| Bearer token | única (a do token) | API key de serviço (ex.: SlideSpeak) |
| OAuth M2M | única (o service principal) | automação server-to-server; MCP em Databricks App |
| OAuth U2M Shared | única (quem autorizou) | serviço sem client credentials, identidade única aceitável |
| **OAuth U2M Per-User** | **a do usuário que invoca** | acesso/auditoria por usuário (on-behalf-of) — MCP com IdP corporativo (ACI + Entra ID) |
| DCR | por usuário | provedor que suporta Dynamic Client Registration (ex.: HuggingFace) |

**Conectividade Azure** (2.5) — o proxy da HTTP Connection roda na infra do Databricks; o egress do
plano serverless (padrão do gateway) é controlado por **NCC**. Para MCP privado em ACI/AKS o caminho
normal é **NCC + Private Link Service** (private endpoint gerenciado → aprovação do dono do PLS →
DNS privado). Caminhos alternativos: private endpoint nativo (PaaS), IP allowlist (público com TLS)
e VNet-injected/peering.

## Pilar 3 — Roteamento e controles

Um serving endpoint pode ter mais de um *served entity*. Sobre ele, o bloco `ai_gateway` aplica
**rate limits** por usuário ou endpoint, **fallback** entre modelos e **traffic split** por
percentual (A/B, canário, roteamento por custo).

## Pilar 4 — Guardrails

O gateway inspeciona o payload de entrada (antes do modelo) e de saída (antes de retornar). Guardrails
configuráveis no endpoint: `pii` (`BLOCK` / `MASK` / `NONE`), `safety`, `invalid_keywords` e
`valid_topics`. `BLOCK` retorna HTTP 400; `MASK` retorna 200 com conteúdo redigido.

O detector de PII nativo reconhece categorias por formato e jurisdição (e-mail, cartão, telefone,
IBAN, `us_ssn`, `uk_nhs`, `in_pan`…). **Identificadores brasileiros (CPF, RG) não constam da lista
documentada** — o notebook verifica isso empiricamente no endpoint e entrega um **guardrail próprio
para CPF**, com validação dos dois dígitos verificadores (módulo 11), a ser aplicado na borda do agente.

## Pilar 5 — Skill como tool

Uma skill do Genie Code (carregada por contexto num coding agent) e uma tool do AI Gateway são
mecanismos diferentes: só a tool é consumível por um agente externo e testável no Playground. Para
uma skill ser usada por um agente externo, ela é exposta como tool via MCP. Duas formas:

- **Parte A — MCP próprio como Databricks App:** para skills com bibliotecas binárias ou I/O (ex.:
  gerar PDF com `reportlab` e persistir num Volume).
- **Parte B — UC Function + managed MCP:** para uma função **inteligente** (LLM embutido via
  `ai_query`) ou determinística; sem app, exposta automaticamente em
  `/api/2.0/mcp/functions/{catalog}/{schema}` e governada por `GRANT EXECUTE`.

---

## Convenções e boas práticas aplicadas

- Nenhum token/chave em texto plano — sempre via widget, secret ou contexto do notebook em runtime.
- HTTP Connections com prefixo (`CONN_PREFIX`), pois a namespace de connections é metastore-level e
  compartilhada.
- Estilo dos notebooks: profissional e impessoal, valores de cliente sempre parametrizados.

## Referências

- Register an external MCP server — `docs.databricks.com/aws/en/ai-gateway/register-mcp-service`
- HTTP Connections (tipos de auth) — `docs.databricks.com/aws/en/query-federation/http`
- Guardrails — `docs.databricks.com/aws/en/ai-gateway/guardrails`
- MCP versioning — `modelcontextprotocol.io/specification/versioning`
