# Dicionário de dados: Operações

Todos os dados são sintéticos. Transportadoras e clientes são fictícios.

## pedidos_excecao.csv (uma linha por exceção aberta)

| Campo | Descrição |
| --- | --- |
| order_id | Identificador do pedido (GB = GoBeauté, GC = Gocase) |
| customer_id | Identificador do cliente (ver clientes.csv) |
| marca | Marca (Gocase ou marcas GoBeauté) |
| data_pedido | Data do pedido |
| valor | Valor pago (R$) |
| sku, descricao_produto | Produto principal |
| qtd_itens | Quantidade de itens no pedido |
| brinde_no_pedido | Se o pedido incluía brinde de campanha |
| tipo_excecao | Tipo da exceção registrada: atraso_entrega, extravio, rastreio_parado, devolvido_transportadora, avaria_transporte, vazamento_produto, item_faltante, falha_personalizacao, pagamento_pendente, ruptura_pos_venda ou endereco_divergente |
| status | Situação da tratativa |
| transportadora | Transportadora responsável |
| uf, cidade | Destino |
| data_prometida | Data prometida ao cliente, calculada pelo sistema |
| data_entrega | Data de entrega, se já entregue |
| maquina | Máquina de impressão que produziu a personalização (somente pedidos Gocase; vazio nos demais) |
| turno | Turno em que o pedido foi produzido e separado (manhã, tarde ou noite) |
| cd | Centro de distribuição que separou o pedido |
| lote_produto | Lote de fabricação do produto principal |
| data_abertura_excecao | Data em que a exceção foi aberta |

## clientes.csv

| Campo | Descrição |
| --- | --- |
| customer_id | Identificador do cliente |
| n_pedidos | Pedidos feitos desde o primeiro pedido |
| receita_acumulada | Receita acumulada do cliente (R$) |
| data_primeiro_pedido | Data do primeiro pedido |
| n_contatos_atendimento | Contatos com o atendimento nos últimos 12 meses |

## transportadoras.csv

| Campo | Descrição |
| --- | --- |
| transportadora | Nome |
| uf_atendida | Estado de destino |
| sla_contratado_dias_uteis | Prazo contratado, em dias úteis |
| custo_medio_frete | Frete médio por pedido (R$) |
