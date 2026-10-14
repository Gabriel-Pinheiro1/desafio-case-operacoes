"""Gera um 'dado novo': 300 pedidos com datas deslocadas +5 dias, clientes correspondentes,
separador ';' e uma linha duplicada de propósito."""
import os
import sys

import pandas as pd

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
p = pd.read_csv(os.path.join(src, "pedidos_excecao.csv"), dtype=str, keep_default_na=False)
p = p.drop_duplicates("order_id").sample(300, random_state=42)
for c in ["data_pedido", "data_prometida", "data_entrega", "data_abertura_excecao"]:
    p[c] = (pd.to_datetime(p[c].replace("", None)) + pd.Timedelta(days=5)).dt.strftime("%Y-%m-%d").fillna("")
p = pd.concat([p, p.iloc[[0]].assign(status="em_analise")])  # duplicata proposital
c = pd.read_csv(os.path.join(src, "clientes.csv"), dtype=str, keep_default_na=False)
c = c[c.customer_id.isin(p.customer_id)]
p.to_csv(os.path.join(out, "pedidos_excecao_novo.csv"), index=False, sep=";")
c.to_csv(os.path.join(out, "clientes_novo.csv"), index=False)
print(len(p), "linhas de pedidos;", len(c), "clientes; datas", p.data_pedido.min(), "a", p.data_pedido.max())
