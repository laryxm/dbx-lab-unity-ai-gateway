# Databricks notebook source
# MAGIC %md
# MAGIC # Conectividade Databricks ↔ servidor MCP em Azure (ACI / AKS)
# MAGIC
# MAGIC Quando o servidor MCP está hospedado em infraestrutura própria na Azure (Azure Container
# MAGIC Instances ou AKS), é preciso garantir a rota de rede entre o Databricks e esse endpoint antes
# MAGIC de registrá-lo no Unity AI Gateway. Este notebook documenta os caminhos possíveis e o passo a
# MAGIC passo de cada um.
# MAGIC
# MAGIC ## Ponto de partida: de onde sai o tráfego
# MAGIC O **proxy da HTTP Connection do Unity Catalog roda na infraestrutura do Databricks**. O caminho
# MAGIC de saída depende do plano de compute que atende a chamada:
# MAGIC
# MAGIC | Plano de compute | Origem do tráfego de saída | Quem controla a rede |
# MAGIC |---|---|---|
# MAGIC | **Serverless** (padrão do gateway) | plano serverless do Databricks (IPs gerenciados) | **NCC** (Network Connectivity Config) |
# MAGIC | **Classic / VNet-injected** | a VNet do próprio workspace | o cliente (NSG, peering, rota) |
# MAGIC
# MAGIC O Unity AI Gateway roteia tipicamente pelo **serverless** — então o caminho principal é via NCC.
# MAGIC
# MAGIC ## Decisão rápida (qual caminho seguir)
# MAGIC ```
# MAGIC O endpoint MCP é acessível publicamente (com TLS)?
# MAGIC ├── SIM, e pode ficar público          -> Caminho C: IP allowlist (mais simples)
# MAGIC └── NÃO (privado na VNet)               -> precisa de conectividade privada
# MAGIC     ├── É PaaS Azure nativo (raro p/ MCP) -> Caminho A: NCC + private endpoint (group_id)
# MAGIC     └── É serviço próprio (ACI/AKS)       -> Caminho B: NCC + Private Link Service (domain_names)
# MAGIC ```
# MAGIC Para MCP em ACI/AKS o caso normal é o **Caminho B**.
# MAGIC
# MAGIC ## Pré-requisitos
# MAGIC - Databricks CLI autenticado no **nível de conta** (`databricks account ...`) — NCC é recurso de conta.
# MAGIC - `account_id` do Databricks e permissão de account admin.
# MAGIC - Do lado Azure: az CLI com acesso à subscription onde está o ACI/AKS.
# MAGIC
# MAGIC > Este notebook é uma **referência de configuração** (os comandos rodam no terminal / az CLI,
# MAGIC > não dentro do cluster). Substitua os placeholders `<...>` pelos valores reais.

# COMMAND ----------

# MAGIC %run "./0.0 Setup - Parâmetros do cliente"

# COMMAND ----------

# MAGIC %md
# MAGIC # Caminho B — NCC + Private Link Service (recomendado para ACI/AKS privado)
# MAGIC
# MAGIC Expõe-se o serviço MCP atrás de um **Private Link Service (PLS)** na Azure; o Databricks cria um
# MAGIC **private endpoint gerenciado** apontando para esse PLS; o dono do PLS aprova; configura-se DNS.
# MAGIC O tráfego Databricks → MCP nunca transita pela internet pública.
# MAGIC
# MAGIC ### Visão geral do fluxo
# MAGIC ```
# MAGIC [Serverless Databricks] --PE gerenciado--> [Private Link Service] --> [Internal LB] --> [Pod/Container MCP]
# MAGIC                                                    (VNet do cliente, ACI/AKS)
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## B.1 — Expor o MCP atrás de um Internal Load Balancer + Private Link Service (lado Azure)
# MAGIC
# MAGIC **AKS:** publicar o serviço com um Internal Load Balancer (ILB):
# MAGIC ```yaml
# MAGIC # service-mcp.yaml
# MAGIC apiVersion: v1
# MAGIC kind: Service
# MAGIC metadata:
# MAGIC   name: slidespeak-mcp
# MAGIC   annotations:
# MAGIC     service.beta.kubernetes.io/azure-load-balancer-internal: "true"
# MAGIC spec:
# MAGIC   type: LoadBalancer
# MAGIC   selector: { app: slidespeak-mcp }
# MAGIC   ports:
# MAGIC     - port: 443
# MAGIC       targetPort: 8080
# MAGIC ```
# MAGIC ```bash
# MAGIC kubectl apply -f service-mcp.yaml
# MAGIC ```
# MAGIC
# MAGIC **ACI:** implantar o container group **dentro da VNet** (IP privado) e colocá-lo atrás de um
# MAGIC Standard Internal LB na mesma VNet/subnet.
# MAGIC
# MAGIC **Criar o Private Link Service** apontando pro frontend do ILB (numa subnet dedicada ao PLS):
# MAGIC ```bash
# MAGIC az network private-link-service create \
# MAGIC   --resource-group <RG_ERO> \
# MAGIC   --name pls-slidespeak-mcp \
# MAGIC   --vnet-name <VNET_ERO> \
# MAGIC   --subnet <SUBNET_PLS> \
# MAGIC   --lb-name <NOME_DO_ILB> \
# MAGIC   --lb-frontend-ip-configs <FRONTEND_DO_ILB> \
# MAGIC   --location <REGIAO> \
# MAGIC   --fqdns mcp.interno.ero.com
# MAGIC
# MAGIC # anotar o resource id do PLS (usado no passo B.3):
# MAGIC # /subscriptions/<SUB>/resourceGroups/<RG_ERO>/providers/Microsoft.Network/privateLinkServices/pls-slidespeak-mcp
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## B.2 — Criar (ou reutilizar) o NCC na conta Databricks
# MAGIC ```bash
# MAGIC # criar um NCC na região do workspace
# MAGIC databricks account network-connectivity create-network-connectivity-configuration \
# MAGIC   --json '{"name":"ncc-ero-mcp","region":"<REGIAO_WORKSPACE>"}'
# MAGIC
# MAGIC # anotar o network_connectivity_config_id retornado (ex.: ncc-abc123)
# MAGIC # listar, se já existir:
# MAGIC databricks account network-connectivity list-network-connectivity-configurations
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## B.3 — Criar a regra de private endpoint apontando pro PLS
# MAGIC Para **PLS gerenciado pelo cliente** usa-se `resource_id` + **`domain_names`** (NÃO `group_id`;
# MAGIC os dois são mutuamente exclusivos — `group_id` é só para PaaS nativo, ver Caminho A).
# MAGIC ```bash
# MAGIC cat > pe-rule.json <<'JSON'
# MAGIC {
# MAGIC   "resource_id": "/subscriptions/<SUB>/resourceGroups/<RG_ERO>/providers/Microsoft.Network/privateLinkServices/pls-slidespeak-mcp",
# MAGIC   "domain_names": ["mcp.interno.ero.com"]
# MAGIC }
# MAGIC JSON
# MAGIC
# MAGIC databricks account network-connectivity create-private-endpoint-rule <NCC_ID> --json @pe-rule.json
# MAGIC # a regra retorna connection_state = PENDING (aguardando aprovação no passo B.4)
# MAGIC ```
# MAGIC Equivalente em **Terraform**:
# MAGIC ```hcl
# MAGIC resource "databricks_mws_ncc_private_endpoint_rule" "mcp" {
# MAGIC   network_connectivity_config_id = "<NCC_ID>"
# MAGIC   resource_id  = "/subscriptions/<SUB>/resourceGroups/<RG_ERO>/providers/Microsoft.Network/privateLinkServices/pls-slidespeak-mcp"
# MAGIC   domain_names = ["mcp.interno.ero.com"]
# MAGIC }
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## B.4 — Aprovar o private endpoint (lado Azure, dono do PLS)
# MAGIC O Databricks cria um Private Endpoint na subscription dele apontando pro PLS da Ero. O dono do
# MAGIC PLS **precisa aprovar** — a regra fica PENDING até isso (e expira se não aprovada na janela).
# MAGIC ```bash
# MAGIC # listar conexões pendentes no PLS
# MAGIC az network private-link-service connection list \
# MAGIC   --resource-group <RG_ERO> --name pls-slidespeak-mcp -o table
# MAGIC
# MAGIC # aprovar
# MAGIC az network private-endpoint-connection approve \
# MAGIC   --resource-group <RG_ERO> \
# MAGIC   --name <NOME_DA_CONEXAO_PENDENTE> \
# MAGIC   --resource-name pls-slidespeak-mcp \
# MAGIC   --type Microsoft.Network/privateLinkServices \
# MAGIC   --description "Aprovado para Databricks NCC"
# MAGIC ```
# MAGIC Confirmar que virou ESTABLISHED no Databricks:
# MAGIC ```bash
# MAGIC databricks account network-connectivity get-private-endpoint-rule <NCC_ID> <RULE_ID>
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## B.5 — DNS privado (crítico — sem isso falha mesmo aprovado)
# MAGIC O serverless do Databricks precisa resolver `mcp.interno.ero.com` para o IP privado do endpoint.
# MAGIC - Criar/uso de uma **Private DNS Zone** com um registro A do FQDN → IP privado do private endpoint.
# MAGIC - Vincular a zona à(s) VNet(s) relevante(s).
# MAGIC - O `domain_names` da regra (B.3) DEVE bater com o FQDN que a connection HTTP usará como `host`.
# MAGIC
# MAGIC ## B.6 — Anexar o NCC ao workspace
# MAGIC ```bash
# MAGIC databricks account network-connectivity update-network-connectivity-configuration ... \
# MAGIC   # ou, no Account Console: Cloud resources > Network Connectivity > anexar workspace(s)
# MAGIC ```
# MAGIC Após anexado + ESTABLISHED + DNS, o proxy serverless alcança o MCP pelo FQDN privado.

# COMMAND ----------

# MAGIC %md
# MAGIC # Caminho A — NCC + private endpoint para PaaS Azure nativo (`group_id`)
# MAGIC
# MAGIC Aplica-se quando o alvo é um **serviço PaaS Azure de primeira parte** (Storage, SQL, etc.) — raro
# MAGIC para um MCP, mas incluído por completude. Aqui usa-se **`group_id`** (o sub-recurso), NÃO `domain_names`.
# MAGIC ```bash
# MAGIC databricks account network-connectivity create-private-endpoint-rule <NCC_ID> \
# MAGIC   --resource-id "/subscriptions/<SUB>/resourceGroups/<RG>/providers/Microsoft.Storage/storageAccounts/<SA>" \
# MAGIC   --group-id blob
# MAGIC ```
# MAGIC Aprovação e DNS seguem o mesmo padrão do Caminho B (B.4–B.5), com as private DNS zones `privatelink.*`.
# MAGIC **Nota:** se o MCP estiver empacotado atrás de um App Gateway/Front Door gerenciado, avaliar caso a caso.

# COMMAND ----------

# MAGIC %md
# MAGIC # Caminho C — IP allowlist (endpoint público, se Private Link não for viável)
# MAGIC
# MAGIC Se o MCP puder ficar **público** (com TLS) porém protegido por firewall, libera-se os **IPs de
# MAGIC saída do plano serverless do Databricks**. Menos elegante e exige manutenção, mas funciona sem PLS.
# MAGIC
# MAGIC ## C.1 — Estabilizar os IPs de saída do serverless (recomendado)
# MAGIC O serverless usa um pool de IPs que pode mudar. Para ter IPs **estáveis** de saída, criar
# MAGIC **NCC default rules** (que fixam IPs/Service Tag Azure de egress) e anexar ao workspace:
# MAGIC ```bash
# MAGIC databricks account network-connectivity list-network-connectivity-configurations
# MAGIC # a config traz o bloco default_rules com os egress IPs / service tags do serverless
# MAGIC databricks account network-connectivity get-network-connectivity-configuration <NCC_ID>
# MAGIC ```
# MAGIC
# MAGIC ## C.2 — Liberar no firewall/NSG da Ero
# MAGIC - Adicionar os IPs de egress do serverless (do passo C.1) como **allow** na regra de entrada do
# MAGIC   endpoint MCP (NSG do ACI/AKS, ou Azure Firewall / App Gateway WAF na frente).
# MAGIC - Restringir a porta (443) e, de preferência, exigir a API key (Bearer) no próprio MCP.
# MAGIC - **Automatizar a atualização** dos IPs: o Databricks publica `ip-ranges.json` / service tags;
# MAGIC   criar um job que reconcilia a allowlist quando houver mudança.
# MAGIC
# MAGIC ## C.3 — Trade-offs
# MAGIC - ✅ Simples, sem PLS/DNS.
# MAGIC - ⚠️ Tráfego sai pela internet pública (TLS obrigatório).
# MAGIC - ⚠️ Manutenção da allowlist quando os IPs do serverless mudam.

# COMMAND ----------

# MAGIC %md
# MAGIC # Caminho D — Compute clássico (VNet-injected)
# MAGIC
# MAGIC Se a chamada ao MCP for executada por **compute clássico com VNet injection** (não serverless),
# MAGIC o egress sai da **VNet do workspace** e vale conectividade de VNet padrão:
# MAGIC - **VNet peering** entre a VNet do workspace e a VNet do ACI/AKS da Ero, ou
# MAGIC - um **private endpoint / rota** alcançável a partir da subnet do workspace.
# MAGIC - NSGs e private DNS na VNet do workspace resolvendo o FQDN do MCP.
# MAGIC
# MAGIC Menos comum para o Unity AI Gateway (que tende a rodar em serverless), mas aplicável se a
# MAGIC arquitetura da Ero direcionar o tráfego por clusters clássicos.

# COMMAND ----------

# MAGIC %md
# MAGIC # Validação end-to-end (depois de qualquer caminho)
# MAGIC 1. Do serverless (um notebook simples), testar resolução + alcance do FQDN privado/público:
# MAGIC    ```python
# MAGIC    import socket, requests
# MAGIC    print(socket.gethostbyname("mcp.interno.ero.com"))   # deve resolver p/ IP privado (Caminhos A/B)
# MAGIC    r = requests.post("https://mcp.interno.ero.com/mcp",
# MAGIC                      headers={"Authorization":"Bearer <API-KEY>",
# MAGIC                               "Accept":"application/json, text/event-stream",
# MAGIC                               "Content-Type":"application/json"},
# MAGIC                      json={"jsonrpc":"2.0","id":1,"method":"initialize",
# MAGIC                            "params":{"protocolVersion":"2025-11-25","capabilities":{},
# MAGIC                                      "clientInfo":{"name":"test","version":"1.0"}}}, timeout=30)
# MAGIC    print(r.status_code, r.text[:300])
# MAGIC    ```
# MAGIC 2. Registrar a HTTP Connection com `host` = o FQDN configurado (ver `registrar_slidespeak_mcp`).
# MAGIC 3. Rodar o `initialize`/`tools/list` pelo proxy do UC — se resolver e responder, a rota está ok.
# MAGIC
# MAGIC ## Itens de discovery a confirmar com o time de infra da Ero
# MAGIC 1. O endpoint MCP será público (com firewall) ou privado na VNet?  → C vs A/B
# MAGIC 2. Está no mesmo tenant/subscription do workspace Databricks? (cross-tenant adiciona aprovação de PE + DNS)
# MAGIC 3. O AKS já tem Internal LB / o ACI está em-VNet? Existe subnet dedicada para o PLS?
# MAGIC 4. Quem aprova o private endpoint do lado Azure (dono do PLS)?
# MAGIC 5. Qual FQDN a aplicação usará para o MCP (define `domain_names` e DNS privado)?

# COMMAND ----------

# MAGIC %md
# MAGIC ## Resumo dos caminhos
# MAGIC | Caminho | Quando usar | Tráfego | Complexidade |
# MAGIC |---|---|---|---|
# MAGIC | **B — NCC + Private Link Service** | ACI/AKS privado (caso normal) | privado | média (PLS + aprovação + DNS) |
# MAGIC | **A — NCC + PE nativo (`group_id`)** | alvo PaaS Azure de 1ª parte | privado | baixa-média |
# MAGIC | **C — IP allowlist** | endpoint público c/ firewall | público (TLS) | baixa (mas manutenção de IPs) |
# MAGIC | **D — VNet-injected** | chamada por compute clássico | VNet do workspace | média (peering/rota) |
# MAGIC
# MAGIC Documentação: NCC private endpoint rule — `docs.databricks.com/api/networking/v1/ncc-private-endpoint-rule` ·
# MAGIC HTTP connections — `docs.databricks.com/aws/en/query-federation/http`