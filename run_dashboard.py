"""Inicia o painel Radar B3 e abre o navegador.

Uso:
    python run_dashboard.py
ou clique em painel.bat
"""
from __future__ import annotations

import threading
import time
import webbrowser

import uvicorn

URL = "http://127.0.0.1:8055"


def open_browser() -> None:
    time.sleep(2.5)
    webbrowser.open(URL)


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()
    print(f"Radar B3 em {URL}  (Ctrl+C para encerrar)")
    uvicorn.run("dashboard.app:app", host="127.0.0.1", port=8055, log_level="warning")
