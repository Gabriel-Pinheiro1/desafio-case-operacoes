# Parâmetros de negócio: Operações Gocase e GoBeauté

Data de referência: 30/09/2026. Exceções abertas de pedidos feitos entre 01/08/2026 e 27/09/2026.

## Capacidade do time

- 150 tratativas por dia, somando todos os tipos de exceção.
- Tempo médio de uma tratativa: 12 minutos.

## Custos

| Item | Valor |
| --- | --- |
| Custo da mercadoria (CMV) | 24% do valor do pedido, em média |
| Custo de reenvio | CMV do pedido + frete médio da transportadora (ver transportadoras.csv) |
| Custo de reembolso | Valor pago pelo cliente |
| Custo de um contato de atendimento | R$ 9,00 (média ponderada dos canais: WhatsApp, e-mail e Reclame Aqui) |

## Risco de cancelamento por atraso

Probabilidade de o cliente cancelar e pedir reembolso, conforme os dias úteis de atraso em relação ao prazo prometido:

| Dias úteis de atraso | Probabilidade de cancelamento |
| --- | --- |
| 1 a 2 | 4% |
| 3 a 5 | 10% |
| 6 a 10 | 22% |
| Mais de 10 | 40% |

## Valor do cliente

- Clientes com receita acumulada acima de R$ 1.500 recompram em 12 meses em 70% dos casos.
- Os demais recompram em 28% dos casos.
- Um cliente que passa por uma exceção mal resolvida tem a probabilidade de recompra reduzida pela metade.

## Prazos

- O prazo exibido ao cliente no checkout é de 1 dia útil de separação mais o SLA contratado com a transportadora, também em dias úteis.
- Feriados nacionais no período: 07/09/2026 e 12/10/2026.
- A contagem começa no primeiro dia útil depois do pedido, que é o dia de separação. Exemplo: em um pedido feito na sexta, no sábado ou no domingo, a segunda-feira seguinte é o dia 1.

## Operação

- Dois centros de distribuição (CD1 e CD2), com três turnos de separação cada (manhã, tarde e noite).
- Pedidos de campanhas promocionais podem incluir brinde, separado junto com os itens comprados.
- Pedidos Gocase são personalizados (nome, foto ou iniciais) antes da separação, em seis impressoras: IMP-01 a IMP-03 no CD1 e IMP-04 a IMP-06 no CD2. Uma falha de personalização exige produzir o item de novo, com o mesmo custo de um reenvio.
