# -*- coding: utf-8 -*-
"""
Relatório de envios para os alvos principais (Vanessa, Ivanilda, Sérgio)
e classificação de todas as outras saídas.

Contas da EMPRESA: Asaas, Santander, Itaú, Inter
Conta dos SÓCIOS:  Banco do Brasil

Uso:
    python alvos.py            -> resumo no console + gera CSVs
    python alvos.py vanessa    -> detalhe de um alvo no console
CSVs gerados em ./relatorios/:
    envios_vanessa.csv, envios_ivanilda.csv, envios_sergio.csv
    saidas_classificadas.csv      (todas as saídas com a coluna 'alvo')
    outros_destinatarios.csv      (saídas que NÃO são dos alvos, agrupadas)
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

# padrões de identificação de cada alvo (nome normalizado)
ALVOS = {
    "ricardo": [
        r"RICARDO BRUNO",                      # R. Bruno Boueres Costa / B Costa
        r"082\.353",                            # CPF ***.082.353-**
    ],
    "vanessa": [
        r"VANESSA CAVALCANTE",                 # todas as grafias dela
        r"\b754.?563\b",                        # CPF ***.754.563-** / 055.754.563-31
        r"VANESSA COSTA$",                      # '98383248 VANESSA COSTA' (Inter)
    ],
    "ivanilda": [r"IVANILDA"],
    "sergio": [
        r"RAIMUNDO SERGIO",
        r"RAIMUNDO S\.? ?CARVALHO",
        r"SERGIO CARVALHO",
    ],
}

INTERNAS = [
    r"V C C B", r"SORRIRDAY", r"RB ODONTO", r"ODONTOCOMPANY", r"ORAL SIN",
    r"LUMINE SORRISO", r"LUMIER", r"ODONTO RISOS", r"I OFTALMO",
    r"BOUERES SOLAR", r"56\.987\.020",
]


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9\. ]", " ", s).upper()
    return re.sub(r"\s+", " ", s).strip()


def money(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def classificar(contraparte, doc, desc, det):
    """retorna (alvo, interna_bool)"""
    txt = norm(f"{contraparte} {doc} {desc} {det}")
    for alvo, pats in ALVOS.items():
        for p in pats:
            if re.search(p, txt):
                return alvo, False
    for p in INTERNAS:
        if re.search(p, txt):
            return "", True
    return "", False


def carregar_saidas(con):
    q = """
    SELECT t.id, t.data, c.banco, c.natureza, c.titular, c.agencia, c.conta,
           t.descricao, t.detalhe, t.documento,
           t.contraparte, t.contraparte_doc, t.valor, t.arquivo
    FROM transacoes t JOIN contas c ON c.id=t.conta_id
    WHERE t.tipo='saida' ORDER BY t.data, t.id
    """
    rows = []
    for r in con.execute(q):
        alvo, interna = classificar(r[10], r[11], r[7], r[8])
        rows.append(dict(zip(
            ["id", "data", "banco", "natureza", "titular", "ag", "conta",
             "descricao", "detalhe", "documento", "contraparte",
             "contraparte_doc", "valor", "arquivo"], r),
            alvo=alvo, interna=interna))
    return rows


def resumo_alvo(rows, alvo):
    sel = [r for r in rows if r["alvo"] == alvo]
    total = sum(r["valor"] for r in sel)
    print(f"\n{'='*78}\n{alvo.upper()}  —  {len(sel)} envios   "
          f"TOTAL: R$ {money(total)}\n{'='*78}")
    # por banco de origem
    por_banco = {}
    por_mes = {}
    for r in sel:
        por_banco.setdefault(r["banco"], [0, 0.0])
        por_banco[r["banco"]][0] += 1
        por_banco[r["banco"]][1] += r["valor"]
        mes = r["data"][:7]
        por_mes.setdefault(mes, [0, 0.0])
        por_mes[mes][0] += 1
        por_mes[mes][1] += r["valor"]
    print("\n por banco origem:")
    for b, (n, v) in sorted(por_banco.items(), key=lambda x: -x[1][1]):
        print(f"   {b:16s} {n:4d} envios   R$ {money(v)}")
    print("\n por mês:")
    for mes in sorted(por_mes):
        n, v = por_mes[mes]
        print(f"   {mes}  {n:4d} envios   R$ {money(v)}")
    print("\n maiores envios:")
    for r in sorted(sel, key=lambda x: -x["valor"])[:15]:
        print(f"   {r['data']} {r['banco']:10s} "
              f"{(r['contraparte'] or r['descricao'])[:40]:40s} "
              f"R$ {money(r['valor'])}")
    return sel


def detalhe_alvo(rows, alvo):
    sel = [r for r in rows if r["alvo"] == alvo]
    print(f"{alvo.upper()} — {len(sel)} envios, "
          f"total R$ {money(sum(r['valor'] for r in sel))}\n")
    for r in sorted(sel, key=lambda x: -x["valor"]):
        print(f"{r['data']} {r['banco']:10s} {r['titular'][:20]:20s} | "
              f"{r['descricao'][:35]:35s} | "
              f"{(r['contraparte'] or '')[:40]:40s} R$ {money(r['valor'])}")


def main():
    con = sqlite3.connect(DB)
    rows = carregar_saidas(con)
    os.makedirs(REL, exist_ok=True)

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd in ALVOS:
        detalhe_alvo(rows, cmd)
        return

    total_saidas = sum(r["valor"] for r in rows)
    print(f"Total de saídas: {len(rows)}   R$ {money(total_saidas)}")
    print("Origem: contas EMPRESA (asaas/santander/itau/inter) "
          "e SÓCIOS (banco do brasil)")

    for alvo in ALVOS:
        sel = resumo_alvo(rows, alvo)
        with open(os.path.join(REL, f"envios_{alvo}.csv"), "w",
                  newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["data", "banco", "natureza", "titular_origem",
                        "ag", "conta", "descricao", "contraparte",
                        "contraparte_doc", "valor", "arquivo"])
            for r in sel:
                w.writerow([r["data"], r["banco"], r["natureza"], r["titular"],
                            r["ag"], r["conta"], r["descricao"],
                            r["contraparte"], r["contraparte_doc"],
                            f"{r['valor']:.2f}", r["arquivo"]])

    # ---------- todas as saídas classificadas ----------
    with open(os.path.join(REL, "saidas_classificadas.csv"), "w",
              newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["alvo", "interna", "data", "banco", "natureza",
                    "titular_origem", "descricao", "detalhe",
                    "contraparte", "contraparte_doc", "valor", "arquivo"])
        for r in rows:
            w.writerow([r["alvo"], "sim" if r["interna"] else "",
                        r["data"], r["banco"], r["natureza"], r["titular"],
                        r["descricao"], r["detalhe"], r["contraparte"],
                        r["contraparte_doc"], f"{r['valor']:.2f}", r["arquivo"]])

    # ---------- outras saídas agrupadas ----------
    outros = {}
    for r in rows:
        if r["alvo"] or r["interna"]:
            continue
        k = norm(r["contraparte"] or r["descricao"]) or "(sem identificação)"
        a = outros.setdefault(k, {"nome": r["contraparte"] or r["descricao"],
                                  "doc": r["contraparte_doc"],
                                  "tot": 0.0, "qtd": 0,
                                  "bancos": set(), "nat": set()})
        a["tot"] += r["valor"]
        a["qtd"] += 1
        a["bancos"].add(r["banco"])
        a["nat"].add(r["natureza"])
    lista = sorted(outros.values(), key=lambda x: -x["tot"])
    with open(os.path.join(REL, "outros_destinatarios.csv"), "w",
              newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["destinatario", "doc", "qtd", "total", "bancos", "natureza_origem"])
        for a in lista:
            w.writerow([a["nome"], a["doc"], a["qtd"], f"{a['tot']:.2f}",
                        ",".join(sorted(a["bancos"])),
                        ",".join(sorted(a["nat"]))])

    print(f"\n{'='*78}\nOUTROS DESTINATÁRIOS (fora dos 3 alvos e fora do "
          f"grupo) — top 30\n{'='*78}")
    print(f"{'total R$':>14s} {'qtd':>5s}  destinatário")
    for a in lista[:30]:
        print(f"{money(a['tot']):>14s} {a['qtd']:5d}  {a['nome'][:60]}")

    n_int = sum(1 for r in rows if r["interna"])
    v_int = sum(r["valor"] for r in rows if r["interna"])
    print(f"\nTransferências internas do grupo: {n_int}  R$ {money(v_int)}")
    print(f"CSVs salvos em: {REL}")


if __name__ == "__main__":
    main()
