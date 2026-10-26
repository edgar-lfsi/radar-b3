"""Painel Radar B3 — backend FastAPI.

Fontes publicas:
  - scanner do TradingView: ranking, variacoes e volume de todo o mercado B3
  - API de cotacoes da B3: IBOV
  - Yahoo Finance: historico de precos (dia/semana/mes/ano)
  - Google News RSS: noticias por ativo

O servidor faz snapshots a cada 10s para estimar o fluxo comprador/vendedor
por regra de tick. A B3 nao divulga o agressor em tempo real de forma publica.
"""
from __future__ import annotations

import asyncio
import html
import json
import math
import time
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / "data"
OUT = ROOT.parent / "output"

SCANNER_URL = "https://scanner.tradingview.com/brazil/scan"
UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
UA_GET = {"User-Agent": "Mozilla/5.0"}

COLS = [
    "name", "description", "sector", "close", "open", "high", "low", "VWAP", "change",
    "change|1", "change|5", "change|15", "change|60", "change|240",
    "change|1W", "change|1M",
    "volume", "volume|15", "volume|60", "volume|240", "volume|1W", "volume|1M",
    "average_volume_20d_calc", "market_cap_basic",
]

SECTOR_PT = {
    "Finance": "Financeiro",
    "Retail Trade": "Varejo",
    "Consumer Services": "Serviços ao consumidor",
    "Transportation": "Transporte",
    "Utilities": "Energia e saneamento",
    "Energy Minerals": "Petróleo e gás",
    "Non-Energy Minerals": "Mineração e siderurgia",
    "Process Industries": "Papel, celulose e química",
    "Producer Manufacturing": "Bens de capital",
    "Electronic Technology": "Defesa e eletrônicos",
    "Consumer Non-Durables": "Consumo não durável",
    "Consumer Durables": "Bens de consumo duráveis",
    "Health Services": "Saúde",
    "Health Technology": "Saúde (tecnologia)",
    "Technology Services": "Tecnologia",
    "Commercial Services": "Serviços comerciais",
    "Industrial Services": "Serviços industriais",
    "Distribution Services": "Distribuição",
    "Communications": "Telecom e comunicações",
    "Miscellaneous": "Outros",
}

PERIODS: dict[str, dict[str, Any]] = {
    "1m": {"change": "change|1", "secs": 60, "label": "1 min"},
    "5m": {"change": "change|5", "secs": 300, "label": "5 min"},
    "15m": {"change": "change|15", "secs": 900, "label": "15 min"},
    "1h": {"change": "change|60", "secs": 3600, "label": "1 hora"},
    "1d": {"change": "change", "vol": "volume", "label": "Dia"},
    "1w": {"change": "change|1W", "vol": "volume|1W", "label": "Semana"},
    "1M": {"change": "change|1M", "vol": "volume|1M", "label": "Mês"},
}

HISTORY: dict[str, dict[str, Any]] = {
    "1d": {"range": "1d", "intervals": ["1m", "5m", "15m", "60m"], "default": "5m",
           "label": "Dia"},
    "1w": {"range": "5d", "intervals": ["15m", "60m", "1d"], "default": "60m",
           "label": "Semana"},
    "1m": {"range": "1mo", "intervals": ["60m", "1d"], "default": "1d",
           "label": "Mês"},
    "1y": {"range": "1y", "intervals": ["1d", "1wk"], "default": "1d",
           "label": "Ano"},
}

INTERVAL_LABEL = {
    "1m": "1 min", "5m": "5 min", "15m": "15 min", "60m": "1 hora",
    "1d": "1 dia", "1wk": "1 semana",
}

POLL_SECONDS = 10
MAX_POINTS = 1200

STATE: dict[str, Any] = {
    "rows": {},
    "snaps": defaultdict(lambda: deque(maxlen=MAX_POINTS)),
    "updated": 0.0,
    "error": None,
    "market": {},
}
UNIVERSE: dict[str, dict[str, float]] = {}
NAMES: dict[str, str] = {}
IBOV: set[str] = set()
CACHE: dict[str, tuple[float, Any]] = {}
HIST_CACHE: dict[tuple[str, str, str], tuple[float, Any]] = {}
NEWS_CACHE: dict[str, tuple[float, Any]] = {}


# ---------------------------------------------------------------- dados base
def load_universe() -> None:
    path = DATA / "universe.csv"
    if path.exists():
        df = pd.read_csv(path)
        for _, r in df.iterrows():
            t = str(r["ticker"])
            UNIVERSE[t] = {
                "avg_size": float(r["tamanho_medio_negocio"]),
                "ticket": float(r["ticket_medio"]),
            }
            NAMES.setdefault(t, str(r["nome"]))

    raw = DATA / "raw" / "live_brapi.json"
    if raw.exists():
        try:
            for d in json.load(open(raw, encoding="utf-8")):
                t = d.get("stock", "")
                if t and not t.endswith("F") and d.get("name"):
                    NAMES[t] = d["name"]
        except Exception:
            pass

    ibov_path = OUT / "ibov_pesos.csv"
    if ibov_path.exists():
        try:
            df = pd.read_csv(ibov_path)
            IBOV.update(str(x).strip().upper() for x in df["Ativo"])
        except Exception:
            pass


def wait_ready(timeout: float = 6.0) -> None:
    deadline = time.time() + timeout
    while not STATE["rows"] and time.time() < deadline:
        time.sleep(0.15)


def fetch_scanner() -> list[dict[str, Any]]:
    body = {
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "volume", "operation": "greater", "right": 100_000},
        ],
        "options": {"lang": "en"},
        "markets": ["brazil"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": COLS,
        "sort": {"sortBy": "volume", "sortOrder": "desc"},
        "range": [0, 900],
    }
    r = requests.post(SCANNER_URL, json=body, headers=UA, timeout=20)
    r.raise_for_status()
    data = r.json().get("data") or []
    out = []
    for item in data:
        d = dict(zip(COLS, item["d"]))
        ticker = d["name"]
        if not ticker or ticker.endswith("F"):
            continue
        if not d.get("close"):
            continue
        if float(d.get("volume") or 0) * float(d["close"]) < 1_000_000:
            continue
        d["ticker"] = ticker
        d["symbol"] = item.get("s", "")
        d["description"] = NAMES.get(ticker) or d.get("description") or ticker
        d["sector_pt"] = SECTOR_PT.get(d.get("sector") or "", d.get("sector") or "Outros")
        d["is_ibov"] = ticker in IBOV
        out.append(d)
    return out


def fetch_market() -> dict[str, Any]:
    market = {}
    for _ in range(2):
        try:
            r = requests.get(
                "https://cotacao.b3.com.br/mds/api/v1/instrumentQuotation/ibov",
                headers=UA_GET, timeout=8)
            t = r.json()["Trad"][0]["scty"]["SctyQtn"]
            market["ibov"] = t["curPrc"]
            market["ibov_chg"] = t["prcFlcn"]
            break
        except Exception:
            time.sleep(0.2)
    return market


def update_state(rows: list[dict[str, Any]], market: dict[str, Any]) -> None:
    now = time.time()
    for d in rows:
        t = d["ticker"]
        price = float(d["close"])
        vol = float(d.get("volume") or 0)
        snaps = STATE["snaps"][t]
        if not snaps or snaps[-1][1] != price or snaps[-1][2] != vol:
            snaps.append((now, price, vol))
    STATE["rows"] = {d["ticker"]: d for d in rows}
    STATE["updated"] = now
    STATE["error"] = None
    if market:
        STATE["market"] = market


async def poll_loop() -> None:
    while True:
        try:
            rows = await asyncio.to_thread(fetch_scanner)
            market = await asyncio.to_thread(fetch_market)
            update_state(rows, market)
        except Exception as exc:  # noqa: BLE001
            STATE["error"] = str(exc)[:200]
        await asyncio.sleep(POLL_SECONDS)


# ---------------------------------------------------------------- calculos
def minutes_open() -> float:
    now = time.time() - 3 * 3600
    lt = time.gmtime(now)
    minutes = lt.tm_hour * 60 + lt.tm_min - 600
    return max(1.0, min(390.0, float(minutes)))


def fallback_volume(row: dict[str, Any], period: str) -> float:
    day = float(row.get("volume") or 0)
    if period == "15m":
        return float(row.get("volume|15") or 0) or day * 15 / minutes_open()
    if period == "1h":
        return float(row.get("volume|60") or 0) or day * 60 / minutes_open()
    day_per_min = day / minutes_open()
    if period == "5m":
        v15 = float(row.get("volume|15") or 0)
        return max(day_per_min * 5, v15 / 3)
    v15 = float(row.get("volume|15") or 0)
    return max(day_per_min, v15 / 15)


def split_by_change(vol: float, chg: float, scale: float) -> tuple[float, float]:
    share = 0.5 + max(-0.35, min(0.35, (chg or 0) / scale))
    share = max(0.05, min(0.95, share))
    return vol * share, vol * (1 - share)


def split_row(vol: float, chg: float, row: dict[str, Any], period: str) -> tuple[float, float]:
    if period == "1d":
        hi, lo, cl = row.get("high"), row.get("low"), row.get("close")
        try:
            hi, lo, cl = float(hi), float(lo), float(cl)
            if hi > lo:
                pos = max(0.0, min(1.0, (cl - lo) / (hi - lo)))
                base = 0.5 + max(-0.35, min(0.35, (chg or 0) / 40))
                share = 0.5 * pos + 0.5 * base
                share = max(0.05, min(0.95, share))
                return vol * share, vol * (1 - share)
        except Exception:
            pass
    scale = {"1w": 60, "1M": 90, "1m": 3, "5m": 4, "15m": 5, "1h": 8}.get(period, 6)
    return split_by_change(vol, chg, scale)


def compute_period(ticker: str, period: str) -> dict[str, Any]:
    row = STATE["rows"].get(ticker)
    if not row:
        raise KeyError(ticker)
    cfg = PERIODS[period]
    chg = row.get(cfg["change"])
    chg = float(chg) if chg is not None else 0.0
    price = float(row["close"])
    exact = True

    if period in ("1d", "1w", "1M"):
        vol = float(row.get(cfg["vol"]) or 0)
        buy, sell = split_row(vol, chg, row, period)
        coverage = 0
    else:
        now = time.time()
        secs = cfg["secs"]
        snaps = [p for p in STATE["snaps"].get(ticker, ()) if p[0] >= now - secs]
        coverage = int(now - snaps[0][0]) if snaps else 0
        if len(snaps) >= 2 and coverage >= secs * 0.75:
            vol = max(0.0, snaps[-1][2] - snaps[0][2])
            buy = sell = 0.0
            for (_, p0, v0), (_, p1, v1) in zip(snaps, snaps[1:]):
                dv = max(0.0, v1 - v0)
                if dv == 0:
                    continue
                if p1 > p0:
                    buy += dv
                elif p1 < p0:
                    sell += dv
                else:
                    buy += dv / 2
                    sell += dv / 2
        else:
            vol = fallback_volume(row, period)
            buy, sell = split_row(vol, chg, row, period)
            exact = False

    total = buy + sell
    buy_share = buy / total if total else 0.5
    value = vol * price
    avg_size = UNIVERSE.get(ticker, {}).get("avg_size") or max(1.0, 10000 / price)
    orders = vol / avg_size if avg_size else 0
    ticket = value / orders if orders else 0

    return {
        "ticker": ticker,
        "name": row.get("description") or row.get("name") or ticker,
        "sector": row.get("sector_pt") or "Outros",
        "sector_raw": row.get("sector") or "",
        "is_ibov": bool(row.get("is_ibov")),
        "price": price,
        "change": chg,
        "volume": vol,
        "buy": buy,
        "sell": sell,
        "buy_value": buy * price,
        "sell_value": sell * price,
        "value": value,
        "buy_pct": buy_share * 100,
        "orders": orders,
        "orders_buy": orders * buy_share,
        "orders_sell": orders * (1 - buy_share),
        "ticket": ticket,
        "exact": exact,
        "coverage": coverage,
        "period": period,
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "vwap": row.get("VWAP"),
    }


def make_signals(row: dict[str, Any], base: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        price = float(row["close"])
        chg = float(row.get("change") or 0)
        hi, lo = float(row.get("high") or price), float(row.get("low") or price)
        vwap = float(row.get("VWAP") or price)
        vol = float(row.get("volume") or 0)
        avg = float(row.get("average_volume_20d_calc") or 0)

        if avg > 0:
            rel = vol / avg
            if rel >= 1.4:
                out.append({"label": "Volume", "value": f"{rel:.1f}x a média 20d",
                            "tone": "pos" if chg >= 0 else "neg"})
        out.append({"label": "VWAP", "value": "acima" if price >= vwap else "abaixo",
                    "tone": "pos" if price >= vwap else "neg"})
        if hi > lo:
            pos = (price - lo) / (hi - lo)
            if pos >= 0.8:
                out.append({"label": "Range", "value": "fechou no topo", "tone": "pos"})
            elif pos <= 0.2:
                out.append({"label": "Range", "value": "fechou na base", "tone": "neg"})
        if base["buy_pct"] >= 65:
            out.append({"label": "Fluxo", "value": f"{base['buy_pct']:.0f}% compra",
                        "tone": "pos"})
        elif base["buy_pct"] <= 35:
            out.append({"label": "Fluxo", "value": f"{100 - base['buy_pct']:.0f}% venda",
                        "tone": "neg"})

        ibov_chg = STATE["market"].get("ibov_chg")
        if ibov_chg is not None:
            rel = chg - float(ibov_chg)
            if abs(rel) >= 3:
                out.append({"label": "vs IBOV", "value": f"{rel:+.1f} p.p.",
                            "tone": "pos" if rel > 0 else "neg"})
    except Exception:
        pass
    return out[:5]


def ranking(period: str, sort: str = "change", sector: str = "",
            only_ibov: bool = False) -> dict[str, Any]:
    wait_ready()
    now = time.time()
    key = f"{period}|{sort}|{sector}|{only_ibov}"
    cached = CACHE.get(key)
    if cached and now - cached[0] < 2:
        return cached[1]

    items = []
    for t in STATE["rows"]:
        try:
            items.append(compute_period(t, period))
        except Exception:
            continue
    items = [i for i in items if i["volume"] > 0 and math.isfinite(i["change"])]
    min_val = {"1m": 50_000, "5m": 100_000, "15m": 250_000, "1h": 500_000}.get(period, 1_000_000)
    items = [i for i in items if i["value"] >= min_val]
    sectors = sorted({i["sector"] for i in items})

    if sector:
        items = [i for i in items if i["sector"] == sector]
    if only_ibov:
        items = [i for i in items if i["is_ibov"]]

    gainers = [i for i in items if i["change"] > 0]
    losers = [i for i in items if i["change"] < 0]
    if sort == "volume":
        gainers.sort(key=lambda x: -x["value"])
        losers.sort(key=lambda x: -x["value"])
    else:
        gainers.sort(key=lambda x: -x["change"])
        losers.sort(key=lambda x: x["change"])
    gainers = gainers[:15]
    losers = losers[:15]

    changes = [i["change"] for i in items]
    day_value = sum(float(r.get("volume") or 0) * float(r.get("close") or 0)
                    for r in STATE["rows"].values())

    payload = {
        "period": period,
        "label": PERIODS[period]["label"],
        "updated": STATE["updated"],
        "stale": bool(STATE["error"]),
        "market": STATE["market"],
        "volume_acoes": day_value,
        "gainers": gainers,
        "losers": losers,
        "universe": len(items),
        "sectors": sectors,
        "sort": sort,
        "sector": sector,
        "only_ibov": only_ibov,
        "breadth": {
            "up": sum(1 for c in changes if c > 0),
            "down": sum(1 for c in changes if c < 0),
            "flat": sum(1 for c in changes if c == 0),
            "median": float(sorted(changes)[len(changes) // 2]) if changes else 0.0,
        },
    }
    CACHE[key] = (now, payload)
    return payload


def detail(ticker: str, period: str) -> dict[str, Any]:
    wait_ready()
    ticker = ticker.upper().strip()
    row = STATE["rows"].get(ticker)
    if not row:
        raise HTTPException(status_code=404, detail="Ativo não encontrado")
    base = compute_period(ticker, period)
    cfg = PERIODS[period]
    secs = cfg.get("secs") or 3600
    now = time.time()
    pts = [p for p in STATE["snaps"].get(ticker, ()) if p[0] >= now - secs]
    series = []
    for (ts0, p0, v0), (ts1, p1, v1) in zip(pts, pts[1:]):
        dv = max(0.0, v1 - v0)
        if p1 > p0:
            b, s = dv, 0.0
        elif p1 < p0:
            b, s = 0.0, dv
        else:
            b, s = dv / 2, dv / 2
        series.append({"t": int(ts1 * 1000), "price": p1, "buy": b, "sell": s})
    if len(series) > 180:
        step = math.ceil(len(series) / 180)
        series = series[::step]

    perf = {p: (row.get(PERIODS[p]["change"]) or 0) for p in PERIODS}
    return {
        "stock": base,
        "series": series,
        "perf": perf,
        "signals": make_signals(row, base),
        "warming": len(series) < 3,
        "updated": STATE["updated"],
    }


# ---------------------------------------------------------------- historico
HIST_DIR = DATA / "cache" / "history"
HIST_DIR.mkdir(parents=True, exist_ok=True)


def _candles_from_rows(rows: list[dict[str, Any]], ticker: str) -> list[dict[str, Any]]:
    price = STATE["rows"].get(ticker, {}).get("close")
    avg_size = UNIVERSE.get(ticker, {}).get("avg_size") or max(1.0, 10000 / float(price or 1))
    candles = []
    for c in rows:
        try:
            o, h, l, cl, v = (float(c[k]) for k in ("o", "h", "l", "c", "v"))
            rng = h - l
            share = 0.5 if rng <= 0 else max(0.0, min(1.0, (cl - l) / rng))
            buy = v * share
            sell = v - buy
            orders = v / avg_size
            candles.append({
                "t": int(c["t"]), "o": o, "h": h, "l": l, "c": cl, "v": v,
                "buy": buy, "sell": sell,
                "ob": orders * share, "os": orders * (1 - share),
            })
        except Exception:
            continue
    return candles


def _fetch_yahoo(ticker: str, period: str, interval: str) -> list[dict[str, Any]] | None:
    cfg = HISTORY[period]
    sym = f"{ticker}.SA"
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?range={cfg['range']}&interval={interval}&includePrePost=false")
    try:
        r = requests.get(url, headers=UA_GET, timeout=4)
        if r.status_code != 200:
            return None
        result = (r.json().get("chart", {}).get("result") or [None])[0]
        if not result:
            return None
        ts = result.get("timestamp") or []
        q = (result.get("indicators", {}).get("quote") or [{}])[0]
        opens, highs, lows, closes, vols = (q.get(k) or [] for k in
                                            ("open", "high", "low", "close", "volume"))
        rows = []
        for i, t in enumerate(ts):
            try:
                if closes[i] is None:
                    continue
                rows.append({
                    "t": int(t * 1000),
                    "o": opens[i] if opens[i] is not None else closes[i],
                    "h": highs[i] if highs[i] is not None else closes[i],
                    "l": lows[i] if lows[i] is not None else closes[i],
                    "c": closes[i],
                    "v": vols[i] or 0,
                })
            except Exception:
                continue
        return _candles_from_rows(rows, ticker) or None
    except Exception:
        return None


def _fetch_brapi(ticker: str, period: str, interval: str) -> list[dict[str, Any]] | None:
    range_map = {"1d": "1d", "1w": "5d", "1m": "1mo", "1y": "1y"}
    url = (f"https://brapi.dev/api/quote/{ticker}"
           f"?range={range_map[period]}&interval={interval}")
    try:
        r = requests.get(url, headers=UA_GET, timeout=75)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("error"):
            return None
        hist = (data.get("results") or [{}])[0].get("historicalDataPrice") or []
        rows = [{
            "t": int(d["date"] * 1000),
            "o": d.get("open"), "h": d.get("high"), "l": d.get("low"),
            "c": d.get("close"), "v": d.get("volume") or 0,
        } for d in hist if d.get("close") is not None]
        return _candles_from_rows(rows, ticker) or None
    except Exception:
        return None


def history(ticker: str, period: str, interval: str) -> dict[str, Any]:
    cfg = HISTORY.get(period)
    if not cfg:
        raise HTTPException(status_code=400, detail="Período inválido")
    if interval not in cfg["intervals"]:
        raise HTTPException(status_code=400, detail="Granularidade inválida")

    path = HIST_DIR / f"{ticker}_{period}_{interval}.json"
    if path.exists() and time.time() - path.stat().st_mtime < 6 * 3600:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass

    source = "yahoo"
    candles = _fetch_yahoo(ticker, period, interval)
    if not candles:
        source = "brapi"
        candles = _fetch_brapi(ticker, period, interval)
    if not candles:
        raise HTTPException(status_code=502, detail="Histórico indisponível")

    total_v = sum(c["v"] for c in candles)
    total_buy = sum(c["buy"] for c in candles)
    total_sell = sum(c["sell"] for c in candles)
    payload = {
        "ticker": ticker,
        "period": period,
        "interval": interval,
        "interval_label": INTERVAL_LABEL.get(interval, interval),
        "range_label": cfg["label"],
        "source": source,
        "candles": candles,
        "summary": {
            "first": candles[0]["c"],
            "last": candles[-1]["c"],
            "change": (candles[-1]["c"] / candles[0]["c"] - 1) * 100 if candles[0]["c"] else 0,
            "volume": total_v,
            "buy": total_buy,
            "sell": total_sell,
            "buy_pct": total_buy / total_v * 100 if total_v else 50,
            "orders_buy": sum(c["ob"] for c in candles),
            "orders_sell": sum(c["os"] for c in candles),
        },
    }
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass
    return payload


# ---------------------------------------------------------------- noticias
def clean_query_name(name: str) -> str:
    junk = {"S.A.", "SA", "S/A", "LTDA", "S.A", "CIA", "COMPANHIA", "EMPRESA",
            "PARTICIPACOES", "PARTICIPAÇÕES", "HOLDING", "BRASIL", "DO", "DA", "DE"}
    parts = [p for p in name.replace(",", " ").split() if p and p.upper() not in junk]
    return " ".join(parts[:3])


def news(ticker: str, name: str = "") -> dict[str, Any]:
    ticker = ticker.upper().strip()
    now = time.time()
    cached = NEWS_CACHE.get(ticker)
    if cached and now - cached[0] < 300:
        return cached[1]

    nome = clean_query_name(name or NAMES.get(ticker, ""))
    q = f"{ticker} {nome}".strip()
    url = ("https://news.google.com/rss/search?q=" + quote(q) +
           "&hl=pt-BR&gl=BR&ceid=BR:pt-419")
    items = []
    try:
        r = requests.get(url, headers=UA_GET, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        for item in root.iter("item"):
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub = item.findtext("pubDate") or ""
            src_el = item.find("source")
            source = src_el.text if src_el is not None else ""
            if not title or not link:
                continue
            try:
                dt = parsedate_to_datetime(pub)
                pub_iso = dt.astimezone(timezone.utc).isoformat()
            except Exception:
                pub_iso = ""
            items.append({
                "title": html.unescape(title),
                "link": link,
                "source": html.unescape(source or ""),
                "published": pub_iso,
            })
            if len(items) >= 10:
                break
    except Exception as exc:  # noqa: BLE001
        return {"ticker": ticker, "items": [], "error": str(exc)[:120],
                "query": q, "updated": now}

    payload = {"ticker": ticker, "items": items, "error": None, "query": q, "updated": now}
    NEWS_CACHE[ticker] = (now, payload)
    return payload


# ---------------------------------------------------------------- app
@asynccontextmanager
async def lifespan(app: FastAPI):
    load_universe()
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()


app = FastAPI(title="Radar B3", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/ranking")
def api_ranking(period: str = Query("1d"), sort: str = Query("change"),
                sector: str = Query(""), ibov: bool = Query(False)):
    if period not in PERIODS:
        raise HTTPException(status_code=400, detail="Período inválido")
    if sort not in ("change", "volume"):
        raise HTTPException(status_code=400, detail="Ordenação inválida")
    return ranking(period, sort, sector, ibov)


@app.get("/api/stock/{ticker}")
def api_stock(ticker: str, period: str = Query("1d")):
    if period not in PERIODS:
        raise HTTPException(status_code=400, detail="Período inválido")
    return detail(ticker, period)


@app.get("/api/history/{ticker}")
def api_history(ticker: str, period: str = Query("1d"), interval: str = Query("5m")):
    return history(ticker.upper().strip(), period, interval)


@app.get("/api/news/{ticker}")
def api_news(ticker: str, name: str = Query("")):
    return news(ticker, name)


@app.get("/api/status")
def api_status():
    return {
        "updated": STATE["updated"],
        "error": STATE["error"],
        "tickers": len(STATE["rows"]),
        "market": STATE["market"],
        "ibov_count": len(IBOV),
    }
