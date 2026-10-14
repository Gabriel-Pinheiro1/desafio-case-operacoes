# Mesa de exceções: Problema 2 (Operações)

Aplicação web que **prioriza, investiga e ajuda a resolver** os pedidos com exceção da Gocase e da GoBeauté.
Ela responde às quatro perguntas do desafio com números em R$ e recalcula tudo quando chegam CSVs novos
(no mesmo formato) ou quando um parâmetro muda: capacidade, custos, feriados, tabela de cancelamento etc.

## Como rodar

```bash
# 1) uma vez: criar o ambiente e instalar as dependências
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt  # Windows (app + testes)
# .venv/bin/python -m pip install -r requirements-dev.txt    # Linux/macOS

# 2) subir o app
.venv\Scripts\python app.py
# abra http://127.0.0.1:8765   (outra porta: set PORT=9000)

# testes (a IA é simulada: nenhum teste chama a rede)
.venv\Scripts\python -m pytest -q

# 3) opcional: ligar o agente de IA. Crie um .env na raiz (já está no .gitignore e no .vercelignore):
#   AI_API_KEY=...
#   AI_BASE_URL=https://ai-proxy.gogroupbr.com/v1
#   AI_MODEL=latest
```

Python 3.11, pandas, FastAPI e uvicorn. Os gráficos usam Chart.js e as fontes vêm do Google Fonts, ambos por CDN.
Sem internet a página continua funcionando: os números aparecem nas tabelas e no lugar dos gráficos surge um aviso.
A análise não chama serviços externos. Só o **agente de IA** (opcional) envia dados para fora: os números de um pedido por vez,
sem identificar o cliente. Sem `AI_API_KEY` o agente aparece como "desligado" e todo o resto funciona igual.

## Publicar na Vercel

O `app.py` expõe o objeto `app` (FastAPI) na raiz, que a Vercel reconhece sozinha. Em vercel.com: **Add New → Project**, importe este
repositório do GitHub e clique em **Deploy** (Framework Preset: FastAPI, ou deixe *Other*; sem comandos extras).
O app não depende de memória entre requisições: quem chega numa instância nova carrega os dados originais, o upload vai inteiro
na própria requisição, trocar parâmetros depois de um upload reenvia os arquivos e o CSV da fila é gerado no navegador.
O agente segue a mesma regra: cada execução leva o pedido, os parâmetros atuais e, se houve upload, os mesmos arquivos.
O `vercel.json` só define `maxDuration: 60` para a função `app.py` (na Vercel o app inteiro é uma função; o limite vale para todas as
rotas, mas só a do agente chega perto dele). Para ligar o agente, veja [Configurar na Vercel](#configurar-na-vercel).

## O que tem na interface

| Aba | Conteúdo |
| --- | --- |
| **Visão executiva** | Dinheiro em risco, quanto a priorização recupera, ganho mensal se as causas forem corrigidas, principais achados em R$, evolução semanal e o método. |
| **Fila de hoje** | Os N pedidos (N = capacidade diária) que devem ser tratados hoje, com o motivo da prioridade e a ação recomendada. Tem filtros e exportação em CSV. |
| **Causas** | Causas sistêmicas (dono, ação, evidências, ganho estimado), falhas de sistema e roteiro caso a caso por tipo. Traz também as transportadoras medidas pelo prazo correto e pelo prazo do sistema. |
| **Parâmetros e dados** | Todos os parâmetros do `parametros.md`, mais as premissas próprias (marcadas), editáveis. Upload de `pedidos_excecao.csv` + `clientes.csv`, com `transportadoras.csv` opcional. |
| **Qualidade dos dados** | As 16 verificações que rodam a cada carga: o que foi encontrado, o que foi feito e o motivo. Inclui as concentrações brutas, para auditoria. |
| **Agente de IA** | Roda o agente num pedido (ID ou botão "Rodar agente" em cada linha da fila) ou nos N primeiros da fila (padrão 5, máx. 8). Mostra diagnóstico, ação, custo, rascunhos e as consultas feitas; ações com dinheiro têm "Aprovar"/"Recusar". |

API: `GET /api/analise`, `GET /api/backlog`, `POST /api/parametros` (JSON, aceita atualização parcial),
`POST /api/upload` (multipart: `pedidos`, `clientes`, `transportadoras` opcional, `params` opcional), `POST /api/reset`, `GET /api/fila.csv`,
`GET /api/agente/status`, `POST /api/agente` (multipart: `order_id`, `params` opcional e, se houve upload, os mesmos arquivos).

## Estrutura

```
analise.py            motor de análise (funções puras, testáveis)
agente.py             agente de IA: prompt, ferramentas, loop de tool calling, limites, chamada ao gateway
app.py                servidor FastAPI + estado em memória + rotas do agente
static/index.html     interface (HTML/CSS/JS puro + Chart.js)
tests/                pytest: dias úteis/prazo, deduplicação, priorização, detecção de causas, API, agente (IA simulada)
vercel.json           maxDuration de 60 s para a função app.py
scripts/gerar_dado_novo.py   gera um subconjunto com datas deslocadas para testar "dado novo"
dados/                CSVs e parâmetros do desafio
```

## Método

1. **Limpeza.** Normaliza texto e tipos e aceita separador `,` ou `;` e UTF-8 ou Latin-1. Remove linhas idênticas e, quando o
   mesmo `order_id` aparece em mais de uma linha, mantém uma só. Todas as checagens ficam registradas na aba de qualidade.
2. **Prazo correto.** Recalcula a data prometida pela regra do `parametros.md`: 1 dia útil de separação + SLA da transportadora
   em dias úteis, contando a partir do 1º dia útil depois do pedido e pulando fins de semana e os feriados configurados.
   A `data_prometida` do sistema **não** segue a regra: em 100% dos pedidos ela é `data_pedido + (1 + SLA)` em dias **corridos**.
3. **Atraso e risco.** O atraso é contado em dias úteis contra o prazo correto. Para atraso já entregue, conta até a entrega.
   Para as demais exceções (avaria, item faltante etc.), conta até a data de referência, porque o cliente segue sem o produto certo.
   - Perda esperada = P(cancelar | atraso) × (reembolso + recompra perdida).
     - P vem da tabela do `parametros.md`.
     - Reembolso = valor pago. Em item faltante, valor / qtd_itens. Em pagamento pendente, receita − CMV (venda perdida).
     - Recompra perdida = 50% × chance de recompra (70% ou 28%) × ticket médio do cliente × (1 − CMV).
   - **Custo de esperar** = quanto essa perda cresce se o pedido ficar mais *H* dias úteis sem tratativa
     (H = 5, editável), mais os contatos que o cliente faz enquanto espera.
   - Custo direto de resolver = reenvio (CMV + frete) em extravio, avaria, vazamento, falha de personalização
     (reprodução) e rastreio parado há 5 DU ou mais. Item faltante = CMV do item + frete. Devolvido = frete.
4. **Fila de hoje.** Os N maiores custos de esperar. Cada pedido traz o motivo (atraso, faixa de risco atual e futura,
   cliente de alto valor, causa sistêmica) e a ação conforme tipo e status.
5. **Quanto a priorização recupera.** O backlog inteiro é simulado com a mesma capacidade diária, de dois jeitos:
   (a) por ordem de chegada, como é hoje; (b) priorizado, com nova ordenação a cada dia. A premissa é que tratar
   o pedido congela o risco no dia da tratativa. A diferença de perda esperada é o valor recuperado.
6. **Causas sistêmicas.** Detecção genérica, que roda igual com dados novos:
   - Para cada tipo de exceção e cada dimensão plausível (transportadora × região, UF, CD × turno, brinde,
     máquina, lote × SKU, SKU, marca), compara o observado em cada segmento com o esperado.
   - **Não temos o volume total de pedidos**, só os que tiveram exceção. Por isso o esperado usa as outras exceções do
     segmento como base de volume: `esperado = exceções não-T do segmento × (T / não-T no resto)`.
   - Ficam só os segmentos com pelo menos 15 casos, lift ≥ 2, excesso ≥ 10 e Poisson p ≤ 0,0001. Segmentos redundantes são
     removidos e cada pedido entra em no máximo uma causa, para não contar em dobro.
   - Ganho mensal = excesso × custo médio de uma exceção (custo direto + perda esperada na fila priorizada + 1 contato),
     projetado para 30 dias.

## Agente de IA

### O que faz e por que essa ação

O **agente da fila de exceções** pega um pedido da fila priorizada, investiga o caso e deixa a tratativa pronta para uma pessoa revisar:

1. **Diagnóstico** em 2 ou 3 frases, com os números do caso: DU de atraso pelo prazo correto, risco de cancelar hoje e se esperar,
   custo de esperar e causa sistêmica.
2. **Ação recomendada**, de uma lista fechada: `reenviar`, `reembolsar`, `abrir_chamado_transportadora`, `refazer_personalizacao`,
   `contatar_cliente`, `aguardar` e `escalar`. O custo da ação é calculado pelo app, com os parâmetros, e não pelo modelo.
3. **Rascunho da mensagem ao cliente** e, quando faz sentido, **rascunho para a transportadora ou para a área dona da causa**.
   O rascunho cita a causa (ex.: C1, C5), para que a correção de processo seja acompanhada.

Por que essa ação: a fila já diz *quem* tratar hoje (150 pedidos/dia), mas cada tratativa ainda custa cerca de 12 minutos.
Nesse tempo, a pessoa abre o pedido, cruza com o cliente e a transportadora, decide entre reenviar e reembolsar e escreve
duas mensagens. O agente faz esse trabalho com as mesmas contas do app e entrega o caso pronto para revisar.
A decisão continua com a pessoa.

**Modo em lote:** "Processar os N primeiros da fila" (padrão 5, máx. 8) roda o agente em sequência e junta as recomendações e
os rascunhos para revisão. O navegador faz uma requisição por pedido, para cada uma caber no limite de tempo da Vercel.

### Ferramentas (todas chamam o que o motor de análise já calculou)

| Ferramenta | O que devolve | De onde vem |
| --- | --- | --- |
| `buscar_pedido` | tipo, status, datas, prazo correto × sistema, atraso em DU, risco hoje → se esperar, custo de esperar, posição na fila, motivo e ação do roteiro padrão | `enriquecer`, `fila_hoje`, `_motivo`, `_acao` |
| `historico_cliente` | nº de pedidos, receita acumulada, ticket médio, contatos, alto valor, chance de recompra (e se mal resolvida), recompra em risco, outras exceções abertas | `limpar` + `enriquecer` |
| `sla_transportadora` | SLA contratado e frete para a UF, desempenho pelo prazo correto (geral e na UF), alternativas na UF | `transportadoras.csv` limpo + `enriquecer` |
| `causa_sistemica` | se o pedido está numa causa detectada (dono, ação, evidências, lift, ganho/mês), se é "atraso falso" (P1) e quantas exceções do mesmo tipo há nos segmentos dele (transportadora × UF, CD × turno, lote × SKU, máquina) | `causas_sistemicas` |
| `custos_alternativas` | custo em R$ de cada ação: reenvio (CMV + frete; item faltante só o item), reembolso, reprodução, contato e o custo de aguardar | `parametros.md` + `custo_de_esperar` |
| `registrar_recomendacao` | a ação final: valida a lista fechada e marca **pendente de aprovação humana** quando há dinheiro | `agente.py` |

O loop: no 1º passo, o modelo chama as cinco ferramentas de investigação de uma vez; no 2º, registra a recomendação.
Na prática são 2 chamadas ao modelo por pedido, cerca de 4,5 mil tokens e de 15 a 25 s.

### Limites e segurança

- **Ações com dinheiro não são executadas.**
  - `reenviar`, `reembolsar` e `refazer_personalizacao` (que custa o mesmo que um reenvio) saem como **pendente de aprovação humana**.
  - Na interface, uma pessoa aprova ou recusa.
  - Tudo é simulado: nada é enviado e nenhum dinheiro é movimentado. O registro de decisões fica só no navegador (`localStorage`).
- **Lista fechada:** quem recusa uma ação fora da lista é o app, não só o `enum` do esquema. A recusa volta ao modelo como erro.
  Se ele insistir até o fim, o caso sai "sem recomendação".
- **Dados são dados:**
  - Os textos dos CSVs (produto, campos livres) chegam ao modelo como JSON de resultado de ferramenta.
  - Esses textos são limpos de caracteres de controle e cortados em 120 caracteres.
  - O prompt de sistema manda ignorar qualquer instrução que apareça neles.
  - As ferramentas só consultam o pedido em análise.
  - O ID digitado é validado (`[A-Za-z0-9_-]{1,32}`) e a saída é escapada na página.
- **Minimização de dados:** não vão ao modelo o `customer_id` nem a cidade (a UF basta). Os dados não têm nome, e-mail ou telefone.
  A mensagem começa com "Olá, [nome]!", para quem envia completar.
- **Limites:**
  - no máximo 6 passos; no último, só a ferramenta final fica disponível, com `tool_choice: required`;
  - 1.200 `max_tokens` por chamada;
  - timeout de 25 s por chamada e de 50 s por pedido;
  - até 8 ferramentas por passo;
  - entrada de até 4 MB;
  - 12 execuções por minuto por IP.
- **Erros legíveis:** chave recusada, limite do gateway, timeout e gateway fora do ar viram uma mensagem curta na tela.
  A chave nunca aparece em erro, log ou resposta.

### Configurar na Vercel

1. No projeto da Vercel: **Settings → Environment Variables**.
2. Adicione `AI_API_KEY` (a chave, marcada como *Sensitive*), `AI_BASE_URL` = `https://ai-proxy.gogroupbr.com/v1` e
   `AI_MODEL` = `latest`, para os ambientes Production e Preview.
3. Faça um novo deploy (**Deployments → Redeploy**): as variáveis só valem para deploys feitos depois de salvas.
4. Confira em `/api/agente/status` que aparece `"ligado": true`. Sem a chave, o agente aparece como "desligado" e o resto funciona igual.

O `.env` local nunca vai para o repositório nem para a Vercel: está no `.gitignore` e no `.vercelignore`.

## Como usei a IA

- **No desenvolvimento:** usei um assistente de código (Claude) como par de programação. Ele ajudou a estruturar o motor de análise,
  a escrever os testes e a revisar os textos. Os números não vêm da IA: saem do pandas e foram conferidos por testes (prazo em dias
  úteis, deduplicação, priorização, detecção de uma causa plantada) e por recálculo com dados novos.
- **No produto:** o agente de IA descrito acima.
  - A IA cuida do que é linguagem e julgamento do caso: ler o contexto, escolher entre as ações e redigir as mensagens.
  - As contas ficam com o código: prazo, risco, custos e causas vêm das ferramentas.
  - O custo da ação é recalculado pelo app, e a lista de ações é validada fora do modelo.
- **Testes do agente sem rede:** uma IA simulada percorre o loop de ferramentas. Os testes cobrem:
  - ação com dinheiro pendente;
  - ação fora da lista recusada;
  - limite de passos;
  - bloqueio de consulta a outro pedido;
  - limite por IP;
  - o app funcionando sem chave.
- **Validação real:** rodei o agente no gateway em um atraso, uma falha de personalização e um item faltante, e no lote dos
  3 primeiros da fila. As ações com dinheiro ficaram pendentes de aprovação. As rodadas apontaram dois ajustes, já aplicados:
  explicar ao modelo que, no item faltante, o atraso conta até hoje; e não expor detalhes internos ao cliente.

## Principais resultados (dados originais, referência 30/09/2026)

- **2.042 linhas → 2.000 pedidos.** São 42 exceções duplicadas, que só diferem no status. R$ 220 mil em pedidos e 1.242 clientes, 116 deles de alto valor.
- **Dinheiro em risco: R$ 124,7 mil.** São R$ 40,4 mil de reenvios/reproduções que vão acontecer de qualquer jeito, mais R$ 84,3 mil
  de perda esperada (cancelamentos e recompra) se o backlog ficar parado mais 5 DU.
- **Priorização:** no backlog atual, a perda esperada cai de R$ 96,5 mil (ordem de chegada) para R$ 75,2 mil, ou seja,
  **R$ 21,4 mil recuperados** com a mesma equipe. Só a fila de hoje evita R$ 6,8 mil, contra R$ 1,3 mil dos 150 primeiros por ordem de chegada.
- **Prazo errado no sistema:** a data do sistema vem antes da correta em 91% dos pedidos (1,5 DU em média).
  **147 das 497 exceções de "atraso_entrega" não estão atrasadas** pelo prazo correto: 75 foram entregues no prazo e 72 estão em trânsito dentro do prazo.
  Além disso, 234 atrasos já foram entregues e só precisam ser encerrados.
- **Causas sistêmicas:** 7 causas explicam 944 casos (47%), com 853 acima do esperado em 58 dias.
  O ganho estimado é **R$ 30,8 mil/mês** e cerca de **540 tratativas/mês a menos**. As tratativas incluem as 98 que a correção do prazo e das duplicatas libera.

| # | Causa | Casos (esperado) | Ganho/mês | Dono | Ação |
| --- | --- | --- | --- | --- | --- |
| C1 | Atraso + rastreio parado · Rota Express no Nordeste (PB, PE, BA, CE, RN) | 396 (45) | R$ 8,1 mil | Logística (transportadoras) | Os pedidos entregues chegam 3,5 DU depois do prazo, e a situação piora desde a semana de 31/08 (33 → 63 por semana). Cobrar plano de recuperação de SLA com multa; se não normalizar, migrar para a Veloz Log (mesmo SLA, +R$ 3,22 de frete por pedido). |
| C2 | Item faltante · CD2, turno da noite | 210 (11) | R$ 7,5 mil | Operação do CD2 | 75% dos casos tinham brinde de campanha. Checklist de brinde e conferência por peso. |
| C3 | Avaria · Carga Norte | 98 (14) | R$ 5,1 mil | Logística + Embalagem | Pedir ressarcimento e auditar o manuseio. Não despachar frágeis por ela. |
| C4 | Vazamento · lote L2607-B do APC-1067 (Cond. Anti-Frizz 1000 ml) | 74 (≈0) | R$ 3,7 mil | Qualidade + Compras | Bloquear o lote e cobrar o fornecedor. |
| C5 | Falha de personalização · impressora IMP-02 | 77 (10) | R$ 3,3 mil | Produção / Manutenção | Começou na semana de 24/08 (3 → 14 por semana). Parar e calibrar a máquina; conferência pós-impressão. |
| C6 | Avaria · Lescent (colônias de vidro), em todas as transportadoras | 30 (3) | R$ 1,7 mil | Embalagem da marca | Calço/colmeia e caixa reforçada. |
| C7 | Ruptura pós-venda · Kokeshi | 59 (7) | R$ 1,3 mil | Estoque + E-commerce | Sincronizar estoque e bloquear venda sem saldo. |
| P1 | Data prometida em dias corridos | 147 atrasos falsos | 76 tratativas/mês | TI / Produto | Corrigir o cálculo e reprocessar os pedidos em aberto. |
| P2 | Exceções duplicadas | 42 | 22 tratativas/mês | TI / CRM | Chave única por pedido. |

**Caso a caso (1.056 pedidos)**: o restante não tem concentração e segue um roteiro padrão por tipo (está na aba Causas).
Dois tipos podem sair da fila do time com automação: **pagamento pendente** (novo link Pix + lembrete) e **endereço divergente**
(confirmação por WhatsApp e validação de CEP no checkout).

## Premissas próprias (não estão no `parametros.md`; todas editáveis)

- **Horizonte de espera de 5 DU**: se não for tratado hoje, o pedido espera cerca de uma semana útil.
- **0,2 contato por DU de espera**: cerca de 1 contato a cada 5 DU, a R$ 9 cada.
- **Rastreio parado** com 5 DU ou mais de atraso é tratado como extravio (reenvio).
- **Avaria, vazamento e item faltante**: o "atraso" do cliente conta até a data de referência, porque ele segue sem o produto certo.
- **Recompra perdida** = metade da probabilidade de recompra × ticket médio × margem bruta, ponderada pela probabilidade de cancelar.
  A exceção mal resolvida foi equiparada ao cancelamento.
- **Duplicatas:** fica o status mais avançado (aguardando_* > em_analise > aberta), porque não há carimbo de data/hora.
- **A tratativa congela o risco** no dia em que acontece. Isso é usado só na simulação de recuperação.

## Limitações e riscos

- **Sem o volume total de pedidos** não dá para calcular taxas de exceção por transportadora, CD ou máquina. O "esperado" das
  concentrações usa as outras exceções como base de volume. Isso é bom para achar desvios fortes, mas é aproximado: se um
  segmento tem problemas em todos os tipos ao mesmo tempo, o método subestima.
- **O R$ depende das premissas acima.** A tabela de cancelamento é a do negócio, mas a eficácia real de cada tratativa não está
  nos dados. O valor "recuperado" compara duas ordens de atendimento com o mesmo modelo; não é uma medição.
- **Pedidos com mais de 10 DU de atraso já estão no teto da tabela (40%).** Pela tabela, esperar não piora o risco deles, por isso
  não aparecem no topo da fila. Eles continuam no backlog e são tratados assim que as urgências acabam (14 DU no total).
  Se o negócio achar que o risco continua subindo depois de 10 DU, basta editar a tabela de faixas.
- **Backlog incoerente com a entrada:** entram cerca de 49 exceções por DU e a capacidade é 150 por dia, mas há 2.000 abertas.
  Ou cada exceção exige várias tratativas, ou a capacidade efetiva é menor. Vale medir antes de tirar conclusões sobre headcount.
- **Migrar o volume da Rota Express** só compensa se o ganho com menos exceções superar +R$ 3,22 por pedido. Com o ganho
  estimado (R$ 8,1 mil/mês), o ponto de equilíbrio fica em torno de 2.500 pedidos/mês da Rota no Nordeste. Esse volume precisa ser confirmado.
- **Dados sintéticos;** a causa de cada concentração (ex.: calibração da IMP-02) é hipótese a validar no chão de fábrica.
- **Estado em memória:** reiniciar o app volta aos dados da pasta `dados/`.
- **Agente de IA:**
  - **Limite por IP:** fica em memória e conta por instância; na Vercel, cada instância conta separado e o IP vem de
    `x-forwarded-for`. É uma proteção básica, não um controle de abuso de verdade, que exigiria armazenamento compartilhado
    (Redis/KV) ou firewall.
  - **Registro de aprovações:** fica só no navegador. Não há trilha de auditoria no servidor nem integração com o atendimento.
  - **Erros do modelo:** ele pode errar o tom ou escolher uma ação discutível, e as respostas variam um pouco entre execuções.
    Por isso tudo passa por revisão, com o roteiro padrão do app ao lado como referência.
  - **Upload:** cada execução reenvia os arquivos e pode recalcular a análise (cerca de 4 s). Há um cache pequeno por instância.
