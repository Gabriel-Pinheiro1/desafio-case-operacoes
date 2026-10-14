# Resumo: Problema 2 (Operações)

**Problema.** São 2.000 pedidos com exceção aberta (R$ 220 mil, 1.242 clientes), tratados por ordem de chegada por um time de 150 tratativas/dia.
Construí uma mesa de exceções local (Python + FastAPI). Ela recalcula a fila e os números quando chegam dados novos ou quando um parâmetro muda.

**Dois problemas nos próprios dados**
- **A data prometida do sistema está em dias corridos**, mas a regra é 1 DU de separação + SLA em dias úteis, com feriados.
  O efeito: 91% dos pedidos têm prazo antecipado, 147 das 497 exceções de atraso (30%) não são atraso de verdade e o SLA das transportadoras é medido errado.
- **42 exceções duplicadas** (o mesmo pedido, com status diferentes). Seriam tratadas duas vezes.

**Quanto está em risco e quanto se recupera**
- Em risco: **R$ 124,7 mil**. São R$ 40,4 mil de reenvios/reproduções inevitáveis e R$ 84,3 mil de perda esperada (cancelamento + recompra) se a fila parar 5 DU.
- **Priorizar pelo custo de esperar**, isto é, quanto a perda esperada cresce se o pedido não for tratado hoje:
  a perda esperada do backlog cai de R$ 96,5 mil para R$ 75,2 mil. São **R$ 21,4 mil recuperados com a mesma equipe**.
  A fila de hoje (150 pedidos) evita R$ 6,8 mil, contra R$ 1,3 mil pela ordem de chegada.
- **Corrigir as 7 causas sistêmicas: cerca de R$ 30,8 mil/mês** e cerca de 540 tratativas/mês a menos.

**O que causa as exceções: 47% dos casos vêm de 7 causas de processo (recomendações por ordem de ganho)**

| # | Causa | Casos (esperado) | Ganho/mês | Dono | Ação |
| --- | --- | --- | --- | --- | --- |
| 1 | Rota Express no Nordeste (atraso + rastreio parado), piorando desde 31/08 | 396 (45) | R$ 8,1 mil | Logística | Plano de SLA com multa; se não normalizar em 2 semanas, migrar para a Veloz Log (mesmo SLA, +R$ 3,22/pedido) |
| 2 | Item faltante no CD2, turno da noite (75% com brinde) | 210 (11) | R$ 7,5 mil | Operação do CD2 | Checklist de brinde + conferência por peso |
| 3 | Avaria na Carga Norte | 98 (14) | R$ 5,1 mil | Logística + Embalagem | Ressarcimento, auditoria, frágeis fora dela |
| 4 | Vazamento: lote L2607-B do Cond. Anti-Frizz 1000 ml | 74 (≈0) | R$ 3,7 mil | Qualidade + Compras | Bloquear lote, cobrar fornecedor |
| 5 | Falha de personalização na IMP-02, desde 24/08 | 77 (10) | R$ 3,3 mil | Produção | Manutenção + conferência pós-impressão |
| 6 | Avaria em colônias Lescent (vidro), todas as transportadoras | 30 (3) | R$ 1,7 mil | Embalagem | Calço e caixa reforçada |
| 7 | Ruptura pós-venda Kokeshi | 59 (7) | R$ 1,3 mil | Estoque + E-commerce | Bloquear venda sem saldo |
| — | Prazo em dias corridos; duplicatas | 147; 42 | 98 tratativas/mês | TI / Produto | Corrigir o cálculo; chave única por pedido |

**Caso a caso (os outros 53%)**: roteiro padrão por tipo, com a ação sugerida em cada linha da fila.
Pagamento pendente e endereço divergente podem ser automatizados (link Pix/lembrete e confirmação por WhatsApp) e saem da fila do time.

**Agente de IA (ação útil, não chat).**
- Para cada pedido da fila, ou para os N primeiros em lote, o agente consulta com ferramentas o que a análise já calculou:
  pedido, cliente, SLA da transportadora na UF, causa sistêmica e custo de cada alternativa.
- Ele devolve:
  - um diagnóstico com números;
  - uma ação de uma lista fechada (reenviar, reembolsar, chamado na transportadora, refazer personalização, contatar, aguardar, escalar);
  - os rascunhos das mensagens ao cliente e à transportadora ou à área dona da causa.
- Reenvio, reembolso e reprodução ficam **pendentes de aprovação humana**. Tudo é simulado.
- Em geral são 2 chamadas ao modelo por pedido (cerca de 20 s), com limites de passos, tokens, tempo e uso por IP.
  Sem chave de IA, o resto do app funciona igual.

**Limites.** Não temos o volume total de pedidos, então as concentrações usam as outras exceções como base de volume.
Os valores em R$ dependem de premissas declaradas e editáveis (horizonte de 5 DU, contatos durante a espera, recompra).
O valor recuperado compara duas ordens de atendimento num mesmo modelo; não é uma medição. Cada causa é uma hipótese forte a validar na operação.
