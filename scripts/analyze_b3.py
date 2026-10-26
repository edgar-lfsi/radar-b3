"""Analise de liquidez e pressao de negociacao na B3.

Entradas:
  data/raw/trades_YYYY-MM-DD.csv  (Negocios Consolidados do Pregao - B3)
  data/raw/instruments_YYYY-MM-DD.csv (Cadastro de Instrumentos - B3)
  data/raw/live_brapi.json (cotacoes intradiarias - brapi.dev)

Saidas em output/:
  ranking_volume.csv, ranking_negocios.csv, ranking_quantidade.csv,
  variacao_liquidez.csv, pressao.csv, block_trades.csv,
  live_top.csv, live_breadth.csv, live_setores.csv, ibov_pesos.csv
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

STOCK_CATEGORIES = {"SHARES", "UNIT"}


def read_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        sep=";",
        skiprows=1,
        encoding="latin-1",
        dtype={"TckrSymb": str, "SgmtNm": str},
        decimal=",",
        thousands=".",
        na_values=["", " "],
    )
    for c in ["MinPric", "MaxPric", "TradAvrgPric", "LastPric", "OscnPctg",
              "RefPric", "TradQty", "FinInstrmQty", "NtlFinVol"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def read_instruments(path: Path) -> pd.DataFrame:
    ins = pd.read_csv(path, sep=";", skiprows=1, encoding="latin-1", dtype=str)
    return ins[["TckrSymb", "SctyCtgyNm", "MktNm", "CrpnNm", "ISIN"]].drop_duplicates("TckrSymb")


def spot(df: pd.DataFrame) -> pd.DataFrame:
    """Mercado a vista: lote padrao + fracionario, com ticker normalizado."""
    s = df[df["SgmtNm"].isin(["CASH", "ODD LOT"])].copy()
    s["Ativo"] = s["TckrSymb"].str.replace(r"F$", "", regex=True)
    return s.groupby("Ativo", as_index=False).agg(
        volume=("NtlFinVol", "sum"),
        negocios=("TradQty", "sum"),
        quantidade=("FinInstrmQty", "sum"),
        preco_ult=("LastPric", "last"),
        preco_min=("MinPric", "min"),
        preco_max=("MaxPric", "max"),
        preco_med=("TradAvrgPric", "mean"),
        oscilacao=("OscnPctg", "last"),
    )


def enrich(df: pd.DataFrame, ins: pd.DataFrame) -> pd.DataFrame:
    out = df.merge(ins, left_on="Ativo", right_on="TckrSymb", how="left")
    out["eh_acao"] = out["SctyCtgyNm"].isin(STOCK_CATEGORIES) & (out["MktNm"] == "EQUITY-CASH")
    return out


def main() -> None:
    ins = read_instruments(RAW / "instruments_2026-10-23.csv")
    files = sorted(RAW.glob("trades_*.csv"))
    frames = {}
    for f in files:
        d = f.stem.split("_")[1]
        frames[d] = enrich(spot(read_trades(f)), ins)

    last_date = max(frames)
    last = frames[last_date]
    acoes = last[last.eh_acao].copy()
    raw_last = read_trades(RAW / f"trades_{last_date}.csv")

    # --- rankings do ultimo pregao -------------------------------------
    cols = ["Ativo", "CrpnNm", "volume", "negocios", "quantidade",
            "preco_ult", "preco_min", "preco_max", "preco_med", "oscilacao"]
    acoes.sort_values("volume", ascending=False)[cols].head(30).to_csv(
        OUT / "ranking_volume.csv", index=False, float_format="%.2f")
    acoes.sort_values("negocios", ascending=False)[cols].head(30).to_csv(
        OUT / "ranking_negocios.csv", index=False, float_format="%.2f")
    acoes.sort_values("quantidade", ascending=False)[cols].head(30).to_csv(
        OUT / "ranking_quantidade.csv", index=False, float_format="%.2f")

    # --- liquidez vs media de 20 pregoes ------------------------------
    hist = []
    for d, fr in frames.items():
        x = fr[fr.eh_acao][["Ativo", "volume", "negocios"]].copy()
        x["data"] = d
        hist.append(x)
    hist = pd.concat(hist, ignore_index=True)
    media = (hist[hist.data < last_date]
             .groupby("Ativo")[["volume", "negocios"]]
             .mean()
             .rename(columns={"volume": "vol_medio", "negocios": "neg_medio"}))
    liq = acoes.set_index("Ativo").join(media)
    liq["vol_rel"] = liq["volume"] / liq["vol_medio"]
    liq["neg_rel"] = liq["negocios"] / liq["neg_medio"]
    liq = liq.reset_index().sort_values("vol_rel", ascending=False)
    liq[["Ativo", "CrpnNm", "volume", "vol_medio", "vol_rel", "negocios",
         "neg_medio", "neg_rel", "oscilacao"]].query("volume > 5e6").head(30).to_csv(
        OUT / "variacao_liquidez.csv", index=False, float_format="%.3f")

    # --- proxy de pressao compradora/vendedora ------------------------
    p = acoes.copy()
    rng = (p["preco_max"] - p["preco_min"]).replace(0, np.nan)
    p["pos_fechamento"] = (p["preco_ult"] - p["preco_min"]) / rng
    p["fech_vs_medio"] = (p["preco_ult"] - p["preco_med"]) / p["preco_med"]
    p["vol_rel"] = p["volume"] / p["Ativo"].map(liq.set_index("Ativo")["vol_medio"])
    sel = p[p.volume > 2e7].copy()
    sel = sel.sort_values("pos_fechamento", ascending=False)
    sel[["Ativo", "CrpnNm", "oscilacao", "pos_fechamento", "fech_vs_medio",
         "volume", "vol_rel"]].to_csv(
        OUT / "pressao.csv", index=False, float_format="%.3f")

    # --- block trades (negocios bilaterais) ---------------------------
    blk = raw_last[raw_last["SgmtNm"] == "Equity Block Trading Lot"].copy()
    blk["Ativo"] = blk["TckrSymb"].str.replace(r"Q$", "", regex=True)
    blk = blk.rename(columns={"TradQty": "negocios", "TradAvrgPric": "preco",
                              "FinInstrmQty": "quantidade", "NtlFinVol": "volume"})
    blk.sort_values("volume", ascending=False)[
        ["Ativo", "negocios", "preco", "quantidade", "volume"]].to_csv(
        OUT / "block_trades.csv", index=False, float_format="%.2f")

    # --- cotacoes intradiarias (brapi) --------------------------------
    live = pd.DataFrame(json.loads((RAW / "live_brapi.json").read_text(encoding="utf-8")))
    live = live[live["type"].isin(["stock", "unit"])].copy()
    live = live[~live["stock"].str.endswith("F")].copy()
    live["vol_fin_est"] = live["volume"] * live["close"]
    ref = last.set_index("Ativo")
    live["vol_sexta"] = live["stock"].map(ref["volume"])
    live["vol_rel_sexta"] = live["vol_fin_est"] / live["vol_sexta"]
    live["qtd_sexta"] = live["stock"].map(ref["quantidade"])
    live["qtd_rel_sexta"] = live["volume"] / live["qtd_sexta"]

    live.sort_values("vol_fin_est", ascending=False)[
        ["stock", "name", "sector", "close", "change", "volume", "vol_fin_est",
         "vol_sexta", "vol_rel_sexta", "qtd_rel_sexta", "market_cap"]].head(30).to_csv(
        OUT / "live_top.csv", index=False, float_format="%.2f")

    breadth = pd.DataFrame({
        "metrica": ["alta", "baixa", "estavel", "total", "mediana_var",
                    "media_var", "pct_queda_10", "pct_alta_10"],
        "valor": [
            int((live.change > 0).sum()), int((live.change < 0).sum()),
            int((live.change == 0).sum()), len(live),
            live.change.median(), live.change.mean(),
            float((live.change <= -10).mean()), float((live.change >= 10).mean()),
        ],
    })
    breadth.to_csv(OUT / "live_breadth.csv", index=False, float_format="%.2f")

    setores = (live.groupby("sector")
               .apply(lambda d: pd.Series({
                   "n": len(d),
                   "var_media": d.change.mean(),
                   "var_mediana": d.change.median(),
                   "var_pond": (d.change * d.vol_fin_est).sum() / d.vol_fin_est.sum(),
                   "vol_fin": d.vol_fin_est.sum(),
                   "mcap": d.market_cap.sum(),
               }), include_groups=False)
               .sort_values("var_pond"))
    setores.to_csv(OUT / "live_setores.csv", float_format="%.2f")

    # --- pesos do Ibovespa (BDI oficial) -----------------------------
    ibov = []
    pat = re.compile(r"^([A-Z0-9]{4,6})\s+(.+?)\s+(-?[\d.]+)\s+([\d,]+)$")
    txt = (RAW / "bdi_text.txt").read_text(encoding="utf-8", errors="ignore")
    a, b = txt.find("IBOVESPA"), txt.find("IGCX", txt.find("IBOVESPA"))
    for line in txt[a:b].splitlines():
        m = pat.match(line.strip())
        if m:
            ibov.append((m.group(1), m.group(2).strip(), float(m.group(4).replace(",", "."))))
    pesos = pd.DataFrame(ibov, columns=["Ativo", "nome", "peso_pct"]).drop_duplicates("Ativo")
    pesos.to_csv(OUT / "ibov_pesos.csv", index=False, float_format="%.4f")

    # --- blue chips: peso no Ibovespa x liquidez x movimento de hoje ----
    blue = pesos.merge(
        live[["stock", "close", "change", "volume", "vol_fin_est", "vol_rel_sexta", "sector"]],
        left_on="Ativo", right_on="stock", how="left",
    ).merge(
        acoes[["Ativo", "volume", "negocios"]].rename(
            columns={"volume": "vol_sexta_b3", "negocios": "neg_sexta"}),
        on="Ativo", how="left",
    )
    blue["peso_x_vol"] = blue["peso_pct"] * blue["vol_fin_est"].fillna(0)
    blue.sort_values("peso_pct", ascending=False).to_csv(
        OUT / "blue_chips.csv", index=False, float_format="%.2f")

    # --- concentracao e totais -----------------------------------------
    tot_hoje = live["vol_fin_est"].sum()
    tot_sexta = acoes["volume"].sum()
    top10 = live.nlargest(10, "vol_fin_est")["vol_fin_est"].sum()
    resumo = {
        "pregao_referencia": last_date,
        "acoes_negociadas_sexta": int((acoes.volume > 0).sum()),
        "vol_fin_acoes_sexta": round(float(tot_sexta), 2),
        "vol_fin_acoes_hoje_parcial": round(float(tot_hoje), 2),
        "vol_hoje_vs_sexta": round(float(tot_hoje / tot_sexta), 3),
        "concentracao_top10_hoje": round(float(top10 / tot_hoje), 4),
        "acoes_alta": int((live.change > 0).sum()),
        "acoes_baixa": int((live.change < 0).sum()),
        "mediana_variacao": round(float(live.change.median()), 2),
        "pct_queda_10": round(float((live.change <= -10).mean()), 4),
        "atualizado_em": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
    }
    (OUT / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"Pregao de referencia: {last_date}")
    print(f"Acoes (SHARES+UNIT): {len(acoes)} | com volume > 0: {(acoes.volume > 0).sum()}")
    print(f"Volume financeiro total (acoes): R$ {acoes.volume.sum()/1e9:,.2f} bi")
    print(f"Live: {len(live)} acoes | breadth alta {int((live.change>0).sum())} / baixa {int((live.change<0).sum())}")
    print("OK")


if __name__ == "__main__":
    main()
