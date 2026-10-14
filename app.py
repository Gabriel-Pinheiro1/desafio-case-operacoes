"""
App web local: `python app.py` -> http://127.0.0.1:8765

Endpoints
  GET  /                    interface
  GET  /api/analise         análise atual (resumo, fila, causas, qualidade, gráficos)
  GET  /api/backlog         backlog completo com prioridade e dia planejado
  POST /api/parametros      JSON com parâmetros -> recalcula
  POST /api/upload          multipart: pedidos (obrig.), clientes (obrig.), transportadoras (opcional), params (JSON opcional)
  POST /api/reset           volta aos dados e parâmetros originais
  GET  /api/fila.csv        fila de hoje em CSV (para o time)
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

import analise as A

BASE = os.path.dirname(os.path.abspath(__file__))
_lock = threading.Lock()
_estado: dict = {}


def _nativo(o):
    if isinstance(o, dict):
        return {str(k): _nativo(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_nativo(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if np.isnan(f) or np.isinf(f) else f
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime, date)):
        return None if pd.isna(o) else o.strftime("%Y-%m-%d")
    if o is pd.NaT:
        return None
    return o


def _recalcular(pedidos=None, clientes=None, transp=None, params=None, origem=None):
    with _lock:
        ped = pedidos if pedidos is not None else _estado["pedidos"]
        cli = clientes if clientes is not None else _estado["clientes"]
        tra = transp if transp is not None else _estado["transp"]
        prm = {**_estado.get("params", {}), **(params or {})}  # atualização parcial mantém o resto
        res = A.analisar(ped, cli, tra, prm)  # levanta ValueError se os dados forem inválidos
        _estado.update(pedidos=ped, clientes=cli, transp=tra, params=res["params"], res=res,
                       origem=origem or _estado.get("origem", "dados originais (pasta dados/)"),
                       calculado_em=datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
        return res


def _carregar_original():
    ped, cli, tra = A.carregar_padrao(os.path.join(BASE, "dados"))
    _estado["params"] = {}
    _recalcular(ped, cli, tra, dict(A.DEFAULT_PARAMS), origem="dados originais (pasta dados/)")


def _payload():
    res = _estado["res"]
    out = {k: v for k, v in res.items() if k != "_df"}
    out["meta"] = {"origem": _estado["origem"], "calculado_em": _estado["calculado_em"],
                   "linhas_pedidos": int(len(_estado["pedidos"])), "linhas_clientes": int(len(_estado["clientes"])),
                   "linhas_transportadoras": int(len(_estado["transp"])), "defaults": A.DEFAULT_PARAMS}
    return _nativo(out)


@asynccontextmanager
async def _lifespan(_app):
    _carregar_original()
    yield


app = FastAPI(title="Exceções de pedidos · Operações", lifespan=_lifespan)


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


@app.get("/api/analise")
def api_analise():
    return JSONResponse(_payload())


@app.get("/api/backlog")
def api_backlog():
    p = _estado["res"]["_df"]
    cols = ["order_id", "marca", "tipo_excecao", "status", "valor", "transportadora", "uf", "data_pedido",
            "data_prometida", "prometida_correta", "data_entrega", "atraso_du", "p_cancel_hoje", "custo_de_esperar",
            "custo_direto", "perda_esperada_priorizada", "dia_tratativa_prioridade", "dia_tratativa_fifo", "causa_id"]
    df = A.ordenar_prioridade(p)[cols].copy()
    df.insert(0, "prioridade", np.arange(1, len(df) + 1))
    for c in ["data_pedido", "data_prometida", "prometida_correta", "data_entrega"]:
        df[c] = df[c].dt.strftime("%Y-%m-%d")
    df = df.round(2).astype(object).where(df.notna(), None)
    return JSONResponse(_nativo(df.to_dict(orient="records")))


@app.post("/api/parametros")
async def api_parametros(params: dict):
    try:
        _recalcular(params=params)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Parâmetros inválidos: {e}")
    return JSONResponse(_payload())


@app.post("/api/upload")
async def api_upload(pedidos: UploadFile = File(...), clientes: UploadFile = File(...),
                     transportadoras: UploadFile | None = File(None), params: str | None = Form(None)):
    try:
        ped = A.ler_csv(await pedidos.read())
        cli = A.ler_csv(await clientes.read())
        tra = A.ler_csv(await transportadoras.read()) if transportadoras is not None and transportadoras.filename else None
        prm = json.loads(params) if params else {}
        # dados novos: a data de referência passa a ser a última abertura de exceção do arquivo, salvo se informada
        prm.setdefault("data_referencia", "auto")
        nomes = [pedidos.filename, clientes.filename] + ([transportadoras.filename] if tra is not None else [])
        _recalcular(ped, cli, tra, prm, origem="upload: " + ", ".join(nomes))
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        raise HTTPException(400, f"Não foi possível processar os arquivos: {e}")
    return JSONResponse(_payload())


@app.post("/api/reset")
def api_reset():
    _carregar_original()
    return JSONResponse(_payload())


@app.get("/api/fila.csv")
def api_fila_csv():
    f = pd.DataFrame(_estado["res"]["fila"])
    csv = f.to_csv(index=False, sep=";", decimal=",")
    return Response(content="﻿" + csv, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=fila_hoje_{_estado['res']['resumo']['data_referencia']}.csv"})


if __name__ == "__main__":
    import uvicorn
    porta = int(os.environ.get("PORT", "8765"))
    print(f"\n  Exceções de pedidos · abra http://127.0.0.1:{porta}\n")
    uvicorn.run(app, host="127.0.0.1", port=porta, log_level="warning")
