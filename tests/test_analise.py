import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analise as A  # noqa: E402

FER = ["2026-09-07", "2026-10-12"]


# ---------------------------------------------------------------- dias úteis / prazo
@pytest.mark.parametrize("pedido,sla,esperado", [
    ("2026-08-12", 3, "2026-08-18"),  # qua -> qui(1 separação), sex, seg, ter(4)
    ("2026-09-25", 0, "2026-09-28"),  # sexta: segunda é o dia 1
    ("2026-09-26", 0, "2026-09-28"),  # sábado: segunda é o dia 1
    ("2026-09-27", 0, "2026-09-28"),  # domingo: segunda é o dia 1
    ("2026-09-04", 1, "2026-09-09"),  # sexta antes do feriado de 07/09: ter(1), qua(2)
    ("2026-09-06", 0, "2026-09-08"),  # domingo antes do feriado: terça é o dia 1
    ("2026-10-09", 2, "2026-10-15"),  # feriado 12/10: ter(1), qua(2), qui(3)
])
def test_prazo_correto(pedido, sla, esperado):
    r = A.prazo_correto([pedido], [sla], FER, 1)
    assert str(r[0]) == esperado


def test_prazo_respeita_feriados_parametrizados():
    sem = A.prazo_correto(["2026-09-04"], [1], [], 1)
    com = A.prazo_correto(["2026-09-04"], [1], FER, 1)
    assert str(sem[0]) == "2026-09-08" and str(com[0]) == "2026-09-09"


@pytest.mark.parametrize("prom,fim,esperado", [
    ("2026-08-18", "2026-08-18", 0),
    ("2026-08-18", "2026-08-19", 1),
    ("2026-08-18", "2026-08-24", 4),
    ("2026-09-04", "2026-09-08", 1),   # pula sábado, domingo e o feriado de 07/09
    ("2026-08-20", "2026-08-18", -2),  # entregue antes do prazo
])
def test_dias_uteis_atraso(prom, fim, esperado):
    assert int(A.dias_uteis_atraso([prom], [fim], FER)[0]) == esperado


def test_prob_cancelamento_faixas():
    faixas = A.DEFAULT_PARAMS["faixas_cancelamento"]
    d = [-3, 0, 1, 2, 3, 5, 6, 10, 11, 40]
    assert list(A.prob_cancelamento(d, faixas)) == [0, 0, .04, .04, .10, .10, .22, .22, .40, .40]


# ---------------------------------------------------------------- dados sintéticos
def _pedido(oid, **kw):
    base = dict(order_id=oid, customer_id="CL1", marca="Ápice", data_pedido="2026-09-21", valor="100.00",
                sku="APC-1", descricao_produto="Produto", qtd_itens="2", brinde_no_pedido="False",
                tipo_excecao="atraso_entrega", status="aberta", transportadora="T1", uf="SP", cidade="São Paulo",
                data_prometida="2026-09-25", data_entrega="", maquina="", turno="manhã", cd="CD1",
                lote_produto="L1", data_abertura_excecao="2026-09-28")
    base.update(kw)
    return base


def _clientes(ids=("CL1", "CL2")):
    return pd.DataFrame([dict(customer_id=c, n_pedidos="4", receita_acumulada="400", data_primeiro_pedido="2025-01-01",
                              n_contatos_atendimento="1") for c in ids])


def _transp():
    return pd.DataFrame([dict(transportadora="T1", uf_atendida="SP", sla_contratado_dias_uteis="3",
                              custo_medio_frete="12.00")])


def _params(**kw):
    p = A.mesclar_params(kw)
    p["_ref"] = kw.get("data_referencia", "2026-09-30")
    return p


def test_deduplicacao_mantem_status_mais_avancado():
    ped = pd.DataFrame([_pedido("GB1", status="aberta"), _pedido("GB1", status="em_analise"), _pedido("GB2")])
    p, _, _, q = A.limpar(ped, _clientes(), _transp(), _params())
    assert len(p) == 2
    assert p.loc[p.order_id == "GB1", "status"].item() == "em_analise"
    reg = [x for x in q if x["verificacao"].startswith("order_id duplicado (")][0]
    assert reg["linhas"] == 1 and "status" in reg["detalhe"]


def test_linhas_identicas_removidas_e_valor_invalido_descartado():
    ped = pd.DataFrame([_pedido("GB1"), _pedido("GB1"), _pedido("GB3", valor="abc")])
    p, _, _, q = A.limpar(ped, _clientes(), _transp(), _params())
    assert list(p.order_id) == ["GB1"]
    assert any(x["verificacao"] == "Linhas 100% idênticas" and x["linhas"] == 1 for x in q)


def test_colunas_obrigatorias():
    ped = pd.DataFrame([_pedido("GB1")]).drop(columns=["valor"])
    with pytest.raises(ValueError, match="valor"):
        A.limpar(ped, _clientes(), _transp(), _params())


def test_atraso_falso_pelo_prazo_correto():
    # Pedido na sexta 18/09, SLA 3: correto = seg(1) ter qua qui(4) = 24/09; sistema = 18/09 + 4 corridos = 22/09.
    # Entregue em 24/09: atrasado pelo sistema, no prazo pela regra.
    ped = pd.DataFrame([_pedido("GB1", data_pedido="2026-09-18", data_prometida="2026-09-22", data_entrega="2026-09-24")])
    params = _params()
    p, _, _, _ = A.limpar(ped, _clientes(), _transp(), params)
    p = A.enriquecer(p, params)
    r = p.iloc[0]
    assert str(r.prometida_correta.date()) == "2026-09-24"
    assert r.atraso_sistema_du == 2 and r.atraso_entrega_du == 0
    assert bool(r.atraso_falso)
    assert r.custo_de_esperar == 0  # já entregue: não ganha nada em tratar hoje


# ---------------------------------------------------------------- priorização
def _base_priorizacao():
    linhas = [
        # entregue com atraso: risco encerrado
        _pedido("GB1", data_pedido="2026-09-10", data_entrega="2026-09-22", valor="300"),
        # 2 DU de atraso (prazo 28/09 -> ref 30/09) e sem entrega: vai cruzar a faixa de 4% para 22% em 5 DU
        _pedido("GB2", data_pedido="2026-09-21", data_prometida="2026-09-25", valor="200"),
        # 20 DU de atraso: já no teto de 40%, esperar não muda a probabilidade
        _pedido("GB3", data_pedido="2026-08-20", tipo_excecao="rastreio_parado", valor="200"),
        # endereço divergente com prazo no futuro: bloqueia o despacho
        _pedido("GB4", data_pedido="2026-09-29", tipo_excecao="endereco_divergente", valor="200"),
    ]
    return pd.DataFrame(linhas)


def test_priorizacao_ordem_e_capacidade():
    res = A.analisar(_base_priorizacao(), _clientes(), _transp(), {"capacidade_diaria": 2, "data_referencia": "2026-09-30"})
    fila = res["fila"]
    assert len(fila) == 2
    ids = [r["order_id"] for r in fila]
    assert ids[0] == "GB2"            # maior custo de esperar (cruza faixas)
    assert "GB1" not in ids           # já entregue: fica para depois
    assert all(fila[i]["custo_de_esperar"] >= fila[i + 1]["custo_de_esperar"] for i in range(len(fila) - 1))
    assert all(r["motivo"] and r["acao_recomendada"] for r in fila)


def test_priorizado_nao_perde_mais_que_fifo():
    res = A.analisar(*A.carregar_padrao(os.path.join(os.path.dirname(__file__), "..", "dados")),
                     {"capacidade_diaria": 100})
    r = res["resumo"]
    assert len(res["fila"]) == 100
    assert r["perda_backlog_prioridade"] <= r["perda_backlog_fifo"]


def test_capacidade_altera_fila():
    a = A.analisar(_base_priorizacao(), _clientes(), _transp(), {"capacidade_diaria": 1})
    b = A.analisar(_base_priorizacao(), _clientes(), _transp(), {"capacidade_diaria": 3})
    assert len(a["fila"]) == 1 and len(b["fila"]) == 3


# ---------------------------------------------------------------- concentração
def test_detecta_concentracao_plantada():
    rng = np.random.default_rng(0)
    linhas = []
    tipos = ["atraso_entrega", "pagamento_pendente", "endereco_divergente", "falha_personalizacao"]
    for i in range(400):
        maq = f"IMP-0{rng.integers(1, 7)}"
        tipo = tipos[rng.integers(0, len(tipos))]
        linhas.append(_pedido(f"GC{i}", marca="Gocase", maquina=maq, tipo_excecao=tipo))
    for i in range(60):  # falhas plantadas na IMP-02
        linhas.append(_pedido(f"GCX{i}", marca="Gocase", maquina="IMP-02", tipo_excecao="falha_personalizacao"))
    p, _, t, _ = A.limpar(pd.DataFrame(linhas), _clientes(), _transp(), _params())
    p = A.enriquecer(p, _params())
    a = A.detectar_concentracoes(p, _params())
    top = a[a.tipo_excecao == "falha_personalizacao"].iloc[0]
    assert top.dimensao == "máquina" and top.segmento == "IMP-02"


# ---------------------------------------------------------------- dados reais (regressão)
def test_dados_originais_achados_principais():
    res = A.analisar(*A.carregar_padrao(os.path.join(os.path.dirname(__file__), "..", "dados")))
    r = res["resumo"]
    assert r["linhas_brutas"] == 2042 and r["pedidos_em_excecao"] == 2000
    assert r["duplicatas_removidas"] == 42
    assert r["atrasos_falsos"] == 147
    rot = " ".join(c["rotulo"] for c in res["causas"])
    for esperado in ["Rota Express | Nordeste", "CD2 | noite", "IMP-02", "L2607-B | APC-1067", "Carga Norte", "Kokeshi"]:
        assert esperado in rot
