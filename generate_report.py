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
cut  = maxd - dt.timedelta(days=6)          # janela "semana reportada" = últimos 7 dias
prev_cut = cut - dt.timedelta(days=7)

def in_week(r):  return dt.date.fromisoformat(r["date"]) >= cut
def in_prev(r):  return prev_cut <= dt.date.fromisoformat(r["date"]) < cut

week_rows = [r for r in rows if in_week(r)]
prev_rows = [r for r in rows if in_prev(r)]

W = agg(week_rows)
P = agg(prev_rows) if prev_rows else None

# por criativo (na semana reportada)
creatives = defaultdict(list)
for r in week_rows:
    creatives[r["ad_name"]].append(r)
cre_aggs = sorted(
    [(name, agg(rs)) for name, rs in creatives.items()],
    key=lambda x: (x[1]["cpl"] if x[1]["conv"] else 9e9),
)

period_str = f"{cut.strftime('%d/%m')} a {maxd.strftime('%d/%m/%Y')}"
gen_str = dt.datetime.now().strftime("%d/%m/%Y %H:%M")

# ----------------------------- findings (dinâmicos) -----------------------------
findings = []

# 1. sem leads reais
if W["leads"] == 0 and W["conv"] > 0:
    findings.append((
        "“Leads” = conversas no WhatsApp.",
        "Nenhum lead de formulário ou pixel disparou na semana. O único evento de "
        "conversão é conversa iniciada (CTWA), então o CPL é custo por conversa — "
        "não por lead qualificado, agendamento ou fechamento. Validar objetivo e "
        "rastreio pós-conversa antes de escalar verba."
    ))

# 2. melhor vs pior criativo
valid = [(n, a) for n, a in cre_aggs if a["conv"] > 0]
if len(valid) >= 2:
    best_n, best = valid[0]
    worst_n, worst = valid[-1]
    if worst["cpl"] > best["cpl"] * 1.2:
        msg = (f"{best_n} fecha a semana com o melhor CPL ({brl(best['cpl'])}) "
               f"vs. {brl(worst['cpl'])} do {worst_n}.")
        if worst["spend"] > best["spend"]:
            msg += (f" Mesmo assim, o pior criativo levou mais verba "
                    f"({brl(worst['spend'])} vs. {brl(best['spend'])}) — realocar.")
        findings.append(("Desequilíbrio entre criativos.", msg))

# 3. week over week
if P and P["conv"] > 0:
    parts = []
    dc, _ = delta_label(W["cpc"], P["cpc"]); parts.append(f"CPC {dc}")
    dl, _ = delta_label(W["cpl"], P["cpl"]); parts.append(f"CPL {dl}")
    dconv = "estável" if abs(W["conv"]-P["conv"]) <= 1 else (
        f"{'subiu' if W['conv']>P['conv'] else 'caiu'} {abs(W['conv']-P['conv']):.0f}")
    findings.append((
        "Comparativo com a semana anterior.",
        f"CPC saiu de {brl(P['cpc'])} para {brl(W['cpc'])}; "
        f"CPL de {brl(P['cpl'])} para {brl(W['cpl'])}; "
        f"conversas: {dconv} ({P['conv']:.0f} → {W['conv']:.0f})."
    ))

# 4. volume baixo
if W["conv"] < 20:
    findings.append((
        "Volume baixo — leia por semana, não por dia.",
        f"São {W['conv']:.0f} conversas na semana (~{W['conv']/7:.1f}/dia). "
        "Oscilações diárias de CPL são ruído de amostra, não tendência."
    ))

# 5. CPM alto
if W["cpm"] > 70:
    findings.append((
        "CPM elevado.",
        f"CPM de {brl(W['cpm'])} sugere público estreito ou saturação. "
        "Acompanhar frequência e considerar ampliação se confirmar."
    ))

# ----------------------------- HTML/PDF -----------------------------
CSS = """
@page { size: A4; margin: 20mm 18mm 18mm 18mm;
  @bottom-center { content: "Tekoan · Relatório semanal Meta Ads · confidencial";
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
"""

def row_html(label, a, cls=""):
    return (f'<tr class="{cls}"><td>{label}</td><td>{brl(a["spend"])}</td>'
            f'<td>{brl(a["cpm"])}</td><td>{brl(a["cpc"])}</td><td>{pct2(a["ctr"])}</td>'
            f'<td>{a["clicks"]:.0f}</td><td>{pct(a["convr"])}</td>'
            f'<td>{a["conv"]:.0f}</td><td>{brl(a["cpl"]) if a["conv"] else "—"}</td></tr>')

head = ('<tr><th>Recorte</th><th>Invest.</th><th>CPM</th><th>CPC</th><th>CTR</th>'
        '<th>Cliques</th><th>% Conv</th><th>Conversas</th><th>CPL</th></tr>')

cons = row_html("Semana toda", W, "total")
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
        '<table><thead><tr><th>Métrica</th><th>Semana anterior</th>'
        '<th>Esta semana</th><th>Δ</th></tr></thead><tbody>'
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

find_html = ""
for i, (t, b) in enumerate(findings, 1):
    find_html += f'<div class="finding"><p><span class="ft">{i}. {t}</span><br>{b}</p></div>'

html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<style>{CSS}</style></head><body>
<div class="top">
  <p class="kicker">Relatório semanal · Meta Ads</p>
  <h1>Tekoan — Performance da semana</h1>
  <p class="meta"><strong>Conta:</strong> Tekoan — Conta de Anúncios &nbsp;·&nbsp;
  <strong>Período:</strong> {period_str} (7 dias) &nbsp;·&nbsp;
  <strong>Vertical:</strong> Estética (foco deliberado de GTM)<br>
  <strong>Gerado em:</strong> {gen_str} &nbsp;·&nbsp;<strong>Fonte:</strong> Meta Ads API via Windsor.ai</p>
</div>
{lead_block}
<h2><span class="num">01</span>Consolidado e por criativo</h2>
<table><thead>{head}</thead><tbody>{cons}{cre_html}</tbody></table>
<p class="small">CPL = Investimento ÷ Conversas (WhatsApp). % Conv = Conversas ÷ Cliques.</p>
{('<h2><span class="num">02</span>Comparativo com a semana anterior</h2>' + wow_html) if wow_html else ''}
<h2><span class="num">{'03' if wow_html else '02'}</span>Achados e ações</h2>
{find_html}
</body></html>"""

outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
# sempre grava o HTML completo do relatorio (fallback de corpo de e-mail sem dependencia de sistema)
(outdir / "report.html").write_text(html)
pdf_path = outdir / f"Tekoan_Meta_Ads_semanal_{maxd.isoformat()}.pdf"
pdf_ok = False
try:
    from weasyprint import HTML
    HTML(string=html).write_pdf(str(pdf_path))
    pdf_ok = True
except Exception as _ex:
    print("WARN: PDF indisponivel (weasyprint):", _ex)

# ----------------------------- e-mail -----------------------------
subject = f"[Tekoan] Relatório Meta Ads — semana {period_str}"

# corpo HTML enxuto (resumo + tabela)
body_rows = (
    f"<tr><td><b>Investimento</b></td><td>{brl(W['spend'])}</td></tr>"
    f"<tr><td><b>CPC</b></td><td>{brl(W['cpc'])}</td></tr>"
    f"<tr><td><b>CTR</b></td><td>{pct2(W['ctr'])}</td></tr>"
    f"<tr><td><b>Conversas (WhatsApp)</b></td><td>{W['conv']:.0f}</td></tr>"
    f"<tr><td><b>CPL (custo/conversa)</b></td><td>{brl(W['cpl']) if W['conv'] else '—'}</td></tr>"
)
top_findings = "".join(f"<li>{t} {b}</li>" for t, b in findings[:3])
body_html = f"""<div style="font-family:Helvetica,Arial,sans-serif;color:#1d2530;font-size:14px;line-height:1.55">
<p>Olá,</p>
<p>Segue o resumo de Meta Ads da Tekoan da semana <b>{period_str}</b>. PDF completo em anexo.</p>
<table style="border-collapse:collapse;font-size:14px;margin:8px 0 14px">
{body_rows}
</table>
<p style="margin:0 0 4px"><b>Destaques:</b></p>
<ul style="margin:0 0 14px;padding-left:18px">{top_findings}</ul>
<p style="color:#6b7785;font-size:12px">Gerado automaticamente em {gen_str}. CPL = custo por conversa no WhatsApp (não há lead de formulário/pixel nesta conta).</p>
</div>"""

# corpo texto puro (fallback)
lines = [f"Resumo Meta Ads Tekoan — semana {period_str}", "", 
         f"Investimento: {brl(W['spend'])}",
         f"CPC: {brl(W['cpc'])}  |  CTR: {pct2(W['ctr'])}",
         f"Conversas (WhatsApp): {W['conv']:.0f}",
         f"CPL (custo/conversa): {brl(W['cpl']) if W['conv'] else '—'}", "", "Destaques:"]
for t, b in findings[:3]:
    lines.append(f"- {t} {b}")
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
