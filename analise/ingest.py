# -*- coding: utf-8 -*-
"""
Ingestão de extratos bancários (Itaú, Banco do Brasil, Inter, Santander, Asaas)
para um banco SQLite local (extratos.db).

Uso:
    python ingest.py [pasta_dos_extratos]

Idempotente: transações têm hash único (INSERT OR IGNORE); pode rodar de novo.
"""
import glob
import hashlib
import os
import re
import sqlite3
import sys

import openpyxl
import pdfplumber

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "extratos.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS contas (
    id INTEGER PRIMARY KEY,
    banco TEXT NOT NULL,
    titular TEXT DEFAULT '',
    agencia TEXT DEFAULT '',
    conta TEXT DEFAULT '',
    documento TEXT DEFAULT '',
    natureza TEXT DEFAULT '',           -- 'empresa' ou 'socio'
    UNIQUE(banco, agencia, conta)
);

CREATE TABLE IF NOT EXISTS transacoes (
    id INTEGER PRIMARY KEY,
    conta_id INTEGER NOT NULL REFERENCES contas(id),
    data TEXT NOT NULL,               -- yyyy-mm-dd
    descricao TEXT NOT NULL,          -- lançamento / histórico
    detalhe TEXT DEFAULT '',          -- info extra (linha auxiliar, banco destino, etc.)
    tipo TEXT NOT NULL CHECK(tipo IN ('entrada','saida')),
    valor REAL NOT NULL,              -- sempre positivo
    saldo REAL,
    documento TEXT DEFAULT '',
    contraparte TEXT DEFAULT '',      -- nome do favorecido/pagador
    contraparte_doc TEXT DEFAULT '',  -- CPF/CNPJ da contraparte
    arquivo TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_tr_data ON transacoes(data);
CREATE INDEX IF NOT EXISTS ix_tr_tipo ON transacoes(tipo);
CREATE INDEX IF NOT EXISTS ix_tr_contraparte ON transacoes(contraparte);
CREATE INDEX IF NOT EXISTS ix_tr_conta ON transacoes(conta_id);
"""

MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}

RE_DATA = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
RE_VALOR = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2}")


def parse_money(s):
    """'1.234,56' / '-R$ 1.234,56' -> float"""
    if s is None:
        return None
    s = str(s).strip().replace("R$", "").replace(" ", "")
    if not s:
        return None
    neg = s.startswith("-")
    s = s.lstrip("+-").replace(".", "").replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def iso(ddmmaaaa):
    m = RE_DATA.match(str(ddmmaaaa).strip())
    if not m:
        return None
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"


def limpar(s):
    return re.sub(r"\s+", " ", str(s or "").replace("\xa0", " ")).strip()


# ----------------------------------------------------------------------------- conta
class DB:
    def __init__(self, path):
        self.con = sqlite3.connect(path)
        self.con.executescript(SCHEMA)
        cols = [r[1] for r in self.con.execute("PRAGMA table_info(contas)")]
        if "natureza" not in cols:
            self.con.execute("ALTER TABLE contas ADD COLUMN natureza DEFAULT ''")
        for banco, nat in (("itau", "empresa"), ("inter", "empresa"),
                           ("santander", "empresa"), ("asaas", "empresa"),
                           ("banco_do_brasil", "socio")):
            self.con.execute("UPDATE contas SET natureza=? WHERE banco=?",
                             (nat, banco))
        self._conta_ids = {}
        self._seen = {}

    def conta_id(self, banco, titular="", agencia="", conta="", documento=""):
        key = (banco, agencia or "", conta or "")
        if key not in self._conta_ids:
            cur = self.con.execute(
                "INSERT OR IGNORE INTO contas(banco,titular,agencia,conta,documento)"
                " VALUES(?,?,?,?,?)",
                (banco, titular, agencia or "", conta or "", documento),
            )
            row = self.con.execute(
                "SELECT id FROM contas WHERE banco=? AND agencia=? AND conta=?",
                (banco, agencia or "", conta or ""),
            ).fetchone()
            self._conta_ids[key] = row[0]
        return self._conta_ids[key]

    def add(self, conta_id, data, descricao, tipo, valor, saldo=None,
            documento="", detalhe="", contraparte="", contraparte_doc="",
            arquivo="", ocorrencia=None):
        base = (conta_id, data, limpar(descricao), limpar(detalhe), tipo,
                round(valor, 2), saldo, limpar(documento),
                limpar(contraparte), limpar(contraparte_doc))
        # ocorrência n-ésima de conteúdo idêntico dentro do mesmo arquivo
        key = (arquivo,) + base
        if ocorrencia is None:
            self._seen[key] = self._seen.get(key, 0) + 1
            ocorrencia = self._seen[key]
        h = hashlib.sha1(
            ("|".join(map(str, base)) + f"|{ocorrencia}").encode("utf-8")
        ).hexdigest()
        cur = self.con.execute(
            "INSERT OR IGNORE INTO transacoes"
            "(conta_id,data,descricao,detalhe,tipo,valor,saldo,documento,"
            " contraparte,contraparte_doc,arquivo,hash)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (conta_id, data, limpar(descricao), limpar(detalhe), tipo, valor,
             saldo, limpar(documento), limpar(contraparte),
             limpar(contraparte_doc), arquivo, h),
        )
        return cur.rowcount

    def commit(self):
        self.con.commit()


# ----------------------------------------------------------------------------- Itaú xlsx
SALDO_ITAU = re.compile(r"saldo", re.I)


def parse_itau(path, db):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    titular = ag = cc = ""
    hdr = None
    for i, r in enumerate(rows[:12]):
        c0 = limpar(r[0]).lower() if r[0] else ""
        if c0.startswith("nome"):
            titular = limpar(r[1])
        elif c0.startswith("ag"):
            ag = limpar(r[1])
        elif c0.startswith("conta"):
            cc = limpar(r[1])
        elif c0 == "data":
            hdr = i
    if hdr is None:
        print("  ! cabeçalho não achado:", path)
        return 0
    cid = db.conta_id("itau", titular=titular, agencia=ag, conta=cc)
    n = 0
    for r in rows[hdr + 1:]:
        if not r or not r[0]:
            continue
        data = iso(r[0])
        if not data:
            continue
        lanc = limpar(r[1])
        if SALDO_ITAU.search(lanc):          # SALDO TOTAL/EM CONTA/ANTERIOR
            continue
        valor = r[4] if len(r) > 4 else None
        try:
            valor = float(valor)
        except (TypeError, ValueError):
            continue
        saldo = r[5] if len(r) > 5 else None
        try:
            saldo = float(saldo) if saldo is not None else None
        except (TypeError, ValueError):
            saldo = None
        n += db.add(cid, data, lanc,
                    "saida" if valor < 0 else "entrada", abs(valor), saldo,
                    contraparte=r[2] or "", contraparte_doc=r[3] or "",
                    arquivo=os.path.basename(path))
    return n


# ----------------------------------------------------------------------- BB xlsx mensal
SALDO_BB = {"saldo anterior", "s a l d o", "saldo do dia", "movimento do dia"}


def parse_bb(path, db):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    cid = db.conta_id("banco_do_brasil", titular="conta mensal BB",
                      agencia="", conta="mensal")
    n = 0
    started = False
    for r in ws.iter_rows(values_only=True):
        if not started:
            if r and limpar(r[0]).lower() == "data":
                started = True
            continue
        if not r or not r[0]:
            continue
        data = iso(r[0])
        if not data:
            continue
        lanc = limpar(r[1])
        if lanc.lower() in SALDO_BB:
            continue
        detalhe = limpar(r[2] if len(r) > 2 else "")
        doc = limpar(r[3] if len(r) > 3 else "")
        valor = parse_money(r[4] if len(r) > 4 else None)
        if valor is None:
            continue
        tipo_lanc = limpar(r[5] if len(r) > 5 else "").lower()
        if tipo_lanc.startswith("sa"):
            tipo = "saida"
        elif tipo_lanc.startswith("ent"):
            tipo = "entrada"
        else:
            tipo = "saida" if valor < 0 else "entrada"
        # detalhe '02/01 15:52 NOME' -> contraparte
        cp = re.sub(r"^\d{2}/\d{2}\s+\d{2}:\d{2}\s+", "", detalhe)
        cp = re.sub(r"^\d{2}/\d{2}\s+", "", cp)
        n += db.add(cid, data, lanc, tipo, abs(valor), None,
                    documento=doc, detalhe=detalhe, contraparte=cp,
                    arquivo=os.path.basename(path))
    return n


# ----------------------------------------------------------------------------- Inter pdf
RE_DIA_INTER = re.compile(
    r"^(\d{1,2}) de ([A-Za-zç]+) de (\d{4})\b", re.I)
RE_TX_INTER = re.compile(
    r"^(?P<desc>.+?)\s+(?P<valor>-?R\$\s*[\d\.]+,\d{2})\s+"
    r"(?P<saldo>-?R\$\s*[\d\.]+,\d{2})\s*$")
RE_INTER_CAB = re.compile(
    r"CPF/CNPJ:\s*(\S+),\s*Instituição:\s*(.*?),\s*Agência:\s*(.*?),"
    r"\s*Conta:\s*(\S+)", re.I)


def inter_contraparte(desc):
    m = re.search(r'"(?P<q>.*?)"', desc)
    if not m:
        return "", ""
    info = m.group("q").strip()
    doc = ""
    # 'Cp :18236120-Vanessa...' -> doc parcial + nome
    m2 = re.match(r"^(?:Cp\s*:\s*)?(\d{5,})[-\s](.+)$", info, re.I)
    if m2:
        doc = m2.group(1)
        nome = m2.group(2).strip()
    else:
        m3 = re.match(r"^Cp\s*:\s*(.+)$", info, re.I)
        nome = (m3.group(1) if m3 else info).strip()
    return nome, doc


def parse_inter(path, db):
    cid = None
    titular = doc_c = ag = cc = ""
    data_atual = None
    n = 0
    nao_lanc = ("fale com a gente", "sac:", "ouvidoria", "solicitado em",
                "período:", "saldo total", "(bloqueado")
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for linha in (page.extract_text() or "").split("\n"):
                l = limpar(linha)
                if not l:
                    continue
                low = l.lower()
                mcab = RE_INTER_CAB.search(l)
                if mcab:
                    doc_c, _, ag, cc = mcab.groups()
                    if cid is None:
                        cid = db.conta_id("inter", titular=titular,
                                          agencia=ag, conta=cc,
                                          documento=doc_c)
                    continue
                if "cpf/cnpj" in low or any(k in low for k in nao_lanc):
                    continue
                md = RE_DIA_INTER.match(l)
                if md:
                    dia, mes, ano = md.groups()
                    data_atual = f"{ano}-{MESES[mes.lower()]:02d}-{int(dia):02d}"
                    continue
                mt = RE_TX_INTER.match(l)
                if mt and data_atual:
                    desc = mt.group("desc").strip()
                    valor = parse_money(mt.group("valor"))
                    saldo = parse_money(mt.group("saldo"))
                    cp, cpdoc = inter_contraparte(desc)
                    n += db.add(cid, data_atual, desc,
                                "saida" if valor < 0 else "entrada",
                                abs(valor), saldo,
                                contraparte=cp, contraparte_doc=cpdoc,
                                arquivo=os.path.basename(path))
                    continue
                # linha de titular (antes do CPF/CNPJ)
                if not titular and not cc and not re.search(
                        r"\d{2}/\d{2}/\d{4}|R\$", l):
                    titular = l
    if cid is None:
        print("  ! conta Inter não identificada:", path)
    return n


# ------------------------------------------------------------------------ Santander pdf
RE_TX_SANT = re.compile(
    r"^(?P<data>\d{2}/\d{2}/\d{4})\s+(?P<meio>.*?)\s*"
    r"(?P<valor>-?[\d\.]+,\d{2})\s+(?P<saldo>-?[\d\.]+,\d{2})\s*$")
RE_SANT_CAB = re.compile(
    r"Ag.ncia:\s*(\S+)\s+Conta:\s*(\S+)", re.I)
KW_INICIO_SANT = re.compile(
    r"^(pix|pagamento|pag\b|tarifa|tar\b|transfer|ted|doc\b|dep|deposito|"
    r"boleto|resgate|aplic|aplicacao|cobran|saque|compra|credito|crédito|"
    r"debito|débito|iof|rendimento|estorno|devol|manut|cheque|sispag|"
    r"emissao|emissão|receb|liquido|líquido|valor|saldo|custodia|"
    r"prev|darf|gps|fgts|titulo|título|deb\b|juros|multa|encargos|"
    r"atualiza|remunera|dinheiro|custódia)", re.I)
SANT_IGNORA = re.compile(
    r"(santander empresas|per[íi]odos:|saldo dispon[íi]vel|"
    r"data hist.rico|^\d+/\d+$|4004-|0800|ouvidoria|defici.ncia|"
    r"ag.ncia:.*conta:|sac:|fale com|www\.|central|atendimento|"
    r"data/hora)", re.I)


def parse_santander(path, db):
    cid = None
    titular = ""
    n = 0
    pending = []          # histórico(s) que antecedem a próxima linha-data
    last_tx = None        # dict da última transação inserida
    txs = []              # acumula para poder anexar pós-linha
    with pdfplumber.open(path) as pdf:
        linhas = []
        for page in pdf.pages:
            linhas.extend((page.extract_text() or "").split("\n"))

    for linha in linhas:
        l = limpar(linha)
        if not l:
            continue
        if cid is None:
            mcab = RE_SANT_CAB.search(l)
            if mcab:
                ag, cc = mcab.groups()
                titular = l[:mcab.start()].strip()
                cid = db.conta_id("santander", titular=titular,
                                  agencia=ag, conta=cc)
                continue
        if SANT_IGNORA.search(l):
            continue
        mt = RE_TX_SANT.match(l)
        if mt:
            data = iso(mt.group("data"))
            meio = mt.group("meio").strip()
            valor = parse_money(mt.group("valor"))
            saldo = parse_money(mt.group("saldo"))
            # separa histórico de nº documento (token numérico final)
            doc = ""
            mdoc = re.search(r"\s(\d{5,}|\d{11,14}|0{5,})$", meio)
            if mdoc:
                doc = mdoc.group(1)
                meio = meio[:mdoc.start()].strip()
            elif re.fullmatch(r"\d{5,}|0{5,}", meio):
                doc, meio = meio, ""
            hist = " ".join(pending + ([meio] if meio else []))
            pending = []
            tx = {"data": data, "desc": hist, "doc": doc,
                  "valor": valor, "saldo": saldo, "det": ""}
            txs.append(tx)
            last_tx = tx
            continue
        # linha sem data: início de novo histórico ou complemento da anterior
        if KW_INICIO_SANT.match(l) or pending or last_tx is None:
            pending.append(l)
        elif last_tx is not None:
            last_tx["det"] = (last_tx["det"] + " " + l).strip()

    if cid is None:
        cid = db.conta_id("santander", agencia="", conta="")
    for tx in txs:
        desc = tx["desc"]
        cp, cpdoc = "", ""
        m = re.match(r"(?i)pix enviado\s+(.+)$", desc)
        if m:
            cp = m.group(1).strip()
            if tx["det"]:
                cp += " " + tx["det"]
        elif re.match(r"(?i)pix recebido", desc):
            cpdoc = tx["doc"]
        n += db.add(cid, tx["data"], desc,
                    "saida" if tx["valor"] < 0 else "entrada",
                    abs(tx["valor"]), tx["saldo"],
                    documento=tx["doc"], detalhe=tx["det"],
                    contraparte=cp, contraparte_doc=cpdoc,
                    arquivo=os.path.basename(path))
    return n


# ---------------------------------------------------------------------------- Asaas pdf
# colunas por posição x (pt)
ASAAS_COLS = [
    ("sol", 0, 140), ("data", 140, 200), ("nome", 200, 420),
    ("doc", 420, 485), ("banco", 485, 680), ("valor", 680, 745),
    ("op", 745, 9999),
]


def parse_asaas(path, db):
    cid = db.conta_id("asaas", titular="conta asaas", agencia="", conta="asaas")
    n = 0
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            linhas = {}
            for w in words:
                linhas.setdefault(round(w["top"]), []).append(w)
            for top in sorted(linhas):
                ws_ = sorted(linhas[top], key=lambda w: w["x0"])
                cols = {c[0]: [] for c in ASAAS_COLS}
                for w in ws_:
                    for nome, x0, x1 in ASAAS_COLS:
                        if x0 <= w["x0"] < x1:
                            cols[nome].append(w["text"])
                            break
                data = iso(cols["data"][0]) if cols["data"] else None
                if not data or not RE_DATA.match(cols["sol"][0] if cols["sol"] else ""):
                    continue
                nome = limpar(" ".join(cols["nome"]))
                doc = limpar(" ".join(cols["doc"]))
                banco = limpar(" ".join(cols["banco"]))
                tail = " ".join(cols["valor"] + cols["op"])
                mv = RE_VALOR.search(tail)
                if not mv:
                    continue
                valor = parse_money(mv.group(0))
                sit = re.search(
                    r"(Efetuada|Agendada|Cancelada|Falhada|Recusada|Estornada)",
                    tail, re.I)
                op = "Pix" if re.search(r"pix", tail, re.I) else ""
                n += db.add(cid, data, "transferencia " + (op or ""),
                            "saida", valor, None,
                            documento="",
                            detalhe=f"sit:{sit.group(1) if sit else ''} "
                                    f"banco_destino:{banco}",
                            contraparte=nome, contraparte_doc=doc,
                            arquivo=os.path.basename(path))
    return n


# -------------------------------------------------------------------------------- main
def main():
    pasta = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(BASE_DIR)
    filtro = sys.argv[2] if len(sys.argv) > 2 else None
    db = DB(DB_PATH)
    if filtro:
        db.con.execute(
            "DELETE FROM transacoes WHERE conta_id IN"
            " (SELECT id FROM contas WHERE banco=?)", (filtro,))
        db.con.execute("DELETE FROM contas WHERE banco=?", (filtro,))
        db.con.commit()
    tot = {}
    jobs = []
    for f in sorted(glob.glob(os.path.join(pasta, "*.xlsx"))):
        b = os.path.basename(f)
        if b.startswith("Entradas_Saidas"):
            jobs.append(("itau", f, parse_itau))
        elif b.startswith("Extrato conta corrente"):
            jobs.append(("bb", f, parse_bb))
    for f in sorted(glob.glob(os.path.join(pasta, "*.pdf"))):
        b = os.path.basename(f).upper()
        if "INTER" in b:
            jobs.append(("inter", f, parse_inter))
        elif "SANTANDER" in b:
            jobs.append(("santander", f, parse_santander))
        elif "ASSAS" in b or "ASAAS" in b:
            jobs.append(("asaas", f, parse_asaas))

    for banco, f, fn in jobs:
        if filtro and banco != filtro:
            continue
        n = fn(f, db)
        tot[banco] = tot.get(banco, 0) + n
        print(f"{banco:10s} {n:6d}  <- {os.path.basename(f)}")
    db.commit()
    print("\nInseridos (novos):", tot)
    for banco, qtd, ent, sai in db.con.execute(
        "SELECT c.banco, COUNT(*),"
        " SUM(CASE WHEN t.tipo='entrada' THEN 1 ELSE 0 END),"
        " SUM(CASE WHEN t.tipo='saida' THEN 1 ELSE 0 END)"
        " FROM transacoes t JOIN contas c ON c.id=t.conta_id"
        " GROUP BY c.banco"):
        print(f"  {banco:16s} transações={qtd:6d} entradas={ent} saídas={sai}")


if __name__ == "__main__":
    main()
