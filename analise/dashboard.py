# -*- coding: utf-8 -*-
"""Dashboard de análise de extratos — rodar:  streamlit run dashboard.py"""
import os
import sqlite3
import sys

import pandas as pd
import streamlit as st

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from alvos import ALVOS, classificar, norm

DB = os.environ.get("EXTRATOS_DB", os.path.join(BASE, "extratos.db"))

COR_ALVO = {"ricardo": "#34d399", "vanessa": "#f472b6",
            "ivanilda": "#facc15", "sergio": "#38bdf8"}
NOME_BANCO = {"itau": "Itaú", "banco_do_brasil": "Banco do Brasil",
              "inter": "Inter", "santander": "Santander", "asaas": "Asaas"}

st.set_page_config(page_title="Análise de Extratos", layout="wide",
                   page_icon=":material/account_balance:")


def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@st.cache_data
def load_data():
    con = sqlite3.connect(DB)
    df = pd.read_sql(
        """SELECT t.id, t.data, c.banco, c.natureza, c.titular, c.agencia,
                  c.conta, t.descricao, t.detalhe, t.documento,
                  t.contraparte, t.contraparte_doc, t.tipo, t.valor,
                  t.saldo, t.arquivo
           FROM transacoes t JOIN contas c ON c.id=t.conta_id""", con)
    con.close()
    df["dt"] = pd.to_datetime(df["data"])
    df["mes"] = df["dt"].dt.to_period("M").astype(str)
    cl = df.apply(lambda r: classificar(r["contraparte"], r["contraparte_doc"],
                                        r["descricao"], r["detalhe"]), axis=1)
    df["alvo"] = [c[0] for c in cl]
    df["interna"] = [c[1] for c in cl]
    df["destino"] = df.apply(
        lambda r: r["contraparte"] or r["descricao"], axis=1)
    df["banco_nome"] = df["banco"].map(NOME_BANCO).fillna(df["banco"])
    return df


df_all = load_data()

NOME_MES = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
            7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}

# ------------------------------------------------------------- sidebar filtros
with st.sidebar:
    st.markdown("## :material/filter_alt: Filtros")
    anos = sorted(df_all["dt"].dt.year.unique())
    ano_sel = st.segmented_control("Ano", ["Todos"] + anos, default="Todos")
    meses_sel = []
    if ano_sel != "Todos":
        meses_ano = sorted(
            df_all.loc[df_all["dt"].dt.year == ano_sel, "dt"].dt.month
            .unique())
        meses_sel = st.pills(
            "Mês (nenhum = ano inteiro)", meses_ano, selection_mode="multi",
            format_func=lambda m: NOME_MES[m], key=f"meses_{ano_sel}")
    bancos = st.pills("Banco", options=sorted(df_all["banco_nome"].unique()),
                      selection_mode="multi",
                      default=sorted(df_all["banco_nome"].unique()))
    nat = st.pills("Origem da conta", options=["empresa", "socio"],
                   selection_mode="multi", default=["empresa", "socio"],
                   format_func=lambda x: "Empresa" if x == "empresa" else "Sócios")
    tipo_sel = st.segmented_control(
        "Tipo", options=["Saídas", "Entradas", "Todas"], default="Todas")
    st.divider()
    st.caption(f"{len(df_all):,} transações · "
               f"{df_all['conta'].nunique()} contas · "
               f"{df_all['banco'].nunique()} bancos")

d = df_all.copy()
if ano_sel != "Todos":
    d = d[d["dt"].dt.year == ano_sel]
    if meses_sel:
        d = d[d["dt"].dt.month.isin(meses_sel)]
if bancos:
    d = d[d["banco_nome"].isin(bancos)]
if nat:
    d = d[d["natureza"].isin(nat)]
if tipo_sel == "Saídas":
    d = d[d["tipo"] == "saida"]
elif tipo_sel == "Entradas":
    d = d[d["tipo"] == "entrada"]

saidas = d[d["tipo"] == "saida"]
entradas = d[d["tipo"] == "entrada"]

st.markdown("# :material/account_balance: Análise de extratos bancários")
st.caption("Asaas · Santander · Itaú · Inter (empresa) — Banco do Brasil (sócios)")

tab_geral, tab_alvos, tab_dest, tab_tx = st.tabs(
    [":material/dashboard: Visão geral",
     ":material/person_search: Alvos",
     ":material/account_tree: Destinatários",
     ":material/table: Transações"])

# ================================================================ VISÃO GERAL
with tab_geral:
    ent_m = entradas.groupby("mes")["valor"].sum()
    sai_m = saidas.groupby("mes")["valor"].sum()
    meses = sorted(set(ent_m.index) | set(sai_m.index))
    ent_s = [round(float(ent_m.get(m, 0)), 2) for m in meses]
    sai_s = [round(float(sai_m.get(m, 0)), 2) for m in meses]

    with st.grid(4, gap="medium"):
        st.metric("Total de saídas", brl(saidas["valor"].sum()),
                  border=True, chart_data=sai_s, chart_type="bar",
                  height="stretch")
        st.metric("Total de entradas", brl(entradas["valor"].sum()),
                  border=True, chart_data=ent_s, chart_type="bar",
                  height="stretch")
        st.metric("Envios aos alvos",
                  brl(saidas[saidas["alvo"] != ""]["valor"].sum()),
                  border=True, height="stretch",
                  delta=f"{(saidas['alvo'] != '').sum()} envios",
                  delta_description="Vanessa + Ivanilda + Sérgio")
        st.metric("Transf. internas do grupo",
                  brl(saidas[saidas["interna"]]["valor"].sum()),
                  border=True, height="stretch",
                  delta=f"{saidas['interna'].sum()} movimentos")

    col_l, col_r = st.columns([3, 2])
    with col_l:
        with st.container(border=True):
            st.subheader("Entradas × saídas por mês")
            st.echarts_chart({
                "tooltip": {"trigger": "axis",
                            "valueFormatter": "R$ %.2f"},
                "legend": {"top": 0},
                "grid": {"left": 70, "right": 20, "top": 40, "bottom": 70},
                "dataset": {"source": [
                    {"mes": m, "Entradas": e, "Saídas": s}
                    for m, e, s in zip(meses, ent_s, sai_s)]},
                "xAxis": {"type": "category"},
                "yAxis": {"type": "value"},
                "dataZoom": [{"type": "inside"},
                             {"type": "slider", "height": 18}],
                "series": [
                    {"name": "Entradas", "type": "bar", "stack": "x",
                     "itemStyle": {"color": "#34d399", "borderRadius": 3}},
                    {"name": "Saídas", "type": "bar", "stack": "x",
                     "itemStyle": {"color": "#f87171", "borderRadius": 3}},
                ]}, height=380, alt="Barras mensais de entradas e saídas")
    with col_r:
        with st.container(border=True):
            st.subheader("Saídas por banco")
            pb = saidas.groupby("banco_nome")["valor"].sum().sort_values(
                ascending=False)
            st.echarts_chart({
                "tooltip": {"trigger": "item",
                            "formatter": "{b}: R$ {c} ({d}%)"},
                "legend": {"bottom": 0},
                "series": [{
                    "type": "pie", "radius": ["45%", "72%"],
                    "center": ["50%", "45%"],
                    "itemStyle": {"borderColor": "#0d1117",
                                  "borderWidth": 2, "borderRadius": 6},
                    "label": {"show": False},
                    "data": [{"name": k, "value": round(float(v), 2)}
                             for k, v in pb.items()]}]},
                height=380, alt="Rosca de saídas por banco")

    with st.container(border=True):
        st.subheader("Top destinatários de saídas")
        st.caption("Clique numa barra para ver os lançamentos · "
                   "duplo clique limpa a seleção")
        top = saidas.groupby(saidas["destino"].map(norm))["valor"].agg(
            ["sum", "count"]).sort_values("sum", ascending=False).head(15)
        top = top.iloc[::-1]
        ev = st.echarts_chart({
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"},
                        "formatter": "{b}: R$ {c}"},
            "grid": {"left": 10, "right": 90, "top": 10, "bottom": 10,
                     "containLabel": True},
            "xAxis": {"type": "value"},
            "yAxis": {"type": "category",
                      "data": [i[:38] for i in top.index]},
            "series": [{
                "type": "bar",
                "selectedMode": "single",
                "data": [round(float(v), 2) for v in top["sum"]],
                "itemStyle": {"borderRadius": [0, 6, 6, 0],
                              "color": "#7c6cff"},
                "select": {"itemStyle": {"color": "#f472b6"}},
                "label": {"show": True, "position": "right",
                          "formatter": "{c}"}}]},
            height=460, alt="Barras horizontais dos maiores destinatários",
            on_select="rerun", key="top_dest")

        sel_idx = []
        if hasattr(ev, "selection") and ev.selection.get("selected"):
            sel_idx = [i for g in ev.selection["selected"]
                       for i in g["data_indices"]]
        if sel_idx and sel_idx[0] < len(top):
            nome_sel = top.index[sel_idx[0]]
            sub = saidas[saidas["destino"].map(norm) == nome_sel]
            st.divider()
            c1, c2, c3 = st.columns(3)
            c1.metric("Destinatário", nome_sel[:28], border=True)
            c2.metric("Enviado", brl(sub["valor"].sum()), border=True)
            c3.metric("Lançamentos", len(sub), border=True)
            vv = sub[["data", "banco_nome", "titular", "descricao",
                      "contraparte", "valor", "arquivo"]].sort_values(
                "data", ascending=False).copy()
            vv["valor"] = vv["valor"].map(brl)
            vv.columns = ["Data", "Banco", "Conta origem", "Descrição",
                          "Contraparte", "Valor", "Arquivo"]
            st.dataframe(vv, hide_index=True, height=320,
                         alt="Lançamentos do destinatário selecionado")

# ==================================================================== ALVOS
with tab_alvos:
    alvos_rows = saidas[saidas["alvo"] != ""]
    cols = st.columns(len(COR_ALVO))
    for col, (alvo, cor) in zip(cols, COR_ALVO.items()):
        sub = alvos_rows[alvos_rows["alvo"] == alvo]
        spark = sub.groupby("mes")["valor"].sum()
        spark = [float(spark.get(m, 0)) for m in
                 sorted(saidas["mes"].unique())]
        with col:
            st.metric(alvo.upper(), brl(sub["valor"].sum()),
                      border=True, chart_data=spark, chart_type="area",
                      delta=f"{len(sub)} envios", height="stretch")

    with st.container(border=True):
        st.subheader("Evolução mensal dos envios")
        serie_data = []
        for alvo, cor in COR_ALVO.items():
            sub = alvos_rows[alvos_rows["alvo"] == alvo]
            sm = sub.groupby("mes")["valor"].sum()
            serie_data.append({
                "name": alvo.capitalize(), "type": "line", "smooth": True,
                "stack": "alvos", "areaStyle": {"opacity": 0.25},
                "emphasis": {"focus": "series"},
                "itemStyle": {"color": cor},
                "data": [{"mes": m, "valor": round(float(sm.get(m, 0)), 2)}
                         for m in sorted(saidas["mes"].unique())]})
        st.echarts_chart({
            "tooltip": {"trigger": "axis", "valueFormatter": "R$ %.2f"},
            "legend": {"top": 0},
            "grid": {"left": 80, "right": 20, "top": 40, "bottom": 60},
            "xAxis": {"type": "category",
                      "data": sorted(saidas["mes"].unique())},
            "yAxis": {"type": "value"},
            "dataZoom": [{"type": "inside"}, {"type": "slider", "height": 18}],
            "series": [{**s, "data": [p["valor"] for p in s["data"]]}
                       for s in serie_data]},
            height=360, alt="Linhas de evolução mensal por alvo")

    sel_alvo = st.segmented_control(
        "Detalhar alvo", options=["Todos"] + list(ALVOS), default="Todos",
        format_func=str.capitalize)
    det = alvos_rows if sel_alvo == "Todos" else \
        alvos_rows[alvos_rows["alvo"] == sel_alvo]
    view = det[["data", "banco_nome", "titular", "descricao",
                "contraparte", "contraparte_doc", "valor"]].sort_values(
        "valor", ascending=False).copy()
    view["valor"] = view["valor"].map(brl)
    view.columns = ["Data", "Banco", "Conta origem", "Descrição",
                    "Destinatário", "CPF/CNPJ", "Valor"]
    st.dataframe(view, hide_index=True, alt="Envios detalhados ao alvo")

# ============================================================ DESTINATÁRIOS
with tab_dest:
    ext = saidas[~saidas["interna"]]
    topn = ext.groupby(ext["destino"].map(norm)).agg(
        nome=("destino", "first"), total=("valor", "sum"),
        qtd=("valor", "count")).sort_values("total", ascending=False)

    with st.container(border=True):
        st.subheader("Fluxo: banco → destinatário (top 15)")
        top15 = topn.head(15).index.tolist()
        flux = (ext.assign(dest_n=ext["destino"].map(norm))
                  [lambda x: x["dest_n"].isin(top15)]
                  .groupby(["banco_nome", "dest_n"])["valor"].sum()
                  .reset_index())
        nodes = ([{"name": b} for b in flux["banco_nome"].unique()] +
                 [{"name": dn[:40]} for dn in top15])
        links = [{"source": r["banco_nome"], "target": r["dest_n"][:40],
                  "value": round(float(r["valor"]), 2)}
                 for _, r in flux.iterrows()]
        st.echarts_chart({
            "tooltip": {"trigger": "item",
                        "formatter": "{b}: R$ {c}"},
            "series": [{
                "type": "sankey", "left": 20, "right": 190, "top": 15,
                "bottom": 15, "nodeGap": 14, "nodeWidth": 18,
                "emphasis": {"focus": "adjacency"},
                "lineStyle": {"color": "gradient", "curveness": 0.5,
                              "opacity": 0.35},
                "label": {"fontSize": 12},
                "data": [{"name": n["name"]} for n in nodes],
                "links": links}]},
            height=560, alt="Diagrama Sankey banco para destinatário")

    with st.container(border=True):
        st.subheader("Ranking de destinatários")
        rk = topn.copy()
        rk["total_fmt"] = rk["total"].map(brl)
        rk["%"] = (rk["total"] / rk["total"].sum() * 100).round(1)
        rk = rk[["nome", "qtd", "total_fmt", "%"]]
        rk.columns = ["Destinatário", "Envios", "Total", "% das saídas ext."]
        st.dataframe(rk, hide_index=True, alt="Ranking de destinatários")

# ============================================================== TRANSAÇÕES
with tab_tx:
    busca = st.text_input("Buscar", placeholder="nome, descrição, doc…",
                          icon=":material/search:")
    tx = d.copy()
    if busca:
        m = tx.apply(
            lambda r: busca.lower() in str(
                r["contraparte"]).lower() or
            busca.lower() in str(r["descricao"]).lower() or
            busca.lower() in str(r["detalhe"]).lower() or
            busca.lower() in str(r["contraparte_doc"]).lower(), axis=1)
        tx = tx[m]
    v = tx[["data", "banco_nome", "titular", "tipo", "descricao",
            "contraparte", "contraparte_doc", "valor", "alvo"]].sort_values(
        "data", ascending=False).copy()
    v["valor"] = v["valor"].map(brl)
    v.columns = ["Data", "Banco", "Conta", "Tipo", "Descrição",
                 "Contraparte", "Doc", "Valor", "Alvo"]
    st.caption(f"{len(v):,} transações")
    st.dataframe(v, hide_index=True, height=560, alt="Tabela de transações")
