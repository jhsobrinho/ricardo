# -*- coding: utf-8 -*-
"""
Análise dos extratos importados em extratos.db.

Uso:
    python analise.py                -> resumo geral + top destinos de saída
    python analise.py resumo         -> totais por banco/conta
    python analise.py destinos [N]   -> top N destinatários de saídas
    python analise.py origens [N]    -> top N origens de entradas
    python analise.py saidas NOME    -> saídas filtrando contraparte (LIKE)
    python analise.py internas       -> transferências entre contas do grupo
    python analise.py csv            -> exporta CSVs para pasta ./relatorios
"""
import csv
import os
import re
import sqlite3
import sys
import unicodedata

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "extratos.db")
REL = os.path.join(BASE, "relatorios")


def norm(s):
    """normaliza nome p/ agrupar variantes"""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9 ]", " ", s).upper()
    s = re.sub(r"\s+", " ", s).strip()
    # remove sufixos societários p/ agrupar 'X LTDA', 'X LTDA EPP', 'X'
    s = re.sub(r"( LTDA| EPP| EPPI| ME| MEI| SA| S A| EIRELI| SPE)+$", "", s)
    return s


def contraparte_efetiva(row):
    """melhor nome disponível da contraparte"""
    desc, det, cp = row
    if cp and cp.strip():
        return cp.strip()
    return ""


def money(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def resumo(con):
    q = """
    SELECT c.banco, c.titular, c.agencia, c.conta,
           MIN(t.data), MAX(t.data), COUNT(*),
           SUM(CASE WHEN t.tipo='entrada' THEN t.valor ELSE 0 END),
           SUM(CASE WHEN t.tipo='saida' THEN t.valor ELSE 0 END)
    FROM transacoes t JOIN contas c ON c.id=t.conta_id
    GROUP BY c.id ORDER BY c.banco, c.conta
    """
    print(f"{'banco':15s} {'titular':28s} {'ag':6s} {'conta':11s} "
          f"{'período':23s} {'#tx':>5s} {'entradas':>15s} {'saídas':>15s}")
    for b, t, ag, cc, d0, d1, n, ent, sai in con.execute(q):
        print(f"{b:15s} {t[:28]:28s} {ag:6s} {cc:11s} "
              f"{d0}->{d1} {n:5d} {money(ent or 0):>15s} {money(sai or 0):>15s}")
    ent, sai = con.execute(
        "SELECT SUM(CASE WHEN tipo='entrada' THEN valor ELSE 0 END),"
        " SUM(CASE WHEN tipo='saida' THEN valor ELSE 0 END) FROM transacoes"
    ).fetchone()
    print(f"\nTOTAL GERAL  entradas: R$ {money(ent or 0)}   "
          f"saídas: R$ {money(sai or 0)}")


def destinos(con, n=40, onde=""):
    """saídas agrupadas por contraparte"""
    q = """
    SELECT t.contraparte, t.contraparte_doc, t.detalhe, t.descricao,
           t.valor, c.banco, c.titular, t.data
    FROM transacoes t JOIN contas c ON c.id=t.conta_id
    WHERE t.tipo='saida'
    """
    agg = {}
    for cp, cpdoc, det, desc, valor, banco, tit, data in con.execute(q):
        nome = cp.strip()
        if not nome:
            nome = _nome_fallback(desc, det)
        k = (norm(nome), cpdoc.replace("*", "") if cpdoc else "")
        a = agg.setdefault(k, {"nome": nome or "(sem identificação)",
                               "doc": cpdoc, "tot": 0.0, "qtd": 0,
                               "bancos": set(), "desc": set()})
        a["tot"] += valor
        a["qtd"] += 1
        a["bancos"].add(banco)
        a["desc"].add(desc[:40])
        if cpdoc and not a["doc"]:
            a["doc"] = cpdoc
    rows = sorted(agg.values(), key=lambda x: -x["tot"])
    print(f"{'total R$':>15s} {'qtd':>4s}  {'destinatário':42s} {'doc':20s} "
          f"{'bancos origem'}")
    for a in rows[:n]:
        print(f"{money(a['tot']):>15s} {a['qtd']:4d}  {a['nome'][:42]:42s} "
              f"{(a['doc'] or '')[:20]:20s} {','.join(sorted(a['bancos']))}")
    return rows


def _nome_fallback(desc, det):
    """extrai nome quando contraparte não foi preenchido"""
    d = f"{desc} {det}".strip()
    d = re.sub(r"(?i)pix enviado\s*", "", d)
    d = re.sub(r"(?i)transferencia enviada\s*", "", d)
    d = re.sub(r"^\d{2}/\d{2}\s+\d{2}:\d{2}\s*", "", d)
    return d.strip()


def origens(con, n=30):
    q = """
    SELECT t.contraparte, t.contraparte_doc, t.descricao, t.detalhe, t.valor
    FROM transacoes t WHERE t.tipo='entrada'
    """
    agg = {}
    for cp, cpdoc, desc, det, valor in con.execute(q):
        nome = cp.strip() or _nome_fallback(desc, det)
        k = norm(nome)
        a = agg.setdefault(k, {"nome": nome or "(sem identificação)",
                               "tot": 0.0, "qtd": 0})
        a["tot"] += valor
        a["qtd"] += 1
    rows = sorted(agg.values(), key=lambda x: -x["tot"])
    print(f"{'total R$':>15s} {'qtd':>4s}  origem")
    for a in rows[:n]:
        print(f"{money(a['tot']):>15s} {a['qtd']:4d}  {a['nome'][:60]}")
    return rows


def internas(con):
    """detecta transferências entre contas do próprio grupo"""
    titulares = set()
    for (t,) in con.execute("SELECT DISTINCT titular FROM contas"):
        for tok in norm(t).split():
            if len(tok) >= 4:
                titulares.add(tok)
    # nomes que aparecem nos dados (empresas do grupo)
    alvo = {"V C C B COSTA", "SORRIRDAY", "RB ODONTO", "ORAL SIN", "LUMINE",
            "ODONTOCOMPANY", "ODONTO RISOS", "LUMIER", "BOUERES", "RICARDO BRUNO"}
    print("Saídas cujo destinatário parece ser do próprio grupo:\n")
    q = """
    SELECT t.data, c.banco, c.titular, t.contraparte, t.valor, t.descricao
    FROM transacoes t JOIN contas c ON c.id=t.conta_id
    WHERE t.tipo='saida' ORDER BY t.valor DESC
    """
    tot = 0.0
    for data, banco, tit, cp, valor, desc in con.execute(q):
        alvo_txt = norm(f"{cp} {desc}")
        if any(a in alvo_txt for a in alvo):
            print(f"{data} {banco:10s} {tit[:20]:20s} -> "
                  f"{(cp or desc)[:45]:45s} R$ {money(valor)}")
            tot += valor
    print(f"\nTotal movimentado entre contas do grupo: R$ {money(tot)}")


def export_csv(con):
    os.makedirs(REL, exist_ok=True)
    arq = os.path.join(REL, "transacoes.csv")
    with open(arq, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["banco", "titular", "agencia", "conta", "data",
                    "tipo", "descricao", "detalhe", "valor", "saldo",
                    "documento", "contraparte", "contraparte_doc", "arquivo"])
        for r in con.execute(
            "SELECT c.banco,c.titular,c.agencia,c.conta,t.data,t.tipo,"
            "t.descricao,t.detalhe,t.valor,t.saldo,t.documento,"
            "t.contraparte,t.contraparte_doc,t.arquivo "
            "FROM transacoes t JOIN contas c ON c.id=t.conta_id "
            "ORDER BY t.data"):
            w.writerow(r)
    print("exportado:", arq)


def busca_saida(con, termo):
    like = f"%{termo}%"
    q = """
    SELECT t.data, c.banco, c.titular, t.descricao, t.contraparte,
           t.contraparte_doc, t.valor
    FROM transacoes t JOIN contas c ON c.id=t.conta_id
    WHERE t.tipo='saida' AND (t.contraparte LIKE ? OR t.descricao LIKE ?
                              OR t.detalhe LIKE ?)
    ORDER BY t.valor DESC LIMIT 100
    """
    tot = 0.0
    n = 0
    for data, banco, tit, desc, cp, cpdoc, valor in con.execute(
            q, (like, like, like)):
        print(f"{data} {banco:10s} {tit[:18]:18s} | {desc[:38]:38s} | "
              f"{(cp or '')[:30]:30s} {cpdoc:20s} R$ {money(valor)}")
        tot += valor
        n += 1
    print(f"\n{n} lançamentos (até 100)  total: R$ {money(tot)}")


def main():
    con = sqlite3.connect(DB)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "resumo" or not cmd:
        resumo(con)
        if not cmd:
            print("\n=== TOP DESTINOS DE SAÍDA ===\n")
            destinos(con, 25)
    elif cmd == "destinos":
        destinos(con, int(sys.argv[2]) if len(sys.argv) > 2 else 40)
    elif cmd == "origens":
        origens(con, int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    elif cmd == "internas":
        internas(con)
    elif cmd == "saidas" and len(sys.argv) > 2:
        busca_saida(con, sys.argv[2])
    elif cmd == "csv":
        export_csv(con)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
