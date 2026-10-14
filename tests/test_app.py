import io
import os
import sys

import pandas as pd
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as APP  # noqa: E402

DADOS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dados")


def _subconjunto(n=300, desloc=5):
    p = pd.read_csv(os.path.join(DADOS, "pedidos_excecao.csv"), dtype=str, keep_default_na=False).sample(n, random_state=1)
    for c in ["data_pedido", "data_prometida", "data_entrega", "data_abertura_excecao"]:
        d = pd.to_datetime(p[c].replace("", None)) + pd.Timedelta(days=desloc)
        p[c] = d.dt.strftime("%Y-%m-%d").fillna("")
    c = pd.read_csv(os.path.join(DADOS, "clientes.csv"), dtype=str, keep_default_na=False)
    c = c[c.customer_id.isin(p.customer_id)]
    return p, c


def test_endpoints_upload_e_parametros():
    with TestClient(APP.app) as cli:
        assert cli.get("/").status_code == 200
        r = cli.get("/api/analise").json()
        assert r["resumo"]["pedidos_em_excecao"] == 2000 and len(r["fila"]) == 150

        p, c = _subconjunto()
        files = {"pedidos": ("p.csv", p.to_csv(index=False, sep=";").encode("utf-8"), "text/csv"),
                 "clientes": ("c.csv", c.to_csv(index=False).encode("latin-1"), "text/csv")}
        r = cli.post("/api/upload", files=files)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["resumo"]["pedidos_em_excecao"] <= 300
        assert j["resumo"]["data_referencia"] == "2026-10-05"  # 'auto' = última abertura deslocada
        assert len(j["fila"]) == min(150, j["resumo"]["pedidos_em_excecao"])

        r = cli.post("/api/parametros", json={"capacidade_diaria": 100})
        assert r.status_code == 200 and len(r.json()["fila"]) == 100
        assert r.json()["params"]["capacidade_diaria"] == 100

        ruim = {"pedidos": ("p.csv", b"a,b\n1,2\n", "text/csv"), "clientes": ("c.csv", b"x\n1\n", "text/csv")}
        assert cli.post("/api/upload", files=ruim).status_code == 400

        assert cli.post("/api/reset").json()["resumo"]["pedidos_em_excecao"] == 2000
        assert cli.get("/api/fila.csv").status_code == 200
