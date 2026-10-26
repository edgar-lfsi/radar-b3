"""Coleta de dados publicos da B3 (negocios consolidados por ativo).

Fonte: API de arquivos publicos da B3 (arquivos.b3.com.br).
Baixa o arquivo "Negocios Consolidados do Pregao (Listado)" para cada
data de pregao informada e salva em data/raw/.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

BASE = "https://arquivos.b3.com.br"
CHANNEL = "34dcaaeb-0306-4f45-a83e-4f66a23b42fa"
SUBCHANNEL = "cc188e40-03be-408e-aa86-501926b97a76"
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def list_trade_dates(date_from: str, date_to: str) -> list[str]:
    url = f"{BASE}/api/channels/{CHANNEL}/subchannels/{SUBCHANNEL}/publications"
    r = requests.get(
        url,
        params={"lang": "pt", "from": date_from, "to": date_to},
        headers=HEADERS,
        timeout=60,
    )
    r.raise_for_status()
    rows = r.json()
    dates = {
        row["dateTime"][:10]
        for row in rows
        if "TradeInformationConsolidated" in row["fileName"]
        and "AfterHours" not in row["fileName"]
    }
    return sorted(dates)


def download_trade_file(date: str) -> Path:
    dest = RAW / f"trades_{date}.csv"
    if dest.exists() and dest.stat().st_size > 100_000:
        return dest

    url = f"{BASE}/api/download/requestname"
    r = requests.get(
        url,
        params={"fileName": "TradeInformationConsolidated", "date": date},
        headers=HEADERS,
        timeout=60,
    )
    r.raise_for_status()
    redirect = r.json()["redirectUrl"]  # ~/download?token=...
    token = redirect.split("token=", 1)[1]
    dl = f"{BASE}/api/download/?token={token}"
    with requests.get(dl, headers=HEADERS, timeout=300, stream=True) as resp:
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    return dest


def main() -> None:
    date_from = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
    date_to = sys.argv[2] if len(sys.argv) > 2 else "2026-10-26"
    RAW.mkdir(parents=True, exist_ok=True)
    dates = list_trade_dates(date_from, date_to)
    print(f"{len(dates)} pregoes encontrados: {dates[0]} .. {dates[-1]}")
    for d in dates:
        p = download_trade_file(d)
        print(f"  {d} -> {p.name} ({p.stat().st_size:,} bytes)")
        time.sleep(0.6)


if __name__ == "__main__":
    main()
