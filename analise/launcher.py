# -*- coding: utf-8 -*-
"""Launcher para o executável onefile do relatório de extratos.

- Dentro do exe (frozen): usa os arquivos embutidos em sys._MEIPASS
- Se existir extratos.db AO LADO do .exe, usa ele (permite atualizar os dados
  sem rebuildar); senão usa o extratos.db embutido.
"""
import os
import sys

from streamlit.web import cli as stcli


def resolve(p):
    base = getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, p)


if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        app_dir = sys._MEIPASS
        exe_dir = os.path.dirname(sys.executable)
    else:
        app_dir = exe_dir = os.path.dirname(os.path.abspath(__file__))

    db_externo = os.path.join(exe_dir, "extratos.db")
    os.environ["EXTRATOS_DB"] = (db_externo if os.path.exists(db_externo)
                               else resolve("extratos.db"))
    os.chdir(app_dir)
    sys.argv = [
        "streamlit", "run", resolve("dashboard.py"),
        "--server.headless", "true",
        "--global.developmentMode", "false",
        "--server.port", "8501",
    ]
    sys.exit(stcli.main())
