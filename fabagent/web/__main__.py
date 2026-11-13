"""Pokretanje: python -m fabagent.web [--port 8000] [--no-browser]

Server slusa ISKLJUCIVO na 127.0.0.1 (samo ovaj racunar)."""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

HOST = "127.0.0.1"
DEFAULT_PORT = 8000
BROWSER_DELAY_S = 1.5


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m fabagent.web")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true", help="ne otvaraj browser automatski")
    args = ap.parse_args()
    url = f"http://{HOST}:{args.port}/radni"     # radni ekran (crtanje + laser) je pocetna stranica
    if not args.no_browser:
        threading.Timer(BROWSER_DELAY_S, webbrowser.open, args=(url,)).start()
    print(f"FabAgent web: {url}  (zatvori ovaj prozor ili Ctrl+C za kraj)")
    uvicorn.run("fabagent.web.app:app", host=HOST, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
