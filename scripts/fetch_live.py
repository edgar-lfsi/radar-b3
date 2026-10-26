"""Baixa o catalogo de cotacoes intradiarias da B3 via brapi.dev.

Sem token: limite de requisicoes baixo, por isso o script faz pausas e
repete paginas com erro. Salva data/raw/live_brapi.json.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

URL = "https://brapi.dev/api/quote/list"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def get_page(page: int, limit: int = 100) -> dict:
    for attempt in range(4):
        try:
            r = requests.get(URL, params={"sortBy": "volume", "sortOrder": "desc",
                                          "limit": limit, "page": page,
                                          "type": "stock"},
                             headers=HEADERS, timeout=90)
            if r.status_code == 200:
                return r.json()
            print(f"  pagina {page}: HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            print(f"  pagina {page}: erro {exc}")
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"falhou pagina {page}")


def main() -> None:
    first = get_page(1)
    total_pages = first["totalPages"]
    rows = first["stocks"]
    out = RAW / "live_brapi.json"
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"pagina 1/{total_pages} - {len(rows)} acoes")
    for page in range(2, total_pages + 1):
        data = get_page(page)
        rows.extend(data["stocks"])
        out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        print(f"pagina {page}/{total_pages} - {len(rows)} acoes")
        time.sleep(1.0)
    print(f"total {len(rows)} acoes salvas")


if __name__ == "__main__":
    main()
