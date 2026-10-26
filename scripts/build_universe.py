"""Monta o universo de acoes do painel a partir dos arquivos da B3.

Saida: data/universe.csv com ticker, nome, volume/negocios do ultimo pregao
e o tamanho medio de negocio (base para estimar ordens no intradiario).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

trades = pd.read_csv(
    RAW / "trades_2026-10-23.csv", sep=";", skiprows=1, encoding="latin-1",
    dtype={"TckrSymb": str, "SgmtNm": str}, decimal=",", thousands=".",
    na_values=["", " "],
)
for c in ["TradQty", "FinInstrmQty", "NtlFinVol", "LastPric"]:
    trades[c] = pd.to_numeric(trades[c], errors="coerce")

inst = pd.read_csv(RAW / "instruments_2026-10-23.csv", sep=";", skiprows=1,
                   encoding="latin-1", dtype=str)[["TckrSymb", "SctyCtgyNm", "MktNm", "CrpnNm"]]
inst = inst.drop_duplicates("TckrSymb")

spot = trades[(trades.SgmtNm == "CASH") & (~trades.TckrSymb.str.endswith("F"))].copy()
g = spot.groupby("TckrSymb", as_index=False).agg(
    volume=("NtlFinVol", "sum"),
    negocios=("TradQty", "sum"),
    quantidade=("FinInstrmQty", "sum"),
    preco=("LastPric", "last"),
)
g = g.merge(inst, left_on="TckrSymb", right_on="TckrSymb", how="left")
g = g[(g.SctyCtgyNm.isin(["SHARES", "UNIT"])) & (g.MktNm == "EQUITY-CASH")]
g = g[(g.volume > 1_000_000) & (g.negocios > 0) & (g.quantidade > 0)]
g["tamanho_medio_negocio"] = g.quantidade / g.negocios
g["ticket_medio"] = g.volume / g.negocios
g = g.sort_values("volume", ascending=False).reset_index(drop=True)
g["nome"] = g["CrpnNm"].fillna(g["TckrSymb"])

out = g[["TckrSymb", "nome", "volume", "negocios", "quantidade", "preco",
         "tamanho_medio_negocio", "ticket_medio"]].rename(columns={
             "TckrSymb": "ticker", "volume": "vol_prev", "negocios": "neg_prev",
             "quantidade": "qtd_prev", "preco": "preco_prev"})
out.to_csv(ROOT / "data" / "universe.csv", index=False)
print(f"{len(out)} acoes no universo")
print(out.head(8).to_string(index=False))
