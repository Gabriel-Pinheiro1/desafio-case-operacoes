# Mesa de exceções: Problema 2 (Operações)

Aplicação local que **prioriza, investiga e ajuda a resolver** os pedidos com exceção da Gocase e da GoBeauté.
Ela responde às quatro perguntas do desafio com números em R$ e recalcula tudo quando chegam CSVs novos
(no mesmo formato) ou quando um parâmetro muda: capacidade, custos, feriados, tabela de cancelamento etc.

## Como rodar

```bash
# 1) uma vez: criar o ambiente e instalar as dependências
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt      # Windows
# .venv/bin/python -m pip install -r requirements.txt        # Linux/macOS

# 2) subir o app
.venv\Scripts\python app.py
# abra http://127.0.0.1:8765   (outra porta: set PORT=9000)

# testes
.venv\Scripts\python -m pytest -q
```

Python 3.11, pandas, FastAPI e uvicorn. Os gráficos usam Chart.js e as fontes vêm do Google Fonts, ambos por CDN.
Sem internet a página continua funcionando: os números aparecem nas tabelas e no lugar dos gráficos surge um aviso.
Nada sai do computador: não há chamadas a APIs externas e não há recurso de IA ligado.

## O que tem na interface

| Aba | Conteúdo |
| --- | --- |
| **Visão executiva** | Dinheiro em risco, quanto a priorização recupera, ganho mensal se as causas forem corrigidas, principais achados em R$, evolução semanal e o método. |
| **Fila de hoje** | Os N pedidos (N = capacidade diária) que devem ser tratados hoje, com o motivo da prioridade e a ação recomendada. Tem filtros e exportação em CSV. |
| **Causas** | Causas sistêmicas (dono, ação, evidências, ganho estimado), falhas de sistema e roteiro caso a caso por tipo. Traz também as transportadoras medidas pelo prazo correto e pelo prazo do sistema. |
| **Parâmetros e dados** | Todos os parâmetros do `parametros.md`, mais as premissas próprias (marcadas), editáveis. Upload de `pedidos_excecao.csv` + `clientes.csv`, com `transportadoras.csv` opcional. |
| **Qualidade dos dados** | As 16 verificações que rodam a cada carga: o que foi encontrado, o que foi feito e o motivo. Inclui as concentrações brutas, para auditoria. |

API: `GET /api/analise`, `GET /api/backlog`, `POST /api/parametros` (JSON, aceita atualização parcial),
`POST /api/upload` (multipart: `pedidos`, `clientes`, `transportadoras` opcional, `params` opcional), `POST /api/reset`, `GET /api/fila.csv`.

## Estrutura

```
analise.py            motor de análise (funções puras, testáveis)
app.py                servidor FastAPI + estado em memória
static/index.html     interface (HTML/CSS/JS puro + Chart.js)
tests/                pytest: dias úteis/prazo, deduplicação, priorização, detecção de causas, API
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
