#!/usr/bin/env python3
"""
Gera o relatório semanal de Meta Ads da Tekoan no estilo padrão.

Entrada : JSON com linhas dia x criativo (campos abaixo).
Saída   : PDF estilizado + arquivo email.json {subject, body_html, body_text}.

Uso:
    python generate_report.py dados.json --outdir ./out

Cada linha do JSON deve conter:
    date (YYYY-MM-DD), ad_name, spend, impressions, clicks,
    conversations  (conversas iniciadas no WhatsApp / CTWA),
    leads          (leads de formulário/pixel; normalmente 0 nesta conta)
"""
import json, sys, argparse, datetime as dt
from pathlib import Path
from collections import defaultdict

# ----------------------------- helpers -----------------------------
def brl(v):
    s = f"{v:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def pct(v):  # v as fraction
    return f"{v*100:.1f}%".replace(".", ",")

def pct2(v):
    return f"{v*100:.2f}%".replace(".", ",")

def agg(rows):
    sp  = sum(r["spend"] for r in rows)
    imp = sum(r["impressions"] for r in rows)
    cl  = sum(r["clicks"] for r in rows)
    cv  = sum(r["conversations"] for r in rows)
    ld  = sum(r.get("leads", 0) for r in rows)
    return dict(
        spend=sp, impressions=imp, clicks=cl, conv=cv, leads=ld,
        cpm=sp / imp * 1000 if imp else 0,
        cpc=sp / cl if cl else 0,
        ctr=cl / imp if imp else 0,
        convr=cv / cl if cl else 0,
        cpl=sp / cv if cv else 0,
    )

def delta_label(cur, prev, lower_is_better=True):
    if prev == 0:
        return ("—", "flat")
    d = (cur - prev) / prev
    better = (d < 0) if lower_is_better else (d > 0)
    arrow = "▼" if d < 0 else "▲"
    cls = "good" if better else "bad"
    return (f"{arrow} {abs(d)*100:.0f}%", cls)

# ----------------------------- load -----------------------------
ap = argparse.ArgumentParser()
ap.add_argument("data")
ap.add_argument("--outdir", default=".")
ap.add_argument("--recipient", default="")
ap.add_argument("--mode", default="weekly", choices=["weekly", "monthly"])
args = ap.parse_args()

rows = json.loads(Path(args.data).read_text())
for r in rows:
    for k in ("spend", "impressions", "clicks", "conversations"):
        r[k] = float(r.get(k) or 0)
    r["leads"] = float(r.get("leads") or 0)

dates = sorted({r["date"] for r in rows})
if not dates:
    sys.exit("Sem dados.")
maxd = dt.date.fromisoformat(dates[-1])
MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
         "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

def _d(r):  return dt.date.fromisoformat(r["date"])

if args.mode == "monthly":
    # mês reportado = mês da última data; comparativo = mês calendário anterior
    y, m = maxd.year, maxd.month
    first = dt.date(y, m, 1)
    pm_y, pm_m = (y - 1, 12) if m == 1 else (y, m - 1)
    cur_rows  = [r for r in rows if (_d(r).year, _d(r).month) == (y, m)]
    prev_rows = [r for r in rows if (_d(r).year, _d(r).month) == (pm_y, pm_m)]
    period_str = f"{first.strftime('%d/%m')} a {maxd.strftime('%d/%m/%Y')}"
    per_days = (maxd - first).days + 1
    mlabel = f"{MESES[m-1]}/{y}"
    L = dict(kicker="Relatório mensal · Meta Ads",
             title="Tekoan — Performance do mês", per_suffix=f"({mlabel})",
             total="Mês todo", comp_title="Comparativo com o mês anterior",
             comp_prev="Mês anterior", comp_cur="Este mês", unit="mês",
             fname="mensal", subj=f"mês {mlabel}", footer="Relatório mensal Meta Ads")
else:  # weekly
    cut = maxd - dt.timedelta(days=6)        # janela "semana reportada" = últimos 7 dias
    prev_cut = cut - dt.timedelta(days=7)
    cur_rows  = [r for r in rows if _d(r) >= cut]
    prev_rows = [r for r in rows if prev_cut <= _d(r) < cut]
    period_str = f"{cut.strftime('%d/%m')} a {maxd.strftime('%d/%m/%Y')}"
    per_days = 7
    L = dict(kicker="Relatório semanal · Meta Ads",
             title="Tekoan — Performance da semana", per_suffix="(7 dias)",
             total="Semana toda", comp_title="Comparativo com a semana anterior",
             comp_prev="Semana anterior", comp_cur="Esta semana", unit="semana",
             fname="semanal", subj=f"semana {period_str}", footer="Relatório semanal Meta Ads")

W = agg(cur_rows)
P = agg(prev_rows) if prev_rows else None

# por criativo (no período reportado)
creatives = defaultdict(list)
for r in cur_rows:
    creatives[r["ad_name"]].append(r)
cre_aggs = sorted(
    [(name, agg(rs)) for name, rs in creatives.items()],
    key=lambda x: (x[1]["cpl"] if x[1]["conv"] else 9e9),
)

gen_str = dt.datetime.now().strftime("%d/%m/%Y %H:%M")

# ----------------------------- diagnóstico de gestor (itens estruturados) -----------------------------
# cada item: title, detail, action (str ou None), cert ('confirmado'|'validar'), prio (1|2|3)
unidade = L["unit"]
items = []

# 1. Conversão = CTWA, não lead qualificado
if W["leads"] == 0 and W["conv"] > 0:
    items.append(dict(
        title="A conversão é conversa no WhatsApp, não lead qualificado",
        detail=(f"Formulário e pixel zerados: as {W['conv']:.0f} conversões do período são conversas "
                f"iniciadas (CTWA). O CPL de {brl(W['cpl'])} é custo por conversa, não por agendamento ou venda."),
        action=(f"Cruzar as {W['conv']:.0f} conversas com o CRM/WhatsApp e medir quantas viraram agendamento "
                "e venda. Sem esse número, o CPL não diz se a verba está rentável."),
        cert="confirmado", prio=1))

# 2. Verba desbalanceada entre criativos
valid = [(n, a) for n, a in cre_aggs if a["conv"] > 0]
if len(valid) >= 2:
    best_n, best = valid[0]
    worst_n, worst = valid[-1]
    if worst["cpl"] > best["cpl"] * 1.2:
        det = (f"{best_n}: CPL {brl(best['cpl'])} com {brl(best['spend'])} de verba. "
               f"{worst_n}: CPL {brl(worst['cpl'])} com {brl(worst['spend'])}.")
        if worst["spend"] > best["spend"]:
            det += " O criativo mais caro por conversa está levando mais verba."
        items.append(dict(
            title="Verba desbalanceada entre criativos",
            detail=det,
            action=(f"Migrar verba aos poucos do «{worst_n}» para o «{best_n}». Antes de cortar, confirmar que "
                    f"o CPL melhor sustenta: é amostra de {best['conv']:.0f} conversas, ainda pequena."),
            cert="validar", prio=1))

# 3. Pouca diversidade criativa / fadiga
if len(cre_aggs) <= 2:
    items.append(dict(
        title=f"Só {len(cre_aggs)} criativo(s) no ar — base criativa estreita",
        detail=("Pouca variação limita o aprendizado do algoritmo e acelera fadiga de público "
                "(frequência sobe e o CPM encarece)."),
        action=("Subir 2 a 3 criativos novos com ângulos distintos (oferta, prova social, antes/depois) "
                "para dar material de teste e otimização."),
        cert="validar", prio=2))

# 4. Tendência vs período anterior
if P and P["conv"] > 0:
    dcpl, _ = delta_label(W["cpl"], P["cpl"])
    piora = W["cpl"] > P["cpl"] * 1.1
    items.append(dict(
        title=f"Tendência vs {unidade} anterior",
        detail=(f"CPL {brl(P['cpl'])} → {brl(W['cpl'])} ({dcpl}) · conversas {P['conv']:.0f} → {W['conv']:.0f} · "
                f"CPC {brl(P['cpc'])} → {brl(W['cpc'])} · investimento {brl(P['spend'])} → {brl(W['spend'])}."),
        action=("CPL em alta: investigar criativo/público antes de manter ou subir verba." if piora else None),
        cert="confirmado", prio=2 if piora else 3))

# 5. CPM elevado
if W["cpm"] > 70:
    items.append(dict(
        title=f"CPM elevado ({brl(W['cpm'])})",
        detail="Pode ser público estreito/saturado ou leilão concorrido — a entrega sozinha não distingue a causa.",
        action=("Checar frequência e tamanho de público; se a frequência estiver alta, ampliar público "
                "ou renovar criativo."),
        cert="validar", prio=2))

# 6. Volume baixo (cuidado estatístico)
if W["conv"] < 20:
    items.append(dict(
        title=f"Volume baixo ({W['conv']:.0f} conversas no período)",
        detail=(f"Cerca de {W['conv']/per_days:.1f} conversa(s) por dia. Oscilação diária é ruído de "
                "amostra, não tendência."),
        action="Não cortar criativo nem mexer em verba por um dia ruim; decidir sempre por período fechado.",
        cert="confirmado", prio=3))

# 7. Escala depende de capacidade de atendimento
if W["conv"] >= 8:
    items.append(dict(
        title="Escalar verba depende da capacidade de atendimento",
        detail=("Mais verba só compensa se a recepção responde rápido e converte as conversas. "
                "Conversa não respondida é dinheiro perdido."),
        action=("Antes de subir o orçamento, confirmar com a operação o tempo de resposta e o gargalo "
                "de atendimento no WhatsApp."),
        cert="validar", prio=2))

# ----------------------------- HTML/PDF -----------------------------
CSS = """
@page { size: A4; margin: 20mm 18mm 18mm 18mm;
  @bottom-center { content: "Tekoan · Relatório Meta Ads · confidencial";
    font-family: Helvetica, Arial, sans-serif; font-size: 7.5pt; color: #9aa3ad; }
  @bottom-right { content: "Pág. " counter(page) " / " counter(pages);
    font-family: Helvetica, Arial, sans-serif; font-size: 7.5pt; color: #9aa3ad; } }
* { box-sizing: border-box; }
body { font-family: Helvetica, Arial, sans-serif; color: #1d2530; font-size: 9.7pt; line-height: 1.5; margin: 0; }
.top { border-bottom: 2.5px solid #1F3A5F; padding-bottom: 12px; margin-bottom: 20px; }
.kicker { font-size: 8pt; letter-spacing: 2.5px; text-transform: uppercase; color: #C9A227; font-weight: 700; margin: 0 0 4px 0; }
h1 { font-size: 19pt; color: #1F3A5F; margin: 0 0 8px 0; line-height: 1.15; letter-spacing: -0.3px; }
.meta { font-size: 8.6pt; color: #5a6472; margin: 0; }
.meta strong { color: #1d2530; }
h2 { font-size: 12pt; color: #1F3A5F; margin: 22px 0 9px 0; padding-bottom: 4px; border-bottom: 1px solid #e3e7ec; }
h2 .num { color: #C9A227; font-weight: 800; margin-right: 6px; }
p { margin: 0 0 9px 0; }
.lead { background: #fbf7ea; border-left: 4px solid #C9A227; padding: 13px 16px; border-radius: 0 5px 5px 0; margin: 0 0 6px 0; }
.lead .tag { font-size: 7.5pt; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; color: #C9A227; display: block; margin-bottom: 3px; }
.lead p { margin: 0; font-size: 9.7pt; }
table { width: 100%; border-collapse: collapse; margin: 6px 0 4px 0; font-size: 8.7pt; }
thead th { background: #1F3A5F; color: #fff; font-weight: 600; padding: 7px 6px; text-align: right; border: none; }
thead th:first-child { text-align: left; }
tbody td { padding: 6px 6px; text-align: right; border-bottom: 1px solid #eaedf1; }
tbody td:first-child { text-align: left; font-weight: 600; }
tbody tr.total td { background: #1F3A5F; color: #fff; font-weight: 700; border: none; }
tbody tr.win td { background: #f1f7f0; }
tbody tr.lose td { background: #fcf2f1; }
.d-good { color: #2c6e2c; font-weight: 700; }
.d-bad { color: #a83228; font-weight: 700; }
.finding { margin: 0 0 11px 0; padding-left: 14px; border-left: 3px solid #1F3A5F; }
.finding .ft { font-weight: 700; color: #1F3A5F; font-size: 10pt; }
.small { font-size: 8.2pt; color: #6b7785; }
.legend { font-size: 8pt; color: #6b7785; margin: 0 0 11px 0; line-height: 1.7; }
.badge { font-size: 6.8pt; font-weight: 700; padding: 1px 6px; border-radius: 9px; white-space: nowrap; }
.badge.confirmado { background: #e3efe3; color: #2c6e2c; }
.badge.validar { background: #faf0d6; color: #8a5d00; }
.diag { margin: 0 0 9px 0; padding-left: 13px; border-left: 3px solid #2c6e2c; }
.diag.validar { border-left-color: #C9A227; }
.diag .dt { font-weight: 700; color: #1F3A5F; font-size: 9.8pt; }
.diag .dd { font-size: 9.3pt; color: #2b333d; }
.task { margin: 0 0 7px 0; font-size: 9.4pt; line-height: 1.45; }
.task .chk { color: #1F3A5F; font-weight: 700; margin-right: 6px; }
.prio { font-size: 6.8pt; font-weight: 800; color: #fff; padding: 1px 5px; border-radius: 3px; margin-right: 6px; }
.prio.p1 { background: #a83228; }
.prio.p2 { background: #b8860b; }
.prio.p3 { background: #6b7785; }
"""

def row_html(label, a, cls=""):
    return (f'<tr class="{cls}"><td>{label}</td><td>{brl(a["spend"])}</td>'
            f'<td>{brl(a["cpm"])}</td><td>{brl(a["cpc"])}</td><td>{pct2(a["ctr"])}</td>'
            f'<td>{a["clicks"]:.0f}</td><td>{pct(a["convr"])}</td>'
            f'<td>{a["conv"]:.0f}</td><td>{brl(a["cpl"]) if a["conv"] else "—"}</td></tr>')

head = ('<tr><th>Recorte</th><th>Invest.</th><th>CPM</th><th>CPC</th><th>CTR</th>'
        '<th>Cliques</th><th>% Conv</th><th>Conversas</th><th>CPL</th></tr>')

cons = row_html(L["total"], W, "total")
cre_html = ""
for i, (n, a) in enumerate(cre_aggs):
    cls = "win" if (i == 0 and a["conv"] > 0 and len(cre_aggs) > 1) else (
          "lose" if (i == len(cre_aggs)-1 and len(cre_aggs) > 1 and a["conv"] > 0) else "")
    cre_html += row_html(n, a, cls)

wow_html = ""
if P:
    def wow_row(label, key, fmt, lower_better=True):
        cur = W[key]; prev = P[key]
        d, c = delta_label(cur, prev, lower_better)
        return (f'<tr><td>{label}</td><td>{fmt(prev)}</td><td>{fmt(cur)}</td>'
                f'<td class="d-{c}">{d}</td></tr>')
    wow_html = (
        f'<table><thead><tr><th>Métrica</th><th>{L["comp_prev"]}</th>'
        f'<th>{L["comp_cur"]}</th><th>Δ</th></tr></thead><tbody>'
        + wow_row("Investimento", "spend", brl, lower_better=False)
        + wow_row("CPC", "cpc", brl)
        + wow_row("CTR", "ctr", pct2, lower_better=False)
        + wow_row("Conversas", "conv", lambda v: f"{v:.0f}", lower_better=False)
        + wow_row("CPL", "cpl", brl)
        + '</tbody></table>')

lead_block = ""
if W["leads"] == 0 and W["conv"] > 0:
    lead_block = (
        '<div class="lead"><span class="tag">Ponto crítico — ler primeiro</span>'
        '<p>A conta <b>não registra “Leads”</b> (formulário/pixel zerados). O único '
        'evento de conversão é <b>conversa iniciada no WhatsApp (CTWA)</b>. Portanto, '
        '“Conversas” é o proxy de lead e o <b>CPL é custo por conversa</b> — não por '
        'lead qualificado, agendamento ou fechamento. Validar objetivo e rastreio '
        'pós-conversa antes de escalar verba.</p></div>')

PRIO_LBL = {1: "P1", 2: "P2", 3: "P3"}
def cert_badge(c):
    return ('<span class="badge confirmado">Confirmado</span>' if c == "confirmado"
            else '<span class="badge validar">Validar</span>')

legend = ('<p class="legend">'
          '<span class="badge confirmado">Confirmado</span> = 100% nos dados &nbsp;·&nbsp; '
          '<span class="badge validar">Validar</span> = precisa de confirmação humana (CRM, operação, frequência)'
          '<br>Prioridade: <span class="prio p1">P1</span> alta &nbsp; '
          '<span class="prio p2">P2</span> média &nbsp; <span class="prio p3">P3</span> baixa</p>')

diag_html = ""
for it in items:
    cls = "diag" if it["cert"] == "confirmado" else "diag validar"
    diag_html += (f'<div class="{cls}"><p><span class="dt">{it["title"]}</span> '
                  f'{cert_badge(it["cert"])}<br><span class="dd">{it["detail"]}</span></p></div>')

acts = sorted([it for it in items if it.get("action")], key=lambda it: it["prio"])
task_html = ""
for it in acts:
    task_html += (f'<div class="task"><span class="chk">&#9744;</span>'
                  f'<span class="prio p{it["prio"]}">{PRIO_LBL[it["prio"]]}</span>'
                  f'{it["action"]} {cert_badge(it["cert"])}</div>')

html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<style>{CSS}</style></head><body>
<div class="top">
  <p class="kicker">{L["kicker"]}</p>
  <h1>{L["title"]}</h1>
  <p class="meta"><strong>Conta:</strong> Tekoan — Conta de Anúncios &nbsp;·&nbsp;
  <strong>Período:</strong> {period_str} {L["per_suffix"]} &nbsp;·&nbsp;
  <strong>Vertical:</strong> Estética (foco deliberado de GTM)<br>
  <strong>Gerado em:</strong> {gen_str} &nbsp;·&nbsp;<strong>Fonte:</strong> Meta Ads API via Windsor.ai</p>
</div>
{lead_block}
<h2><span class="num">01</span>Consolidado e por criativo</h2>
<table><thead>{head}</thead><tbody>{cons}{cre_html}</tbody></table>
<p class="small">CPL = Investimento ÷ Conversas (WhatsApp). % Conv = Conversas ÷ Cliques.</p>
{(f'<h2><span class="num">02</span>{L["comp_title"]}</h2>' + wow_html) if wow_html else ''}
<h2><span class="num">{'03' if wow_html else '02'}</span>Diagnóstico — visão de gestor de tráfego</h2>
{legend}
{diag_html}
<h2><span class="num">{'04' if wow_html else '03'}</span>Plano de ação priorizado</h2>
{task_html}
</body></html>"""

outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
# sempre grava o HTML completo do relatorio (fallback de corpo de e-mail sem dependencia de sistema)
(outdir / "report.html").write_text(html)
pdf_path = outdir / f"Tekoan_Meta_Ads_{L['fname']}_{maxd.isoformat()}.pdf"
pdf_ok = False
try:
    from weasyprint import HTML
    HTML(string=html).write_pdf(str(pdf_path))
    pdf_ok = True
except Exception as _ex:
    print("WARN: PDF indisponivel (weasyprint):", _ex)

# ----------------------------- e-mail -----------------------------
subject = f"[Tekoan] Relatório Meta Ads — {L['subj']}"

# corpo HTML enxuto (resumo + tabela)
body_rows = (
    f"<tr><td><b>Investimento</b></td><td>{brl(W['spend'])}</td></tr>"
    f"<tr><td><b>CPC</b></td><td>{brl(W['cpc'])}</td></tr>"
    f"<tr><td><b>CTR</b></td><td>{pct2(W['ctr'])}</td></tr>"
    f"<tr><td><b>Conversas (WhatsApp)</b></td><td>{W['conv']:.0f}</td></tr>"
    f"<tr><td><b>CPL (custo/conversa)</b></td><td>{brl(W['cpl']) if W['conv'] else '—'}</td></tr>"
)
def cert_txt(c): return "Confirmado" if c == "confirmado" else "Validar"
email_acts = "".join(
    f'<li style="margin-bottom:6px"><b>{PRIO_LBL[it["prio"]]}</b> · {it["action"]} '
    f'<span style="font-size:11px;color:{"#2c6e2c" if it["cert"]=="confirmado" else "#8a5d00"}">'
    f'[{cert_txt(it["cert"])}]</span></li>' for it in acts[:4])
body_html = f"""<div style="font-family:Helvetica,Arial,sans-serif;color:#1d2530;font-size:14px;line-height:1.55">
<p>Olá,</p>
<p>Segue o resumo de Meta Ads da Tekoan referente a <b>{period_str}</b> {L["per_suffix"]}. PDF completo em anexo.</p>
<table style="border-collapse:collapse;font-size:14px;margin:8px 0 14px">
{body_rows}
</table>
<p style="margin:0 0 4px"><b>Ações prioritárias:</b></p>
<ul style="margin:0 0 14px;padding-left:18px">{email_acts}</ul>
<p style="font-size:12px;color:#6b7785;margin:0 0 12px">P1 alta · P2 média · P3 baixa &nbsp;|&nbsp; [Confirmado] = 100% nos dados · [Validar] = precisa de confirmação humana. Diagnóstico completo e checklist no PDF.</p>
<p style="color:#6b7785;font-size:12px">Gerado automaticamente em {gen_str}. CPL = custo por conversa no WhatsApp (não há lead de formulário/pixel nesta conta).</p>
</div>"""

# corpo texto puro (fallback)
lines = [f"Resumo Meta Ads Tekoan — {L['subj']}", "",
         f"Investimento: {brl(W['spend'])}",
         f"CPC: {brl(W['cpc'])}  |  CTR: {pct2(W['ctr'])}",
         f"Conversas (WhatsApp): {W['conv']:.0f}",
         f"CPL (custo/conversa): {brl(W['cpl']) if W['conv'] else '—'}", "", "Ações prioritárias:"]
for it in acts[:4]:
    lines.append(f"- [{PRIO_LBL[it['prio']]}] {it['action']} ({cert_txt(it['cert'])})")
lines += ["", "PDF completo em anexo."]
body_text = "\n".join(lines)

email = dict(recipient=args.recipient, subject=subject,
             body_html=body_html, body_text=body_text,
             attachment=str(pdf_path) if pdf_ok else "",
             report_html=str(outdir / "report.html"))
(outdir / "email.json").write_text(json.dumps(email, ensure_ascii=False, indent=2))

print(json.dumps({"pdf": str(pdf_path) if pdf_ok else None,
                  "email_json": str(outdir / "email.json"),
                  "subject": subject, "pdf_ok": pdf_ok}, ensure_ascii=False))
