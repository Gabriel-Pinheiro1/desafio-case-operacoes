"""
Motor de análise das exceções de pedidos (Problema 2: Operações).

Tudo aqui é função pura sobre DataFrames + um dicionário de parâmetros, para que
a mesma lógica rode com dados novos no mesmo formato e com parâmetros alterados,
sem mexer em código. A interface web (app.py) só chama `analisar(...)`.

Visão geral do método
---------------------
1. Leitura e limpeza (`limpar`): normaliza texto, valida tipos, remove duplicatas
   de order_id, cruza com clientes e transportadoras e registra cada problema
   encontrado numa lista de "qualidade dos dados".
2. Prazo correto (`prazo_correto`): recalcula a data prometida pela regra do
   parametros.md (1 DU de separação + SLA em dias úteis, contando a partir do
   primeiro dia útil depois do pedido, pulando fins de semana e feriados) e
   compara com a data_prometida do sistema.
3. Risco por pedido (`enriquecer`): atraso em DU contra o prazo correto,
   probabilidade de cancelamento pela tabela, valor do cliente (recompra),
   custo direto de resolver (reenvio / reprodução) e o "custo de esperar":
   quanto a perda esperada cresce se o pedido não for tratado hoje.
4. Fila de hoje (`fila_hoje`): os N pedidos (N = capacidade diária) com maior
   custo de esperar, com motivo e ação recomendada.
5. Simulação (`simular`): esvazia o backlog com a mesma capacidade em ordem de
   chegada (como é hoje) e na ordem priorizada, e compara a perda esperada.
6. Causas sistêmicas (`detectar_concentracoes` + `causas_sistemicas`): procura,
   de forma genérica, segmentos (transportadora x região, CD x turno, máquina,
   lote x SKU, marca, SKU...) em que um tipo de exceção aparece muito acima do
   esperado, quantifica o excesso em R$ e atribui dono e ação.
"""
from __future__ import annotations

import io
import math
import unicodedata
from copy import deepcopy
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Parâmetros (todos editáveis pela interface)
# ---------------------------------------------------------------------------
DEFAULT_PARAMS: dict[str, Any] = {
    # "auto" = maior data_abertura_excecao do arquivo
    "data_referencia": "2026-09-30",
    "capacidade_diaria": 150,
    "minutos_por_tratativa": 12,
    "cmv_pct": 0.24,
    "custo_contato": 9.0,
    "feriados": ["2026-09-07", "2026-10-12"],
    "dias_separacao": 1,
    # [min_du, max_du (None = sem limite), probabilidade]
    "faixas_cancelamento": [[1, 2, 0.04], [3, 5, 0.10], [6, 10, 0.22], [11, None, 0.40]],
    "limiar_cliente_alto_valor": 1500.0,
    "recompra_alto_valor": 0.70,
    "recompra_demais": 0.28,
    "fator_recompra_mal_resolvida": 0.5,
    # Premissas próprias (não estão no parametros.md; declaradas no README)
    "horizonte_espera_du": 5,          # "se não tratar hoje, o pedido espera ~1 semana útil"
    "contatos_por_du_espera": 0.2,     # cliente sem resposta procura o atendimento ~1x a cada 5 DU
    "rastreio_parado_vira_extravio_du": 5,  # parado e >= 5 DU após o prazo: tratar como extravio
    # Detecção de concentração
    "conc_min_casos": 15,
    "conc_min_lift": 2.0,
    "conc_min_excesso": 10,
    "conc_p_max": 0.0001,
}

TIPOS_VALIDOS = [
    "atraso_entrega", "extravio", "rastreio_parado", "devolvido_transportadora",
    "avaria_transporte", "vazamento_produto", "item_faltante", "falha_personalizacao",
    "pagamento_pendente", "ruptura_pos_venda", "endereco_divergente",
]

COLS_PEDIDOS = [
    "order_id", "customer_id", "marca", "data_pedido", "valor", "sku", "descricao_produto",
    "qtd_itens", "brinde_no_pedido", "tipo_excecao", "status", "transportadora", "uf",
    "cidade", "data_prometida", "data_entrega", "maquina", "turno", "cd", "lote_produto",
    "data_abertura_excecao",
]
COLS_CLIENTES = ["customer_id", "n_pedidos", "receita_acumulada", "data_primeiro_pedido",
                 "n_contatos_atendimento"]
COLS_TRANSP = ["transportadora", "uf_atendida", "sla_contratado_dias_uteis", "custo_medio_frete"]

# Ordem de "avanço" do status, usada para escolher qual linha manter numa duplicata.
ORDEM_STATUS = {"aberta": 0, "em_analise": 1, "aguardando_cliente": 2, "aguardando_transportadora": 2}

REGIAO_UF = {
    **dict.fromkeys(["AC", "AM", "AP", "PA", "RO", "RR", "TO"], "Norte"),
    **dict.fromkeys(["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"], "Nordeste"),
    **dict.fromkeys(["DF", "GO", "MS", "MT"], "Centro-Oeste"),
    **dict.fromkeys(["ES", "MG", "RJ", "SP"], "Sudeste"),
    **dict.fromkeys(["PR", "RS", "SC"], "Sul"),
}

# Exceções que exigem mandar produto de novo (custo = CMV + frete, parametros.md).
TIPOS_REENVIO = {"extravio", "avaria_transporte", "vazamento_produto", "falha_personalizacao"}
# Exceções em que o cliente ainda não tem o produto certo: o atraso continua crescendo.
TIPOS_TRANSPORTE = {"atraso_entrega", "extravio", "rastreio_parado", "devolvido_transportadora",
                    "avaria_transporte"}

ROTULO_TIPO = {
    "atraso_entrega": "Atraso na entrega", "extravio": "Extravio", "rastreio_parado": "Rastreio parado",
    "devolvido_transportadora": "Devolvido pela transportadora", "avaria_transporte": "Avaria no transporte",
    "vazamento_produto": "Vazamento de produto", "item_faltante": "Item faltante",
    "falha_personalizacao": "Falha de personalização", "pagamento_pendente": "Pagamento pendente",
    "ruptura_pos_venda": "Ruptura pós-venda", "endereco_divergente": "Endereço divergente",
}


def mesclar_params(params: dict | None) -> dict:
    p = deepcopy(DEFAULT_PARAMS)
    if params:
        for k, v in params.items():
            if k in p and v is not None and v != "":
                p[k] = v
    # tipos
    p["capacidade_diaria"] = max(0, int(float(p["capacidade_diaria"])))
    for k in ["cmv_pct", "custo_contato", "limiar_cliente_alto_valor", "recompra_alto_valor",
              "recompra_demais", "fator_recompra_mal_resolvida", "contatos_por_du_espera",
              "minutos_por_tratativa", "conc_min_lift", "conc_p_max"]:
        p[k] = float(p[k])
    for k in ["horizonte_espera_du", "dias_separacao", "rastreio_parado_vira_extravio_du",
              "conc_min_casos", "conc_min_excesso"]:
        p[k] = int(float(p[k]))
    if isinstance(p["feriados"], str):
        p["feriados"] = [x.strip() for x in p["feriados"].replace(";", ",").replace("\n", ",").split(",") if x.strip()]
    p["feriados"] = sorted({str(pd.Timestamp(x).date()) for x in p["feriados"]})
    faixas = []
    for f in p["faixas_cancelamento"]:
        mn, mx, pr = f
        faixas.append([int(mn), None if mx in (None, "", "null") else int(mx), float(pr)])
    p["faixas_cancelamento"] = sorted(faixas, key=lambda x: x[0])
    return p


# ---------------------------------------------------------------------------
# Datas e dias úteis
# ---------------------------------------------------------------------------
def _d(s) -> np.ndarray:
    """Converte série/lista de datas para numpy datetime64[D]."""
    return pd.to_datetime(pd.Series(s)).values.astype("datetime64[D]")


def _feriados(params: dict) -> np.ndarray:
    return np.array(params["feriados"], dtype="datetime64[D]")


def prazo_correto(data_pedido, sla_du, feriados, dias_separacao: int = 1) -> np.ndarray:
    """Data prometida pela regra do negócio.

    O dia 1 é o primeiro dia útil DEPOIS do pedido (dia de separação). A entrega
    prometida é o dia (dias_separacao + SLA) nessa contagem. Ex.: pedido na sexta,
    sábado ou domingo -> segunda é o dia 1.

    `busday_offset(d, n, roll='backward')`: se d não é dia útil, recua para o dia
    útil anterior e anda n dias úteis; para d útil, anda n dias úteis. Nos dois
    casos o 1º passo cai no primeiro dia útil estritamente depois de d.
    """
    dp = _d(data_pedido)
    hol = np.array(feriados, dtype="datetime64[D]")
    n = dias_separacao + np.asarray(sla_du, dtype=float)
    n = np.nan_to_num(n, nan=0).astype(int)
    return np.busday_offset(dp, n, roll="backward", holidays=hol)


def dias_uteis_atraso(prometida, fim, feriados) -> np.ndarray:
    """Dias úteis de atraso: dias úteis em [prometida, fim). 0 = no prazo; negativo = antes do prazo."""
    hol = np.array(feriados, dtype="datetime64[D]")
    return np.busday_count(_d(prometida), _d(fim), holidays=hol)


def prob_cancelamento(dias_atraso, faixas) -> np.ndarray:
    d = np.asarray(dias_atraso, dtype=float)
    out = np.zeros_like(d, dtype=float)
    for mn, mx, pr in faixas:
        hi = np.inf if mx is None else mx
        out = np.where((d >= mn) & (d <= hi), pr, out)
    return out


# ---------------------------------------------------------------------------
# Leitura e limpeza
# ---------------------------------------------------------------------------
def ler_csv(fonte) -> pd.DataFrame:
    """Lê CSV de caminho, bytes ou arquivo. Tenta UTF-8, depois latin-1; detecta ; ou ,."""
    if isinstance(fonte, (bytes, bytearray)):
        raw = bytes(fonte)
    elif hasattr(fonte, "read"):
        raw = fonte.read()
    else:
        with open(fonte, "rb") as fh:
            raw = fh.read()
    for enc in ("utf-8-sig", "latin-1"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    primeira = txt.splitlines()[0] if txt else ""
    sep = ";" if primeira.count(";") > primeira.count(",") else ","
    df = pd.read_csv(io.StringIO(txt), sep=sep, dtype=str, keep_default_na=False)
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def _norm_txt(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().map(lambda x: unicodedata.normalize("NFC", x))


def _registro(lista, verificacao, n, acao, detalhe="", severidade="info"):
    lista.append({"verificacao": verificacao, "linhas": int(n), "acao": acao,
                  "detalhe": detalhe, "severidade": severidade})


def limpar(pedidos: pd.DataFrame, clientes: pd.DataFrame, transp: pd.DataFrame,
           params: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict]]:
    q: list[dict] = []
    for nome, df, cols in [("pedidos_excecao", pedidos, COLS_PEDIDOS), ("clientes", clientes, COLS_CLIENTES),
                           ("transportadoras", transp, COLS_TRANSP)]:
        falt = [c for c in cols if c not in df.columns]
        if falt:
            raise ValueError(f"{nome}.csv sem as colunas obrigatórias: {', '.join(falt)}")
    p = pedidos[COLS_PEDIDOS].copy()
    c = clientes[COLS_CLIENTES].copy()
    t = transp[COLS_TRANSP].copy()
    n_bruto = len(p)
    _registro(q, "Linhas lidas em pedidos_excecao.csv", n_bruto, "—",
              f"{p['order_id'].nunique()} order_id distintos; {len(c)} clientes; {len(t)} linhas de transportadora")

    for df in (p, c, t):
        for col in df.columns:
            df[col] = _norm_txt(df[col])
    p["tipo_excecao"] = p["tipo_excecao"].str.lower()
    p["status"] = p["status"].str.lower()
    p["uf"] = p["uf"].str.upper()
    t["uf_atendida"] = t["uf_atendida"].str.upper()
    p["cd"] = p["cd"].str.upper()
    p["maquina"] = p["maquina"].str.upper()

    # Linhas idênticas
    n_ident = int(p.duplicated().sum())
    if n_ident:
        p = p.drop_duplicates()
    _registro(q, "Linhas 100% idênticas", n_ident, "Removidas" if n_ident else "Nenhuma encontrada")

    # Mesmo order_id repetido com algum campo diferente
    dup_mask = p["order_id"].duplicated(keep=False)
    n_dup_ids = int(p.loc[dup_mask, "order_id"].nunique())
    if n_dup_ids:
        g = p[dup_mask].groupby("order_id")
        campos = {}
        for _, grp in g:
            dif = tuple(col for col in p.columns if grp[col].nunique() > 1)
            campos[dif] = campos.get(dif, 0) + 1
        desc = "; ".join(f"{n} pedidos diferem em: {', '.join(k) or '(nada)'}" for k, n in campos.items())
        p["_ord_status"] = p["status"].map(ORDEM_STATUS).fillna(-1)
        p = (p.sort_values(["order_id", "_ord_status"], ascending=[True, False])
               .drop_duplicates("order_id", keep="first").drop(columns="_ord_status"))
        _registro(q, "order_id duplicado (mesma exceção registrada 2x)", int(dup_mask.sum()) - n_dup_ids,
                  "Mantida 1 linha por pedido, a de status mais avançado "
                  "(aguardando_* > em_analise > aberta)",
                  f"{n_dup_ids} pedidos com 2+ linhas. {desc}. Sem deduplicar, esses pedidos "
                  "seriam tratados duas vezes e o valor em risco seria contado em dobro.", "alta")
    else:
        _registro(q, "order_id duplicado", 0, "Nenhum encontrado")

    # Tipos numéricos e datas
    p["valor"] = pd.to_numeric(p["valor"].str.replace(",", ".", regex=False), errors="coerce")
    p["qtd_itens"] = pd.to_numeric(p["qtd_itens"], errors="coerce")
    ruins = p["valor"].isna() | (p["valor"] <= 0)
    _registro(q, "Valor ausente, não numérico ou <= 0", int(ruins.sum()),
              "Linhas descartadas" if ruins.any() else "Nenhum encontrado",
              severidade="alta" if ruins.any() else "info")
    p = p[~ruins]
    p["qtd_itens"] = p["qtd_itens"].fillna(1).clip(lower=1)
    p["brinde_no_pedido"] = p["brinde_no_pedido"].str.lower().isin(["true", "1", "sim", "s", "yes"])

    for col in ["data_pedido", "data_prometida", "data_entrega", "data_abertura_excecao"]:
        bruto = p[col].replace("", None)
        conv = pd.to_datetime(bruto, errors="coerce", format="mixed", dayfirst=False)
        n_bad = int((bruto.notna() & conv.isna()).sum())
        if n_bad:
            _registro(q, f"Data inválida em {col}", n_bad, "Convertida para vazio", severidade="media")
        p[col] = conv
    sem_data = p["data_pedido"].isna()
    if sem_data.any():
        _registro(q, "Pedido sem data_pedido válida", int(sem_data.sum()), "Linhas descartadas", severidade="alta")
        p = p[~sem_data]
    p["data_abertura_excecao"] = p["data_abertura_excecao"].fillna(p["data_pedido"])

    inc = p["data_entrega"].notna() & (p["data_entrega"] < p["data_pedido"])
    _registro(q, "Entrega antes do pedido", int(inc.sum()),
              "data_entrega anulada" if inc.any() else "Nenhuma encontrada")
    p.loc[inc, "data_entrega"] = pd.NaT
    inc2 = p["data_abertura_excecao"] < p["data_pedido"]
    _registro(q, "Exceção aberta antes do pedido", int(inc2.sum()), "Registrado" if inc2.any() else "Nenhuma encontrada")

    tipo_ruim = ~p["tipo_excecao"].isin(TIPOS_VALIDOS)
    _registro(q, "tipo_excecao fora da lista do dicionário", int(tipo_ruim.sum()),
              "Mantido, tratado como genérico" if tipo_ruim.any() else "Nenhum encontrado",
              ", ".join(sorted(p.loc[tipo_ruim, "tipo_excecao"].unique()))[:200])

    # Coerência tipo x entrega (avaria/vazamento/faltante só existem com produto entregue)
    precisa_entrega = p["tipo_excecao"].isin(["avaria_transporte", "vazamento_produto", "item_faltante"])
    sem_ent = precisa_entrega & p["data_entrega"].isna()
    _registro(q, "Avaria/vazamento/item faltante sem data de entrega", int(sem_ent.sum()),
              "Registrado" if sem_ent.any() else "Consistente: todos têm data_entrega")
    nao_entregavel = p["tipo_excecao"].isin(["extravio", "rastreio_parado", "devolvido_transportadora"]) & p["data_entrega"].notna()
    _registro(q, "Extravio/rastreio parado/devolvido COM data de entrega", int(nao_entregavel.sum()),
              "Registrado" if nao_entregavel.any() else "Consistente")

    # Máquina: só Gocase
    gc = p["marca"].str.lower().eq("gocase") | p["order_id"].str.upper().str.startswith("GC")
    maq_fora = (~gc) & p["maquina"].ne("")
    gc_sem = gc & p["maquina"].eq("")
    _registro(q, "Máquina preenchida em pedido não Gocase / Gocase sem máquina",
              int(maq_fora.sum() + gc_sem.sum()), "Registrado" if (maq_fora.any() or gc_sem.any()) else "Consistente",
              f"{int(maq_fora.sum())} não-Gocase com máquina; {int(gc_sem.sum())} Gocase sem máquina")

    # Clientes
    for col in ["n_pedidos", "receita_acumulada", "n_contatos_atendimento"]:
        c[col] = pd.to_numeric(c[col].str.replace(",", ".", regex=False), errors="coerce")
    c["data_primeiro_pedido"] = pd.to_datetime(c["data_primeiro_pedido"].replace("", None), errors="coerce")
    n_cdup = int(c["customer_id"].duplicated().sum())
    if n_cdup:
        c = c.sort_values("receita_acumulada", ascending=False).drop_duplicates("customer_id")
    _registro(q, "customer_id duplicado em clientes.csv", n_cdup, "Mantida a linha de maior receita" if n_cdup else "Nenhum")
    sem_cli = ~p["customer_id"].isin(c["customer_id"])
    _registro(q, "Pedido com cliente ausente em clientes.csv", int(sem_cli.sum()),
              "Cliente tratado como valor padrão (recompra 'demais', ticket = valor do pedido)" if sem_cli.any() else "Todos encontrados",
              severidade="media" if sem_cli.any() else "info")
    c["ticket_medio"] = (c["receita_acumulada"] / c["n_pedidos"].replace(0, np.nan))
    meses = ((pd.Timestamp(params["_ref"]) - c["data_primeiro_pedido"]).dt.days / 30.4).clip(lower=1)
    c["pedidos_mes"] = c["n_pedidos"] / meses
    sus = c[(c["pedidos_mes"] > 8) & (c["n_pedidos"] >= 20)]
    _registro(q, "Clientes com frequência de compra atípica (>8 pedidos/mês, 20+ pedidos)", len(sus),
              "Mantidos; sinalizados (possível revenda ou conta compartilhada)" if len(sus) else "Nenhum",
              ", ".join(sus["customer_id"].head(6)))

    # Transportadoras
    t["sla_contratado_dias_uteis"] = pd.to_numeric(t["sla_contratado_dias_uteis"], errors="coerce")
    t["custo_medio_frete"] = pd.to_numeric(t["custo_medio_frete"].str.replace(",", ".", regex=False), errors="coerce")
    n_tdup = int(t.duplicated(["transportadora", "uf_atendida"]).sum())
    t = t.drop_duplicates(["transportadora", "uf_atendida"])
    _registro(q, "Transportadora x UF duplicada", n_tdup, "Mantida a 1ª" if n_tdup else "Nenhuma")

    p = p.merge(t, left_on=["transportadora", "uf"], right_on=["transportadora", "uf_atendida"], how="left")
    sem_sla = p["sla_contratado_dias_uteis"].isna()
    if sem_sla.any():
        med = t.groupby("transportadora")[["sla_contratado_dias_uteis", "custo_medio_frete"]].median()
        for col in ["sla_contratado_dias_uteis", "custo_medio_frete"]:
            p.loc[sem_sla, col] = p.loc[sem_sla, "transportadora"].map(med[col])
            p[col] = p[col].fillna(t[col].median())
    _registro(q, "Pedido sem SLA contratado para transportadora x UF", int(sem_sla.sum()),
              "Usada a mediana da transportadora" if sem_sla.any() else "Todos com SLA",
              severidade="media" if sem_sla.any() else "info")
    p = p.drop(columns=["uf_atendida"])
    p = p.merge(c[["customer_id", "n_pedidos", "receita_acumulada", "ticket_medio", "n_contatos_atendimento"]],
                on="customer_id", how="left")
    p["ticket_medio"] = p["ticket_medio"].fillna(p["valor"])
    p["receita_acumulada"] = p["receita_acumulada"].fillna(p["valor"])
    p["n_contatos_atendimento"] = p["n_contatos_atendimento"].fillna(0)
    return p.reset_index(drop=True), c, t, q


# ---------------------------------------------------------------------------
# Enriquecimento: prazo, risco, custo de esperar, ação recomendada
# ---------------------------------------------------------------------------
def enriquecer(p: pd.DataFrame, params: dict, q: list[dict] | None = None) -> pd.DataFrame:
    p = p.copy()
    hol = params["feriados"]
    ref = np.datetime64(params["_ref"], "D")
    faixas = params["faixas_cancelamento"]
    H = params["horizonte_espera_du"]

    p["prometida_correta"] = pd.to_datetime(prazo_correto(p["data_pedido"], p["sla_contratado_dias_uteis"], hol,
                                                          params["dias_separacao"]))
    p["dif_prazo_du"] = np.busday_count(_d(p["data_prometida"].fillna(p["prometida_correta"])),
                                        _d(p["prometida_correta"]), holidays=np.array(hol, dtype="datetime64[D]"))
    entregue = p["data_entrega"].notna()
    p["entregue"] = entregue
    tipo = p["tipo_excecao"]
    # Desempenho da entrega (transportadora): data de entrega (ou hoje, se não entregue) contra o prazo.
    fim_ent = np.where(entregue, _d(p["data_entrega"].fillna(pd.Timestamp(params["_ref"]))), ref)
    p["atraso_entrega_du"] = dias_uteis_atraso(p["prometida_correta"], fim_ent, hol)
    p["atraso_sistema_du"] = dias_uteis_atraso(p["data_prometida"].fillna(p["prometida_correta"]), fim_ent, hol)
    # Espera do cliente: só o atraso de pedido JÁ entregue está encerrado. Avaria, vazamento, item faltante
    # etc. foram entregues, mas o cliente segue sem o produto certo: a espera conta até a data de referência.
    fim_cli = np.where(tipo.eq("atraso_entrega") & entregue, fim_ent, ref)
    p["atraso_du"] = dias_uteis_atraso(p["prometida_correta"], fim_cli, hol)

    # "Atraso falso": exceção de atraso que, pelo prazo correto, não está atrasada.
    p["atraso_falso"] = tipo.eq("atraso_entrega") & (p["atraso_du"] <= 0)
    # O risco cresce com o tempo se o cliente ainda não tem o produto certo.
    atraso_entregue = tipo.eq("atraso_entrega") & entregue
    em_transito_no_prazo = tipo.eq("atraso_entrega") & ~entregue & (p["atraso_du"] <= 0)
    p["risco_cresce"] = ~(atraso_entregue | em_transito_no_prazo)

    # Valor do cliente: perda de recompra se a exceção for mal resolvida (margem bruta do ticket médio).
    p_recompra = np.where(p["receita_acumulada"] > params["limiar_cliente_alto_valor"],
                          params["recompra_alto_valor"], params["recompra_demais"])
    p["cliente_alto_valor"] = p["receita_acumulada"] > params["limiar_cliente_alto_valor"]
    p["ltv_em_risco"] = (params["fator_recompra_mal_resolvida"] * p_recompra * p["ticket_medio"]
                         * (1 - params["cmv_pct"]))
    # Base de perda num cancelamento: reembolso do valor pago + recompra perdida.
    # Pagamento pendente: não há reembolso; perde-se a venda (receita - CMV).
    # Item faltante: o risco é o reembolso do item que faltou (valor / qtd_itens), não do pedido inteiro.
    base_reemb = np.select([tipo.eq("pagamento_pendente"), tipo.eq("item_faltante")],
                           [p["valor"] * (1 - params["cmv_pct"]), p["valor"] / p["qtd_itens"]], default=p["valor"])
    p["base_perda"] = base_reemb + p["ltv_em_risco"]

    d0 = p["atraso_du"].to_numpy()
    dH = np.where(p["risco_cresce"], d0 + H, d0)
    p["p_cancel_hoje"] = prob_cancelamento(d0, faixas)
    p["p_cancel_espera"] = prob_cancelamento(dH, faixas)
    p["perda_esperada_hoje"] = p["p_cancel_hoje"] * p["base_perda"]
    contatos = np.where(p["risco_cresce"], params["contatos_por_du_espera"] * H * params["custo_contato"], 0.0)
    p["perda_esperada_espera"] = p["p_cancel_espera"] * p["base_perda"] + contatos
    # Custo de esperar = quanto a perda esperada cresce se o pedido ficar mais H dias úteis sem tratativa.
    p["custo_de_esperar"] = (p["perda_esperada_espera"] - p["perda_esperada_hoje"]).clip(lower=0)

    # Custo direto de resolver (incorrido de qualquer forma quando a exceção é resolvida).
    cmv = params["cmv_pct"] * p["valor"]
    frete = p["custo_medio_frete"]
    reenvio = cmv + frete
    item = params["cmv_pct"] * p["valor"] / p["qtd_itens"] + frete
    rastreio_ext = tipo.eq("rastreio_parado") & (p["atraso_du"] >= params["rastreio_parado_vira_extravio_du"])
    p["custo_direto"] = np.select(
        [tipo.isin(TIPOS_REENVIO) | rastreio_ext, tipo.eq("item_faltante"), tipo.eq("devolvido_transportadora")],
        [reenvio, item, frete], default=0.0)
    p["custo_reembolso"] = p["valor"]
    p["custo_tratativa_contato"] = params["custo_contato"]
    p["rastreio_vira_extravio"] = rastreio_ext
    p["dias_em_aberto_du"] = np.busday_count(_d(p["data_abertura_excecao"]), ref,
                                             holidays=np.array(hol, dtype="datetime64[D]"))
    p["regiao"] = p["uf"].map(REGIAO_UF).fillna("Outra")
    p["semana"] = (p["data_pedido"] - pd.to_timedelta(p["data_pedido"].dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
    return p


def _acao(r: pd.Series, params: dict) -> str:
    t = r["tipo_excecao"]
    reenv = f"R$ {r['custo_direto']:.0f}"
    reemb = f"R$ {r['valor']:.0f}"
    status = r["status"]
    pre = ""
    if status == "aguardando_cliente":
        pre = "[Aguardando cliente: fazer 2º contato.] "
    elif status == "aguardando_transportadora":
        pre = "[Aguardando transportadora: escalar, prazo 24 h.] "
    if t == "atraso_entrega":
        if r["entregue"]:
            return "Pedido já entregue: encerrar a exceção com mensagem de desculpas (sem custo adicional)."
        if r["atraso_du"] <= 0:
            return (f"Não está atrasado pelo prazo correto ({r['prometida_correta']:%d/%m}). Reclassificar e "
                    "informar ao cliente a data certa; só monitorar.")
        return (pre + f"acionar {r['transportadora']} pedindo posição/prioridade e avisar o cliente "
                "proativamente com nova data. Se passar de 10 DU, oferecer reenvio.")
    if t == "rastreio_parado":
        if r["rastreio_vira_extravio"]:
            return (pre + f"tratar como extravio: abrir acareação com {r['transportadora']} e reenviar já "
                    f"(reenvio {reenv} < reembolso {reemb}).")
        return pre + f"cobrar rastreio de {r['transportadora']}; se não mover em 2 DU, abrir acareação."
    if t == "extravio":
        return pre + f"reenviar imediatamente (custo {reenv}, vs reembolso {reemb}) e abrir ressarcimento com a transportadora."
    if t == "devolvido_transportadora":
        return pre + "confirmar endereço/disponibilidade com o cliente e redespachar (custo ~ frete)."
    if t in ("avaria_transporte", "vazamento_produto"):
        return (pre + f"reenviar sem exigir devolução (custo {reenv} < reembolso {reemb}); pedir foto para "
                "ressarcimento/qualidade.")
    if t == "item_faltante":
        return pre + f"enviar só o item/brinde faltante (custo ~R$ {r['custo_direto']:.0f}); registrar CD/turno."
    if t == "falha_personalizacao":
        alvo = "em outra impressora" if r.get("maquina") else ""
        return pre + f"reimprimir {alvo} com conferência visual antes de separar (custo {reenv}); avisar o cliente."
    if t == "pagamento_pendente":
        return pre + "enviar novo link de pagamento (Pix) e lembrete; cancelar automaticamente se não pagar em 2 DU."
    if t == "ruptura_pos_venda":
        return pre + "oferecer troca por similar ou cupom; reembolsar só se o cliente recusar."
    if t == "endereco_divergente":
        return pre + "confirmar endereço por WhatsApp antes de despachar; corrigir na transportadora."
    return pre + "analisar caso."


def _acao_fmt(r: pd.Series, params: dict) -> str:
    txt = _acao(r, params)
    if txt.startswith("["):
        i = txt.index("] ") + 2
        return txt[:i] + txt[i:i + 1].upper() + txt[i + 1:]
    return txt[:1].upper() + txt[1:]


def _motivo(r: pd.Series) -> str:
    partes = []
    if r["atraso_du"] > 0:
        partes.append(f"{int(r['atraso_du'])} DU de atraso (risco de cancelar {r['p_cancel_hoje']:.0%})")
    elif r["risco_cresce"]:
        partes.append(f"prazo vence em {int(-r['atraso_du'])} DU" if r["atraso_du"] < 0 else "prazo vence hoje")
    if r["risco_cresce"] and r["p_cancel_espera"] > r["p_cancel_hoje"]:
        partes.append(f"sobe para {r['p_cancel_espera']:.0%} se esperar")
    if r["cliente_alto_valor"]:
        partes.append(f"cliente alto valor (R$ {r['receita_acumulada']:,.0f} acumulados)".replace(",", "."))
    if r["valor"] >= 250:
        partes.append(f"pedido de R$ {r['valor']:.0f}")
    if r.get("causa_id"):
        partes.append(f"ligado a causa sistêmica: {r['causa_rotulo']}")
    return "; ".join(partes) or "baixo risco"


# ---------------------------------------------------------------------------
# Priorização e simulação
# ---------------------------------------------------------------------------
def ordenar_prioridade(p: pd.DataFrame) -> pd.DataFrame:
    """Ordena pelo custo de esperar (R$), desempate por perda esperada atual e valor."""
    return p.sort_values(["custo_de_esperar", "perda_esperada_espera", "valor"], ascending=False)


def fila_hoje(p: pd.DataFrame, params: dict) -> pd.DataFrame:
    cap = params["capacidade_diaria"]
    f = ordenar_prioridade(p).head(cap).copy()
    f["posicao"] = np.arange(1, len(f) + 1)
    f["acao_recomendada"] = f.apply(lambda r: _acao_fmt(r, params), axis=1)
    f["motivo"] = f.apply(_motivo, axis=1)
    return f


def _perda_ao_tratar(p: pd.DataFrame, t: np.ndarray | int, params: dict) -> np.ndarray:
    """Perda esperada se o pedido for tratado no dia útil t (0 = hoje)."""
    d = p["atraso_du"].to_numpy() + np.where(p["risco_cresce"], t, 0)
    contatos = np.where(p["risco_cresce"], params["contatos_por_du_espera"] * t * params["custo_contato"], 0.0)
    return prob_cancelamento(d, params["faixas_cancelamento"]) * p["base_perda"].to_numpy() + contatos


def simular(p: pd.DataFrame, params: dict, ordem: str = "prioridade") -> dict:
    """Esvazia o backlog com a capacidade diária. Supõe que a tratativa congela o risco no dia em que acontece
    (o cliente é contatado e a solução é encaminhada). Retorna perda esperada total e dia de tratativa de cada pedido."""
    cap = max(1, params["capacidade_diaria"])
    n = len(p)
    dia = np.full(n, -1)
    if ordem == "fifo":
        idx = np.lexsort((p["order_id"].to_numpy(), p["data_abertura_excecao"].to_numpy()))
        dia[idx] = np.arange(n) // cap
    else:
        restantes = np.arange(n)
        t = 0
        H = params["horizonte_espera_du"]
        while len(restantes):
            sub = p.iloc[restantes]
            ganho = _perda_ao_tratar(sub, t + H, params) - _perda_ao_tratar(sub, t, params)
            nivel = _perda_ao_tratar(sub, t, params)
            ordem_sub = np.lexsort((-nivel, -ganho))
            esc = restantes[ordem_sub[:cap]]
            dia[esc] = t
            restantes = restantes[ordem_sub[cap:]]
            t += 1
    perdas = np.zeros(n)
    for t in np.unique(dia):
        m = dia == t
        perdas[m] = _perda_ao_tratar(p[m], int(t), params)
    return {"perda_total": float(perdas.sum()), "dias_para_zerar": int(dia.max() + 1) if n else 0,
            "dia": dia, "perdas": perdas}


# ---------------------------------------------------------------------------
# Concentrações e causas sistêmicas
# ---------------------------------------------------------------------------
DIMENSOES = {
    "transportadora x região": ["transportadora", "regiao"],
    "transportadora": ["transportadora"],
    "UF": ["uf"],
    "CD x turno": ["cd", "turno"],
    "CD x turno x brinde": ["cd", "turno", "brinde_txt"],
    "brinde": ["brinde_txt"],
    "máquina": ["maquina"],
    "lote x SKU": ["lote_produto", "sku"],
    "lote": ["lote_produto"],
    "SKU": ["sku"],
    "marca": ["marca"],
}
# Quais dimensões podem, de forma plausível, CAUSAR cada tipo de exceção. Evita falsos achados por
# coincidência (ex.: "pagamento pendente concentrado numa transportadora" não tem mecanismo causal).
_TRANSP = ["transportadora x região", "transportadora", "UF"]
PLAUSIVEL = {
    "atraso_entrega": _TRANSP, "rastreio_parado": _TRANSP, "extravio": _TRANSP,
    "devolvido_transportadora": _TRANSP,
    "avaria_transporte": _TRANSP + ["marca", "SKU", "lote x SKU", "lote"],
    "vazamento_produto": ["lote x SKU", "lote", "SKU", "marca", "transportadora"],
    "item_faltante": ["CD x turno", "CD x turno x brinde", "brinde"],
    "falha_personalizacao": ["máquina", "CD x turno", "SKU"],
    "ruptura_pos_venda": ["marca", "SKU"],
    "endereco_divergente": ["UF"],
    "pagamento_pendente": ["marca"],
}


def _elegivel(df: pd.DataFrame, tipo: str) -> pd.Series:
    """População em que o tipo de exceção pode ocorrer (ex.: falha de personalização só em pedido Gocase)."""
    if tipo == "falha_personalizacao":
        return df["maquina"].astype(str).ne("")
    return pd.Series(True, index=df.index)


def _poisson_cauda(o: int, lam: float) -> float:
    """P(X >= o) para X ~ Poisson(lam)."""
    if lam <= 0:
        return 0.0 if o > 0 else 1.0
    termo = math.exp(-lam)
    acum = termo
    for k in range(1, o):
        termo *= lam / k
        acum += termo
    return max(0.0, 1.0 - acum)


def _segmentos(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    d = df.assign(brinde_txt=np.where(df["brinde_no_pedido"], "com brinde", "sem brinde")) if "brinde_txt" in cols else df
    return d[cols].astype(str).agg(" | ".join, axis=1)


def detectar_concentracoes(p: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Para cada tipo de exceção e cada dimensão plausível: casos observados vs esperados por segmento.

    Não temos o volume total de pedidos (só os que tiveram exceção), então o 'esperado' usa as
    OUTRAS exceções do segmento como proxy de volume: esperado = (exceções do segmento que não
    são do tipo T) x (razão T / não-T no resto da base). Excesso = observado - esperado.
    Filtros: mínimo de casos, lift, excesso e significância (cauda de Poisson).
    """
    achados = []
    for tipo in p["tipo_excecao"].unique():
        el = p[_elegivel(p, tipo)]
        for nome in PLAUSIVEL.get(tipo, []):
            cols = DIMENSOES[nome]
            base = el
            if "maquina" in cols:
                base = base[base["maquina"].astype(str).ne("")]
            if base.empty:
                continue
            seg = _segmentos(base, cols)
            e_t = base["tipo_excecao"].eq(tipo)
            obs = e_t.groupby(seg).sum()
            tot = seg.value_counts().reindex(obs.index)
            nao_t = tot - obs
            T, N = int(e_t.sum()), len(base)
            resto_t = T - obs
            resto_nao = (N - T) - nao_t
            taxa = resto_t / resto_nao.replace(0, np.nan)
            esp = nao_t * taxa
            for s in obs.index:
                o, e = int(obs[s]), esp[s]
                if pd.isna(e):
                    continue
                e = float(e)
                exc = o - e
                lift = o / e if e > 0 else 99.0
                if (o >= params["conc_min_casos"] and lift >= params["conc_min_lift"]
                        and exc >= params["conc_min_excesso"] and _poisson_cauda(o, e) <= params["conc_p_max"]):
                    achados.append({"dimensao": nome, "colunas": cols, "segmento": s, "tipo_excecao": tipo,
                                    "observado": o, "esperado": round(e, 1), "excesso": round(exc, 1),
                                    "lift": round(min(lift, 99.0), 1), "n_segmento": int(tot[s]),
                                    "precisao": o / tot[s]})
    if not achados:
        return pd.DataFrame(columns=["dimensao", "colunas", "segmento", "tipo_excecao", "observado", "esperado",
                                     "excesso", "lift", "n_segmento", "precisao"])
    return pd.DataFrame(achados).sort_values("excesso", ascending=False).reset_index(drop=True)


def _mask_achado(p: pd.DataFrame, cols: list[str], segmento: str, tipo: str) -> np.ndarray:
    m = _segmentos(p, cols).eq(segmento) & p["tipo_excecao"].eq(tipo) & _elegivel(p, tipo)
    return m.to_numpy()


def _selecionar_achados(p: pd.DataFrame, a: pd.DataFrame) -> list[dict]:
    """Remove achados redundantes: para o mesmo tipo, segmentos que se sobrepõem em >= 60% dos casos formam
    um grupo; fica o mais específico (maior precisão) entre os que explicam >= 85% do excesso do maior."""
    if a.empty:
        return []
    mascaras = {i: _mask_achado(p, r["colunas"], r["segmento"], r["tipo_excecao"]) for i, r in a.iterrows()}
    restantes = list(a.index)
    escolhidos = []
    while restantes:
        i = restantes[0]
        r = a.loc[i]
        grupo = [j for j in restantes if a.loc[j, "tipo_excecao"] == r["tipo_excecao"]
                 and (mascaras[i] & mascaras[j]).sum() >= 0.6 * min(mascaras[i].sum(), mascaras[j].sum())]
        cand = [j for j in grupo if a.loc[j, "excesso"] >= 0.85 * r["excesso"]]
        melhor = max(cand, key=lambda j: (a.loc[j, "precisao"], a.loc[j, "lift"]))
        escolhidos.append({**a.loc[melhor].to_dict(), "_mask": mascaras[melhor]})
        restantes = [j for j in restantes if j not in grupo]
    return escolhidos


DONOS = {
    "transp_atraso": ("Logística: gestão de transportadoras", "transporte"),
    "transp_avaria": ("Logística + Embalagem", "transporte"),
    "fragil": ("Embalagem / Engenharia de produto da marca", "produto"),
    "maquina": ("Produção / Manutenção das impressoras", "produção"),
    "lote": ("Qualidade + Compras (fornecedor)", "produto"),
    "cd": ("Operação do CD (liderança do turno)", "separação"),
    "ruptura": ("Planejamento de estoque + E-commerce", "estoque"),
    "geral": ("Operações", "outros"),
}


def _sem_label(s: str) -> str:
    return f"semana de {pd.Timestamp(s):%d/%m}"


def _texto_causa(tipos: list[str], dim: str, seg: str, p_seg: pd.DataFrame, transp: pd.DataFrame) -> dict:
    ev, alt_info = [], None
    tipos_set = set(tipos)
    if "transportadora" in dim and tipos_set & {"atraso_entrega", "rastreio_parado", "extravio", "devolvido_transportadora"}:
        transp_nome = seg.split(" | ")[0]
        ufs = p_seg["uf"].value_counts().head(5)
        ev.append("UFs: " + ", ".join(f"{u} ({n})" for u, n in ufs.items()))
        ent = p_seg[p_seg["entregue"]]
        if len(ent):
            ev.append(f"os já entregues chegaram em média {ent['atraso_entrega_du'].mean():.1f} DU depois do prazo correto")
        tt = transp[transp["uf_atendida"].isin(list(ufs.index))]
        atual = tt[tt["transportadora"] == transp_nome][["sla_contratado_dias_uteis", "custo_medio_frete"]].mean()
        alt = tt[tt["transportadora"] != transp_nome].groupby("transportadora")[["sla_contratado_dias_uteis", "custo_medio_frete"]].mean()
        alt = alt[alt["sla_contratado_dias_uteis"] <= atual["sla_contratado_dias_uteis"]].sort_values("custo_medio_frete")
        alt_txt = ""
        if len(alt):
            nome_alt = alt.index[0]
            dif = float(alt.iloc[0]["custo_medio_frete"] - atual["custo_medio_frete"])
            alt_txt = f" Alternativa com SLA igual ou menor nessas UFs: {nome_alt} (+R$ {dif:.2f} de frete por pedido)."
            alt_info = {"transportadora": nome_alt, "dif_frete": round(dif, 2)}
        regiao = seg.split(" | ")[-1] if " | " in seg else "todas as regiões"
        acao = (f"Cobrar de {transp_nome} um plano de recuperação de SLA ({regiao}), com multa contratual; "
                f"se não normalizar em 2 semanas, migrar o volume.{alt_txt}")
        dono = DONOS["transp_atraso"]
    elif "transportadora" in dim and "avaria_transporte" in tipos_set:
        marcas = p_seg["marca"].value_counts().head(3)
        prods = p_seg["descricao_produto"].value_counts().head(3)
        ev.append("marcas: " + ", ".join(f"{m} ({n})" for m, n in marcas.items()))
        ev.append("produtos: " + ", ".join(f"{m} ({n})" for m, n in prods.items()))
        acao = (f"Pedir ressarcimento das avarias a {seg.split(' | ')[0]} e auditar o manuseio; até lá, "
                "não despachar itens frágeis (vidro/frascos) por ela.")
        dono = DONOS["transp_avaria"]
    elif dim == "máquina":
        acao = (f"Parar a {seg} para manutenção/calibração, redistribuir a fila nas outras impressoras e "
                "incluir conferência visual pós-impressão.")
        dono = DONOS["maquina"]
    elif dim in ("lote x SKU", "lote", "SKU") and tipos_set & {"vazamento_produto", "avaria_transporte"}:
        prods = p_seg["descricao_produto"].value_counts().head(2)
        ev.append("produtos: " + ", ".join(f"{m} ({n})" for m, n in prods.items()))
        acao = (f"Bloquear o estoque de {seg} (segregar e inspecionar), cobrar o fornecedor e trocar tampa/lacre; "
                "até lá, despachar em saco selado.")
        dono = DONOS["lote"]
    elif dim == "marca" and tipos_set & {"avaria_transporte", "vazamento_produto"}:
        prods = p_seg["descricao_produto"].value_counts().head(3)
        tr = p_seg["transportadora"].value_counts().head(3)
        ev.append("produtos: " + ", ".join(f"{m} ({n})" for m, n in prods.items()))
        ev.append("transportadoras: " + ", ".join(f"{m} ({n})" for m, n in tr.items()))
        acao = f"Revisar a embalagem dos produtos {seg} (frascos de vidro): calço/colmeia e caixa reforçada."
        dono = DONOS["fragil"]
    elif dim.startswith("CD") or dim == "brinde":
        ev.append(f"{p_seg['brinde_no_pedido'].mean():.0%} dos casos tinham brinde de campanha")
        acao = (f"Revisar a separação em {seg}: checklist de brinde, conferência por peso na expedição e "
                "treinamento da equipe do turno.")
        dono = DONOS["cd"]
    elif "ruptura_pos_venda" in tipos_set:
        prods = p_seg["descricao_produto"].value_counts().head(3)
        ev.append("SKUs: " + ", ".join(f"{m} ({n})" for m, n in prods.items()))
        acao = (f"Sincronizar o estoque de {seg} com a loja (bloquear venda sem saldo) e rever o estoque de "
                "segurança dos SKUs com mais ruptura.")
        dono = DONOS["ruptura"]
    else:
        acao = f"Investigar a concentração em {dim} = {seg}."
        dono = DONOS["geral"]
    sem = p_seg.groupby("semana").size().sort_index()
    if len(sem) >= 4:
        for k in range(3, len(sem)):
            base = sem.iloc[:k].median()
            if base > 0 and sem.iloc[k] >= 2 * base and sem.iloc[k:].median() >= 1.5 * base:
                ev.append(f"piora a partir da {_sem_label(sem.index[k])} ({base:.0f}/sem antes, "
                          f"{sem.iloc[k:].median():.0f}/sem depois)")
                break
    return {"acao": acao, "dono": dono[0], "area": dono[1], "evidencias": ev, "alternativa": alt_info}


def causas_sistemicas(p: pd.DataFrame, transp: pd.DataFrame, params: dict, janela_dias: float,
                      custo_unit: pd.Series) -> tuple[list[dict], pd.Series, pd.Series]:
    a = detectar_concentracoes(p, params)
    escolhidos = _selecionar_achados(p, a)
    # 1) juntar achados do mesmo segmento (ex.: atraso + rastreio parado na mesma transportadora x região)
    grupos: dict[tuple, dict] = {}
    for ach in escolhidos:
        k = (ach["dimensao"], ach["segmento"])
        if k in grupos:
            g = grupos[k]
            g["tipos"].append(ach["tipo_excecao"])
            g["_mask"] = g["_mask"] | ach["_mask"]
            for f in ["observado", "esperado", "excesso"]:
                g[f] += ach[f]
        else:
            grupos[k] = {**ach, "tipos": [ach["tipo_excecao"]]}
    # 2) atribuir cada pedido a no máximo uma causa (a de maior excesso), sem contar em dobro
    ordem = sorted(grupos.values(), key=lambda g: -g["excesso"])
    usado = np.zeros(len(p), dtype=bool)
    fator_mes = 30.0 / max(janela_dias, 1)
    causas = []
    causa_id = np.array([""] * len(p), dtype=object)
    causa_rot = np.array([""] * len(p), dtype=object)
    cu = custo_unit.to_numpy()
    for g in ordem:
        m = g["_mask"] & ~usado
        obs = int(m.sum())
        if obs == 0:
            continue
        esp = g["esperado"] * obs / g["observado"]
        exc = obs - esp
        if obs < params["conc_min_casos"] or exc < params["conc_min_excesso"]:
            continue
        usado |= m
        seg_df = p[m]
        txt = _texto_causa(g["tipos"], g["dimensao"], g["segmento"], seg_df, transp)
        frac = exc / obs
        custo_seg = float(cu[m].sum())
        rot = f"{' + '.join(ROTULO_TIPO.get(t, t) for t in g['tipos'])} · {g['segmento']}"
        cid = f"C{len(causas) + 1}"
        causa_id[m] = cid
        causa_rot[m] = rot
        causas.append({
            "id": cid, "rotulo": rot, "tipos": g["tipos"], "dimensao": g["dimensao"], "segmento": g["segmento"],
            "observado": obs, "esperado": round(esp, 1), "excesso": round(exc, 1),
            "lift": round(obs / esp, 1) if esp > 0 else 99.0,
            "custo_casos_atuais": round(custo_seg, 2),
            "custo_excesso_janela": round(custo_seg * frac, 2),
            "ganho_mensal_estimado": round(custo_seg * frac * fator_mes, 2),
            "tratativas_mes_evitadas": round(exc * fator_mes, 0),
            "valor_pedidos": round(float(seg_df["valor"].sum()), 2),
            "semanal": {k: int(v) for k, v in seg_df.groupby("semana").size().sort_index().items()},
            **txt,
        })
    causas.sort(key=lambda c: -c["ganho_mensal_estimado"])
    remap = {c["id"]: f"C{i + 1}" for i, c in enumerate(causas)}
    for c in causas:
        c["id"] = remap[c["id"]]
    causa_id = pd.Series(causa_id, index=p.index).map(lambda x: remap.get(x, ""))
    return causas, causa_id, pd.Series(causa_rot, index=p.index)


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------
def _r(x, n=2):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), n)


def analisar(pedidos: pd.DataFrame, clientes: pd.DataFrame, transp: pd.DataFrame,
             params: dict | None = None) -> dict:
    params = mesclar_params(params)
    # data de referência
    ab = pd.to_datetime(pedidos["data_abertura_excecao"].replace("", None), errors="coerce")
    if str(params["data_referencia"]).strip().lower() in ("", "auto", "none"):
        params["_ref"] = str((ab.max() if ab.notna().any() else pd.Timestamp.today()).date())
    else:
        params["_ref"] = str(pd.Timestamp(params["data_referencia"]).date())

    p, c, t, q = limpar(pedidos, clientes, transp, params)
    if p.empty:
        raise ValueError("Nenhum pedido válido após a limpeza.")
    p = enriquecer(p, params, q)

    # Checagem da regra de prazo (sistema x regra do parametros.md)
    hol = params["feriados"]
    dif = p["dif_prazo_du"]
    cal = (p["data_prometida"] - p["data_pedido"]).dt.days - (params["dias_separacao"] + p["sla_contratado_dias_uteis"])
    pct_cal = float((cal == 0).mean())
    n_errado = int((p["data_prometida"].notna() & (p["data_prometida"] != p["prometida_correta"])).sum())
    n_antes = int((p["data_prometida"] < p["prometida_correta"]).sum())
    atrasos = p[p["tipo_excecao"].eq("atraso_entrega")]
    n_falso = int(p["atraso_falso"].sum())
    _registro(q, "data_prometida do sistema diverge da regra (1 DU separação + SLA em dias úteis, sem feriados)",
              n_errado,
              "Prazo recalculado pela regra e usado em todos os cálculos; data do sistema mantida só para comparação",
              f"Em {pct_cal:.0%} dos pedidos a data do sistema = data do pedido + (1 + SLA) dias CORRIDOS. "
              f"Em {n_antes} pedidos a data do sistema é ANTERIOR à correta (em média {dif.mean():.1f} DU antes). Efeito: {n_falso} das "
              f"{len(atrasos)} exceções de 'atraso_entrega' não estão atrasadas pelo prazo correto.",
              "alta" if n_errado else "info")

    janela = max(1.0, float((p["data_pedido"].max() - p["data_pedido"].min()).days + 1))
    sim_fifo = simular(p, params, "fifo")
    sim_prio = simular(p, params, "prioridade")
    p["dia_tratativa_prioridade"] = sim_prio["dia"]
    p["dia_tratativa_fifo"] = sim_fifo["dia"]
    p["perda_esperada_priorizada"] = sim_prio["perdas"]
    # custo de uma exceção = custo direto de resolver + perda esperada (com a fila priorizada) + 1 contato
    custo_unit = p["custo_direto"] + p["perda_esperada_priorizada"] + params["custo_contato"]
    causas, cid, crot = causas_sistemicas(p, t, params, janela, custo_unit)
    p["causa_id"] = cid.values
    p["causa_rotulo"] = crot.values
    fila = fila_hoje(p, params)
    fifo_hoje = p.sort_values(["data_abertura_excecao", "order_id"]).head(params["capacidade_diaria"])

    fator_mes = 30.0 / janela
    entrada_du = len(p) / max(1, np.busday_count(np.datetime64(p["data_pedido"].min().date(), "D"),
                                                 np.datetime64(params["_ref"], "D"),
                                                 holidays=np.array(hol, dtype="datetime64[D]")))

    # Causas: sistêmico x caso a caso
    sistemico = p["causa_id"].ne("")
    ganho_causas = float(sum(c_["ganho_mensal_estimado"] for c_ in causas))

    # Itens de processo não-concentração: bug de prazo e duplicatas
    n_dups = next((x["linhas"] for x in q if x["verificacao"].startswith("order_id duplicado (")), 0)
    processo_extra = []
    if n_falso:
        processo_extra.append({
            "id": "P1", "rotulo": "Cálculo da data prometida em dias corridos (deveria ser dias úteis)",
            "dono": "TI / Produto (motor de prazo do checkout e do OMS)", "area": "sistema",
            "acao": ("Corrigir o cálculo: 1 DU de separação + SLA em dias úteis, pulando fins de semana e feriados; "
                     "reprocessar a data prometida dos pedidos em aberto; só abrir 'atraso_entrega' após a data correta."),
            "evidencias": [f"{n_errado} de {len(p)} pedidos com data do sistema diferente da regra",
                           f"{n_falso} exceções de atraso sem atraso real ({n_falso / max(1, len(atrasos)):.0%} das de atraso)",
                           "também distorce a avaliação de SLA das transportadoras"],
            "tratativas_mes_evitadas": round(n_falso * fator_mes, 0),
            "ganho_mensal_estimado": round(n_falso * fator_mes * params["custo_contato"], 2),
            "obs": "Ganho principal é capacidade do time; em R$ consideramos só 1 contato de atendimento evitado por caso.",
        })
    if n_dups:
        processo_extra.append({
            "id": "P2", "rotulo": "Exceções duplicadas para o mesmo pedido",
            "dono": "TI / CRM de exceções", "area": "sistema",
            "acao": "Impedir 2 exceções abertas para o mesmo order_id (chave única) e mesclar as existentes.",
            "evidencias": [f"{n_dups} linhas duplicadas, com status conflitantes"],
            "tratativas_mes_evitadas": round(n_dups * fator_mes, 0),
            "ganho_mensal_estimado": 0.0,
            "obs": "Ganho em capacidade e em confiabilidade do status.",
        })

    # Resumo por tipo
    por_tipo = (p.groupby("tipo_excecao").agg(
        casos=("order_id", "size"), valor=("valor", "sum"), custo_direto=("custo_direto", "sum"),
        perda_esperada=("perda_esperada_espera", "sum"), sistemicos=("causa_id", lambda s: int(s.ne("").sum())))
        .reset_index().sort_values("casos", ascending=False))
    por_tipo["rotulo"] = por_tipo["tipo_excecao"].map(ROTULO_TIPO).fillna(por_tipo["tipo_excecao"])

    def _linhas(df, cols):
        out = []
        for _, r in df[cols].iterrows():
            d = {}
            for k, v in r.items():
                if isinstance(v, pd.Timestamp):
                    d[k] = v.strftime("%Y-%m-%d")
                elif isinstance(v, (np.floating, float)):
                    d[k] = None if np.isnan(v) else round(float(v), 2)
                elif isinstance(v, (np.integer,)):
                    d[k] = int(v)
                elif isinstance(v, (np.bool_,)):
                    d[k] = bool(v)
                elif v is pd.NaT:
                    d[k] = None
                else:
                    d[k] = v
            out.append(d)
        return out

    cols_fila = ["posicao", "order_id", "customer_id", "marca", "tipo_excecao", "status", "valor",
                 "transportadora", "uf", "data_pedido", "data_prometida", "prometida_correta", "data_entrega",
                 "atraso_du", "p_cancel_hoje", "p_cancel_espera", "custo_de_esperar", "perda_esperada_espera",
                 "custo_direto", "cliente_alto_valor", "receita_acumulada", "causa_id", "motivo", "acao_recomendada"]
    fila_out = _linhas(fila, cols_fila)
    for r in fila_out:
        r["rotulo_tipo"] = ROTULO_TIPO.get(r["tipo_excecao"], r["tipo_excecao"])

    # Séries para gráficos
    semanal = (p.groupby(["semana", "tipo_excecao"]).size().unstack(fill_value=0).sort_index())
    perda_hoje_total = float(p["perda_esperada_hoje"].sum())
    perda_semana_total = float(p["perda_esperada_espera"].sum())
    fila_custo_esperar = float(fila["custo_de_esperar"].sum())
    fifo_custo_esperar = float(fifo_hoje["custo_de_esperar"].sum())
    recuperado_prio = sim_fifo["perda_total"] - sim_prio["perda_total"]

    resumo = {
        "data_referencia": params["_ref"],
        "janela_dias": janela,
        "pedidos_em_excecao": int(len(p)),
        "linhas_brutas": int(len(pedidos)),
        "valor_pedidos": _r(p["valor"].sum()),
        "clientes_afetados": int(p["customer_id"].nunique()),
        "clientes_alto_valor": int(p.loc[p["cliente_alto_valor"], "customer_id"].nunique()),
        "custo_direto_resolver": _r(p["custo_direto"].sum()),
        "perda_esperada_hoje": _r(perda_hoje_total),
        "perda_esperada_se_esperar": _r(perda_semana_total),
        "ltv_em_risco": _r(p["ltv_em_risco"].sum()),
        "exposicao_total": _r(p["custo_direto"].sum() + perda_semana_total),
        "capacidade_diaria": params["capacidade_diaria"],
        "entrada_media_du": _r(entrada_du, 1),
        "dias_para_zerar_backlog": sim_prio["dias_para_zerar"],
        "perda_backlog_fifo": _r(sim_fifo["perda_total"]),
        "perda_backlog_prioridade": _r(sim_prio["perda_total"]),
        "recuperado_priorizacao": _r(recuperado_prio),
        "fila_hoje_custo_esperar": _r(fila_custo_esperar),
        "fifo_hoje_custo_esperar": _r(fifo_custo_esperar),
        "atrasos_falsos": n_falso,
        "atrasos_registrados": int(len(atrasos)),
        "atraso_entregue_so_encerrar": int((p["tipo_excecao"].eq("atraso_entrega") & p["entregue"]).sum()),
        "prazo_sistema_errado_pct": _r(n_errado / len(p), 3),
        "prazo_sistema_dif_media_du": _r(dif.mean(), 2),
        "duplicatas_removidas": int(n_dups),
        "casos_sistemicos": int(sistemico.sum()),
        "casos_caso_a_caso": int((~sistemico).sum()),
        "excesso_sistemico": _r(sum(c_["excesso"] for c_ in causas), 0),
        "custo_casos_sistemicos": _r(custo_unit[sistemico].sum()),
        "custo_casos_caso_a_caso": _r(custo_unit[~sistemico].sum()),
        "ganho_mensal_causas": _r(ganho_causas),
        "tratativas_mes_evitadas": _r(sum(c_["tratativas_mes_evitadas"] for c_ in causas)
                                      + sum(x["tratativas_mes_evitadas"] for x in processo_extra), 0),
        "horas_time_dia": _r(params["capacidade_diaria"] * params["minutos_por_tratativa"] / 60, 1),
    }

    caso_a_caso = (p[~sistemico].groupby("tipo_excecao").agg(casos=("order_id", "size"),
                                                              custo=("custo_direto", "sum"),
                                                              perda=("perda_esperada_espera", "sum"))
                   .reset_index().sort_values("casos", ascending=False))
    caso_a_caso["rotulo"] = caso_a_caso["tipo_excecao"].map(ROTULO_TIPO)
    playbook = {
        "atraso_entrega": "Acionar transportadora + aviso proativo com nova data; encerrar os já entregues em lote.",
        "rastreio_parado": "Cobrar rastreio; após 5 DU do prazo, tratar como extravio (reenvio + acareação).",
        "extravio": "Reenvio imediato + ressarcimento da transportadora.",
        "devolvido_transportadora": "Confirmar endereço e redespachar.",
        "avaria_transporte": "Reenvio sem exigir devolução; foto para ressarcimento.",
        "vazamento_produto": "Reenvio sem exigir devolução; foto para qualidade.",
        "item_faltante": "Enviar só o item/brinde faltante.",
        "falha_personalizacao": "Reimprimir com conferência visual.",
        "pagamento_pendente": "Automatizar: novo link Pix + lembrete; cancelar após 2 DU (não precisa de pessoa).",
        "ruptura_pos_venda": "Oferecer similar ou cupom; reembolso se recusar.",
        "endereco_divergente": "Automatizar: confirmação de endereço por WhatsApp antes do despacho; validar CEP no checkout.",
    }
    caso_a_caso["playbook"] = caso_a_caso["tipo_excecao"].map(playbook).fillna("Analisar caso.")

    # comparação sistema x correto (atrasos)
    comp = {
        "atraso_pelo_sistema": int((atrasos["atraso_sistema_du"] > 0).sum()),
        "atraso_pelo_correto": int((atrasos["atraso_entrega_du"] > 0).sum()),
        "falsos_entregues_no_prazo": int((atrasos["atraso_falso"] & atrasos["entregue"]).sum()),
        "falsos_em_transito_no_prazo": int((atrasos["atraso_falso"] & ~atrasos["entregue"]).sum()),
    }
    desemp = (p[p["entregue"]].groupby("transportadora")
              .agg(entregues=("order_id", "size"), atraso_medio_du=("atraso_entrega_du", "mean"),
                   pct_atrasado=("atraso_entrega_du", lambda s: float((s > 0).mean())),
                   atraso_medio_sistema=("atraso_sistema_du", "mean"))
              .reset_index())

    dist_atraso = (p[p["tipo_excecao"].isin(list(TIPOS_TRANSPORTE))]["atraso_du"].clip(-5, 20)
                   .value_counts().sort_index())
    return {
        "params": {k: v for k, v in params.items() if not k.startswith("_")},
        "resumo": resumo,
        "qualidade": q,
        "fila": fila_out,
        "causas": causas,
        "processo_extra": processo_extra,
        "caso_a_caso": _linhas(caso_a_caso, ["tipo_excecao", "rotulo", "casos", "custo", "perda", "playbook"]),
        "por_tipo": _linhas(por_tipo, ["tipo_excecao", "rotulo", "casos", "valor", "custo_direto", "perda_esperada", "sistemicos"]),
        "prazo": comp,
        "desempenho_transportadoras": _linhas(desemp, list(desemp.columns)),
        "concentracoes": _linhas(detectar_concentracoes(p, params).head(25),
                                 ["dimensao", "segmento", "tipo_excecao", "observado", "esperado", "excesso", "lift"]),
        "graficos": {
            "semanal": {"semanas": list(semanal.index), "series": {c_: semanal[c_].astype(int).tolist() for c_ in semanal.columns}},
            "dist_atraso": {"dias": [int(x) for x in dist_atraso.index], "casos": [int(x) for x in dist_atraso.values]},
            "fila_por_tipo": fila["tipo_excecao"].map(ROTULO_TIPO).value_counts().to_dict(),
        },
        "_df": p,
    }


def carregar_padrao(pasta: str = "dados") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    import os
    return (ler_csv(os.path.join(pasta, "pedidos_excecao.csv")), ler_csv(os.path.join(pasta, "clientes.csv")),
            ler_csv(os.path.join(pasta, "transportadoras.csv")))


if __name__ == "__main__":
    import json
    res = analisar(*carregar_padrao())
    res.pop("_df")
    print(json.dumps(res["resumo"], indent=2, ensure_ascii=False))
    for c_ in res["causas"]:
        print(c_["id"], c_["rotulo"], c_["observado"], c_["excesso"], c_["ganho_mensal_estimado"], c_["dono"])
        print("   ", c_["evidencias"])
    for x in res["qualidade"]:
        print("-", x["verificacao"], x["linhas"], x["acao"])
