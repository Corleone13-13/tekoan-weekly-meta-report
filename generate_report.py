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
import html as htmllib  # alias: a variavel `html` (string do relatorio) reusa o nome
from pathlib import Path
from collections import defaultdict

# ----------------------------- helpers -----------------------------
def esc(s):
    """Escapa texto-DADO vindo do analysis.json (escrito pelo agente LLM) antes de
    injetar no HTML. Sem isso, um '<' vira abertura de tag e um '&' corrompe a
    saida (ex.: 'CTR < 1%' ou 'custo & retorno'). Aplicar so no texto do agente,
    nunca no HTML que nos mesmos geramos (badges, classes)."""
    return htmllib.escape(str(s)) if s else ""

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
    fr  = sum(r.get("first_reply", 0) for r in rows)
    blk = sum(r.get("blocks", 0) for r in rows)
    # video (aditivos; ausentes em criativo estatico → 0). p25..p100 = curva de retencao.
    v3s   = sum(r.get("video_3s", 0) for r in rows)
    vpl   = sum(r.get("video_plays", 0) for r in rows)
    thr   = sum(r.get("thruplays", 0) for r in rows)
    p25   = sum(r.get("v_p25", 0) for r in rows)
    p50   = sum(r.get("v_p50", 0) for r in rows)
    p75   = sum(r.get("v_p75", 0) for r in rows)
    p95   = sum(r.get("v_p95", 0) for r in rows)
    p100  = sum(r.get("v_p100", 0) for r in rows)
    # custo/thruplay: media ponderada por thruplays se houver o campo de custo;
    # senao deriva de spend/thruplays. tempo medio: media ponderada por video_plays.
    cpt_num = sum(r.get("cost_per_thruplay", 0) * r.get("thruplays", 0) for r in rows)
    cpt_w   = sum(r.get("thruplays", 0) for r in rows if r.get("cost_per_thruplay", 0))
    at_num  = sum(r.get("video_avg_time", 0) * r.get("video_plays", 0) for r in rows)
    is_video = (vpl > 0 or v3s > 0)
    a = dict(
        spend=sp, impressions=imp, clicks=cl, conv=cv, leads=ld,
        first_reply=fr, blocks=blk,
        cpm=sp / imp * 1000 if imp else 0,
        cpc=sp / cl if cl else 0,
        ctr=cl / imp if imp else 0,
        convr=cv / cl if cl else 0,
        cpl=sp / cv if cv else 0,
        reply_rate=fr / cv if cv else 0,
        # video
        is_video=is_video,
        video_3s=v3s, video_plays=vpl, thruplays=thr,
        v_p25=p25, v_p50=p50, v_p75=p75, v_p95=p95, v_p100=p100,
        hook_rate=v3s / imp if imp else 0,
        hold_25=p25 / vpl if vpl else 0,
        hold_50=p50 / vpl if vpl else 0,
        hold_75=p75 / vpl if vpl else 0,
        hold_100=p100 / vpl if vpl else 0,
        thruplay_rate=thr / imp if imp else 0,
        cpt=(cpt_num / cpt_w if cpt_w else (sp / thr if thr else 0)),
        avg_time=(at_num / vpl if vpl else 0),
    )
    return a

def delta_label(cur, prev, lower_is_better=True):
    if prev == 0:
        return ("—", "flat")
    d = (cur - prev) / prev
    better = (d < 0) if lower_is_better else (d > 0)
    arrow = "▼" if d < 0 else "▲"
    cls = "good" if better else "bad"
    return (f"{arrow} {abs(d)*100:.0f}%", cls)

# ----------------------------- diagnóstico por regras fixas (fallback) -----------------------------
def legacy_diagnostics(W, P, cre_aggs, period_map, unidade, per_days):
    """Diagnóstico de gestor por REGRAS FIXAS (fallback determinístico).

    Roda quando não há analysis.json válido. Cada item: title, detail,
    action (str ou None), cert ('confirmado'|'validar'), prio (1|2|3).
    Comportamento idêntico ao histórico; não alterar texto nem lógica.
    """
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

    # 8. Conversas que não evoluem (funil de mensagens) — só se houver dado de 1ª resposta
    if W["first_reply"] > 0 and W["conv"] >= 5 and W["reply_rate"] < 0.7:
        items.append(dict(
            title=f"Parte das conversas não evolui (resposta em {pct(W['reply_rate'])})",
            detail=(f"Das {W['conv']:.0f} conversas iniciadas, {W['first_reply']:.0f} registraram primeira resposta. "
                    "Conversa iniciada que não recebe resposta é verba paga que morre no “oi”."),
            action=("Verificar com a recepção o tempo de resposta no WhatsApp: conversa parada é a maior "
                    "perda invisível e derruba o retorno real da verba."),
            cert="validar", prio=1))

    # 9. Bloqueios após contato — só se houver
    if W["blocks"] > 0:
        items.append(dict(
            title=f"{W['blocks']:.0f} bloqueio(s) após o contato",
            detail=("Pessoas que bloquearam a conversa depois do anúncio. Em volume, sinaliza desalinhamento "
                    "entre a promessa do criativo e o que a recepção entrega na abertura."),
            action="Se recorrer, alinhar a promessa do criativo com o primeiro contato da recepção.",
            cert="validar", prio=2))

    # 10. Frequência (fadiga real vs. público fresco) — só com dado de período
    freqs = [(n, period_map[n]) for n, _ in cre_aggs
             if n in period_map and period_map[n]["frequency"] > 0]
    if freqs:
        worst_fn, worst_fp = max(freqs, key=lambda t: t[1]["frequency"])
        fmax = worst_fp["frequency"]
        if fmax >= 2.5:
            items.append(dict(
                title=f"Frequência alta em «{worst_fn}» ({freqx(fmax)})",
                detail=("A mesma pessoa já viu o anúncio muitas vezes no período. Acima de ~2,5x costuma "
                        "indicar fadiga: o CTR cai e o CPM encarece."),
                action=f"Renovar o criativo «{worst_fn}» ou ampliar o público para diluir a repetição.",
                cert="validar", prio=2))
        elif W["cpm"] > 70 and fmax < 1.6:
            items.append(dict(
                title="Custo alto não é saturação: o público está fresco",
                detail=(f"A maior frequência do período é {freqx(fmax)} (cada pessoa viu pouco). Logo o CPM/CPP "
                        "alto vem de público estreito ou leilão concorrido, não de fadiga de criativo."),
                action=("Em vez de trocar criativo, ampliar ou abrir o público e revisar segmentação e "
                        "estratégia de lance."),
                cert="validar", prio=2))

    return items


# ----------------------------- motor de análise híbrido (analysis.json) -----------------------------
def load_analysis(path):
    """Lê e VALIDA um analysis.json escrito pelo agente.

    Retorna um dict normalizado (mesmo shape usado na renderização) ou None se
    o arquivo estiver ausente, ilegível ou fora do contrato. NUNCA propaga
    exceção: qualquer falha cai em None, e o relatório usa as regras fixas.
    """
    try:
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            return None

        raw_insights = data.get("insights")
        if raw_insights is None:
            raw_insights = []
        if not isinstance(raw_insights, list):
            return None
        insights = []
        for it in raw_insights:
            if not isinstance(it, dict):
                return None
            title = it.get("title")
            detail = it.get("detail")
            if not isinstance(title, str) or not title.strip():
                return None
            if not isinstance(detail, str) or not detail.strip():
                return None
            action = it.get("action")
            if action is not None and not isinstance(action, str):
                return None
            if isinstance(action, str) and not action.strip():
                action = None
            cert = it.get("cert", "validar")
            if cert not in ("confirmado", "validar"):
                cert = "validar"
            prio = it.get("prio", 2)
            if prio not in (1, 2, 3):
                prio = 2
            insights.append(dict(title=title, detail=detail, action=action,
                                 cert=cert, prio=prio, novo=bool(it.get("novo", False))))

        def _opt_str(key):
            v = data.get(key)
            if v is None:
                return None
            if not isinstance(v, str):
                raise ValueError(f"{key} não é texto")
            v = v.strip()
            return v or None

        return dict(
            trend_verdict=_opt_str("trend_verdict"),
            insights=insights,
            video_read=_opt_str("video_read"),
            manager_read=_opt_str("manager_read"),
            silence=bool(data.get("silence", False)))
    except Exception as _e:
        print("WARN: analysis.json ignorado (fallback para regras fixas):", _e)
        return None

# ----------------------------- load -----------------------------
ap = argparse.ArgumentParser()
ap.add_argument("data")
ap.add_argument("--outdir", default=".")
ap.add_argument("--recipient", default="")
ap.add_argument("--mode", default="weekly", choices=["weekly", "monthly"])
ap.add_argument("--period-data", default="", dest="period_data",
                help="JSON opcional com reach/frequency/cpp por criativo no período (nível agregado, sem data).")
ap.add_argument("--analysis", default="",
                help="JSON opcional de análise escrita pelo agente (analysis.json). "
                     "Ausente/inválido → regras fixas (fallback).")
args = ap.parse_args()

rows = json.loads(Path(args.data).read_text())
for r in rows:
    for k in ("spend", "impressions", "clicks", "conversations"):
        r[k] = float(r.get(k) or 0)
    r["leads"] = float(r.get("leads") or 0)
    # funil de mensagens (aditivos; ausentes em dados antigos → 0, nunca quebra)
    r["first_reply"] = float(r.get("first_reply") or 0)
    r["blocks"] = float(r.get("blocks") or 0)
    # metricas de video (ausentes em criativo estatico/dados antigos → 0, nunca quebra)
    for k in ("video_3s", "video_plays", "thruplays", "cost_per_thruplay",
              "v_p25", "v_p50", "v_p75", "v_p95", "v_p100", "video_avg_time"):
        r[k] = float(r.get(k) or 0)

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

# ----------------------------- ranking de campeões -----------------------------
_BIG = 9e9

def champion_score(c):
    """Score de campeão de criativo. MENOR é melhor (ordenamos ascendente).

    Critério: PURO desempenho de conversão (CPL = custo por conversa no
    WhatsApp), que é o que decide rentabilidade. Quanto menor, melhor.
      - CPL só é confiável com amostra suficiente: exigimos conv >= 3 para usar
        o CPL "cru". Com conv 1-2 o número é instável, então penalizamos por
        amostra baixa multiplicando o CPL por (1 + (3 - conv)): 2 conversas
        dobra, 1 conversa triplica. Sem conversa não pode ser campeão, recebe
        um score altíssimo (vai para o fim do ranking).

    Sem ajuste por vídeo: hook_rate/hold NÃO movem o campeão, senão um criativo
    com CPL pior poderia exibir o 🏆 na frente de quem converte melhor.
    Engajamento de vídeo é mostrado à parte, na seção "Vídeos".
    """
    conv = c["conv"]
    if conv >= 3 and c["cpl"] > 0:
        return c["cpl"]
    if conv > 0 and c["cpl"] > 0:
        return c["cpl"] * (1 + (3 - conv))   # penalidade por amostra baixa
    return _BIG                              # sem conversa: nunca é campeão

champ_ranked = sorted(cre_aggs, key=lambda x: champion_score(x[1]))
has_video = any(a["is_video"] for _n, a in cre_aggs)

# alcance/frequência por criativo (nível período, fetch agregado sem data) — OPCIONAL.
# Reach e frequência não são aditivos por dia (a mesma pessoa repete), por isso vêm
# de um fetch agregado à parte. Se o arquivo não existir/for inválido, o bloco some
# e o relatório segue normal — zero risco de crash.
period_map = {}
if args.period_data and Path(args.period_data).exists():
    try:
        for x in json.loads(Path(args.period_data).read_text()):
            n = x.get("ad_name")
            if not n:
                continue
            period_map[n] = dict(
                reach=float(x.get("reach") or 0),
                frequency=float(x.get("frequency") or 0),
                cpp=float(x.get("cpp") or 0))
    except Exception as _e:
        print("WARN: period-data ignorado:", _e)
        period_map = {}

def thousands(v):
    return f"{v:,.0f}".replace(",", ".")

def freqx(v):
    return f"{v:.2f}x".replace(".", ",")

def secs(v):  # tempo em segundos, ex.: 6,4s
    return f"{v:.1f}s".replace(".", ",")

gen_str = dt.datetime.now().strftime("%d/%m/%Y %H:%M")

# ----------------------------- diagnóstico de gestor (itens estruturados) -----------------------------
# Motor híbrido: se houver analysis.json válido (escrito pelo agente), usamos a
# análise dele; caso contrário, caímos nas REGRAS FIXAS (legacy_diagnostics).
# Em ambos os caminhos `items` tem o mesmo shape:
#   title, detail, action (str ou None), cert ('confirmado'|'validar'), prio (1|2|3)
# Números e tabelas continuam SEMPRE vindo do script (determinístico).
unidade = L["unit"]
analysis = load_analysis(args.analysis)
if analysis is not None:
    items = analysis["insights"]
else:
    items = legacy_diagnostics(W, P, cre_aggs, period_map, unidade, per_days)

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
.badge.novo { background: #e7eefb; color: #1F3A5F; }
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

def novo_badge(it):
    return ' <span class="badge novo">Novo</span>' if it.get("novo") else ""

diag_html = ""
for it in items:
    cls = "diag" if it["cert"] == "confirmado" else "diag validar"
    diag_html += (f'<div class="{cls}"><p><span class="dt">{esc(it["title"])}</span> '
                  f'{cert_badge(it["cert"])}{novo_badge(it)}<br>'
                  f'<span class="dd">{esc(it["detail"])}</span></p></div>')

# Silêncio estratégico: análise válida pediu silence e não trouxe insights.
# Mostramos uma linha mínima NO LUGAR da lista; as tabelas seguem normais.
if analysis is not None and analysis.get("silence") and not items:
    diag_html = ('<div class="diag"><p><span class="dd">Sem novos pontos estratégicos '
                 'neste período; números dentro do padrão.</span></p></div>')

# Veredito de tendência (motor híbrido): destaque no topo da análise, se houver.
trend_html = ""
if analysis is not None and analysis.get("trend_verdict"):
    trend_html = ('<div class="lead"><span class="tag">Tendência da conta</span>'
                  f'<p>{esc(analysis["trend_verdict"])}</p></div>')

# Legenda de badges só faz sentido quando há itens com badge.
diag_legend = legend if items else ""

acts = sorted([it for it in items if it.get("action")], key=lambda it: it["prio"])
task_html = ""
for it in acts:
    task_html += (f'<div class="task"><span class="chk">&#9744;</span>'
                  f'<span class="prio p{it["prio"]}">{PRIO_LBL[it["prio"]]}</span>'
                  f'{esc(it["action"])} {cert_badge(it["cert"])}</div>')
if not task_html:
    task_html = '<p class="small">Sem ações prioritárias neste período.</p>'

# Leitura do vídeo campeão (motor híbrido): renderizada junto do bloco Vídeos.
video_read_html = ""
if analysis is not None and analysis.get("video_read"):
    video_read_html = ('<div class="lead"><span class="tag">Leitura do vídeo campeão</span>'
                       f'<p>{esc(analysis["video_read"])}</p></div>')

# funil de mensagens (linha abaixo do consolidado) — só com dado de 1ª resposta
funnel_line = ""
if W["first_reply"] > 0:
    blk_txt = f' · {W["blocks"]:.0f} bloqueio(s)' if W["blocks"] > 0 else ""
    funnel_line = (f'<p class="small">Funil do período: {W["clicks"]:.0f} cliques → '
                   f'{W["conv"]:.0f} conversas → {W["first_reply"]:.0f} com primeira resposta '
                   f'({pct(W["reply_rate"])} das conversas){blk_txt}.</p>')

# tabela de alcance e frequência por criativo — só com dado de período
reach_html = ""
if period_map:
    rows_r = ""
    for n, _a in cre_aggs:
        pm = period_map.get(n)
        if not pm or pm["reach"] <= 0:
            continue
        rows_r += (f'<tr><td>{n}</td><td>{thousands(pm["reach"])}</td>'
                   f'<td>{freqx(pm["frequency"])}</td><td>{brl(pm["cpp"])}</td></tr>')
    if rows_r:
        reach_html = ('<table><thead><tr><th>Criativo</th><th>Alcance</th>'
                      '<th>Frequência</th><th>Custo/mil pessoas</th></tr></thead><tbody>'
                      + rows_r + '</tbody></table>'
                      '<p class="small">Alcance = pessoas únicas atingidas. Frequência = quantas vezes, '
                      'em média, cada pessoa viu o anúncio. CPP = custo por mil pessoas alcançadas '
                      '(diferente do CPM, que conta impressões repetidas).</p>')

# ----------------------------- ranking de criativos (sempre) -----------------------------
# Tabela de TODOS os criativos ordenada por performance de conversão (champion_score).
# 🏆 marca o campeão, ⚠️ marca o pior. Frequência só aparece se houver dado de período.
has_freq = bool(period_map) and any(
    (period_map.get(n) or {}).get("frequency", 0) > 0 for n, _a in cre_aggs)
rank_head = ('<tr><th>Criativo</th><th>Gasto</th><th>Conversas</th><th>CPL</th>'
             '<th>CTR</th>' + ('<th>Frequência</th>' if has_freq else '') + '</tr>')
rank_rows = ""
n_ranked = len(champ_ranked)
for i, (n, a) in enumerate(champ_ranked):
    mark, cls = "", ""
    if n_ranked > 1 and i == 0 and a["conv"] > 0:
        mark, cls = "🏆 ", "win"
    elif n_ranked > 1 and i == n_ranked - 1:
        mark, cls = "⚠️ ", "lose"
    freq_td = ""
    if has_freq:
        pm = period_map.get(n) or {}
        freq_td = f'<td>{freqx(pm["frequency"]) if pm.get("frequency") else "—"}</td>'
    rank_rows += (f'<tr class="{cls}"><td>{mark}{n}</td><td>{brl(a["spend"])}</td>'
                  f'<td>{a["conv"]:.0f}</td><td>{brl(a["cpl"]) if a["conv"] else "—"}</td>'
                  f'<td>{pct2(a["ctr"])}</td>{freq_td}</tr>')
ranking_html = (f'<table><thead>{rank_head}</thead><tbody>{rank_rows}</tbody></table>'
                '<p class="small">Ordenado por desempenho de conversão (CPL, com ajuste para '
                'amostra pequena). 🏆 melhor criativo do período, ⚠️ pior. CPL = Gasto ÷ Conversas.</p>')

# ----------------------------- Vídeo vs Imagem (consolidado por formato) -----------------------------
# Soma os criativos por FORMATO (Vídeo se is_video, senão Imagem) e compara o que cada
# formato entrega. SÓ renderiza quando há os dois formatos no ar; com um só não há comparação.
fmt_tot = {"Vídeo": dict(spend=0.0, conv=0.0, imp=0.0, clicks=0.0, n=0),
           "Imagem": dict(spend=0.0, conv=0.0, imp=0.0, clicks=0.0, n=0)}
for _n, a in cre_aggs:
    key = "Vídeo" if a["is_video"] else "Imagem"
    f = fmt_tot[key]
    f["spend"] += a["spend"]; f["conv"] += a["conv"]
    f["imp"] += a["impressions"]; f["clicks"] += a["clicks"]; f["n"] += 1

format_html = ""
if fmt_tot["Vídeo"]["n"] > 0 and fmt_tot["Imagem"]["n"] > 0:  # precisa dos dois formatos
    spend_tot = fmt_tot["Vídeo"]["spend"] + fmt_tot["Imagem"]["spend"]
    fmt_rows = ""
    for key in ("Vídeo", "Imagem"):
        f = fmt_tot[key]
        cpl = f["spend"] / f["conv"] if f["conv"] else 0
        ctr = f["clicks"] / f["imp"] if f["imp"] else 0
        share = f["spend"] / spend_tot if spend_tot else 0
        fmt_rows += (f'<tr><td>{key}</td><td>{brl(f["spend"])}</td><td>{pct(share)}</td>'
                     f'<td>{f["conv"]:.0f}</td><td>{brl(cpl) if f["conv"] else "—"}</td>'
                     f'<td>{pct2(ctr)}</td></tr>')
    format_html = ('<table><thead><tr><th>Formato</th><th>Gasto</th><th>% da verba</th>'
                   '<th>Conversas</th><th>CPL</th><th>CTR</th></tr></thead><tbody>'
                   + fmt_rows + '</tbody></table>'
                   '<p class="small">Consolidado por formato de criativo no período. % da verba = gasto '
                   'do formato ÷ gasto total. CPL = Gasto ÷ Conversas. Use para decidir alocação de verba '
                   'entre vídeo e imagem.</p>')

# campeão do período (melhor criativo do ranking, se converteu) — usado no snapshot e no e-mail
champ_name, champ_cpl = None, 0.0
if champ_ranked and champ_ranked[0][1]["conv"] > 0:
    champ_name = champ_ranked[0][0]
    champ_cpl = champ_ranked[0][1]["cpl"]

# ----------------------------- vídeos (só se houver criativo de vídeo) -----------------------------
video_html = ""
if has_video:
    vid_rows = ""
    for n, a in champ_ranked:
        if not a["is_video"]:
            continue
        vid_rows += (f'<tr><td>{n}</td><td>{pct2(a["hook_rate"])}</td>'
                     f'<td>{pct(a["hold_25"])}</td><td>{pct(a["hold_50"])}</td>'
                     f'<td>{pct(a["hold_75"])}</td><td>{pct(a["hold_100"])}</td>'
                     f'<td>{pct2(a["thruplay_rate"])}</td>'
                     f'<td>{brl(a["cpt"]) if a["thruplays"] else "—"}</td>'
                     f'<td>{secs(a["avg_time"]) if a["avg_time"] else "—"}</td>'
                     f'<td>{brl(a["cpl"]) if a["conv"] else "—"}</td></tr>')
    video_html = (
        '<table><thead><tr><th>Criativo</th><th>Hook rate</th><th>Ret. 25%</th>'
        '<th>Ret. 50%</th><th>Ret. 75%</th><th>Ret. 100%</th><th>Thruplay</th>'
        '<th>Custo/thru</th><th>Tempo médio</th><th>CPL</th></tr></thead><tbody>'
        + vid_rows + '</tbody></table>'
        '<p class="small">Hook rate = visualizações de 3s ÷ impressões (quanto o início prende). '
        'Retenção = % de quem deu play e assistiu até 25/50/75/100%. Thruplay = assistiu 15s ou '
        'o vídeo todo, sobre impressões. Custo/thru = custo por thruplay. CPL ao lado para ligar '
        'engajamento de vídeo a conversa no WhatsApp.</p>')

# montagem das seções com numeração dinâmica (evita números fixos quando há blocos opcionais)
def secnum(k):
    return f'<span class="num">{k:02d}</span>'

sections, k = [], 1
sections.append(f'<h2>{secnum(k)}Consolidado e por criativo</h2>'
                f'<table><thead>{head}</thead><tbody>{cons}{cre_html}</tbody></table>'
                '<p class="small">CPL = Investimento ÷ Conversas (WhatsApp). % Conv = Conversas ÷ Cliques.</p>'
                + funnel_line); k += 1
if reach_html:
    sections.append(f'<h2>{secnum(k)}Alcance e frequência</h2>' + reach_html); k += 1
sections.append(f'<h2>{secnum(k)}Ranking de criativos</h2>' + ranking_html); k += 1
# Vídeo vs Imagem: só quando os dois formatos estão no ar (há comparação a fazer).
if format_html:
    sections.append(f'<h2>{secnum(k)}Vídeo vs Imagem</h2>' + format_html); k += 1
# Vídeos: aparece se houver criativo de vídeo OU leitura de vídeo escrita pelo agente.
if video_html or video_read_html:
    sections.append(f'<h2>{secnum(k)}Vídeos</h2>' + video_read_html + video_html); k += 1
if wow_html:
    sections.append(f'<h2>{secnum(k)}{L["comp_title"]}</h2>' + wow_html); k += 1
sections.append(f'<h2>{secnum(k)}Diagnóstico — visão de gestor de tráfego</h2>'
                + trend_html + diag_legend + diag_html); k += 1
# Leitura de performance do gestor de ads (preenchida só no mensal): seção própria.
if analysis is not None and analysis.get("manager_read"):
    sections.append(f'<h2>{secnum(k)}Leitura de performance do gestor</h2>'
                    f'<p>{esc(analysis["manager_read"])}</p>'); k += 1
sections.append(f'<h2>{secnum(k)}Plano de ação priorizado</h2>' + task_html); k += 1
body_sections = "\n".join(sections)

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
{body_sections}
</body></html>"""

outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
# sempre grava o HTML completo do relatorio (fallback de corpo de e-mail sem dependencia de sistema)
(outdir / "report.html").write_text(html)

# snapshot do período atual para alimentar o histórico (Evolução em execuções futuras).
# Fail-open: qualquer dado ausente vira 0/null; nunca quebra o relatório.
try:
    vvi = None
    if fmt_tot["Vídeo"]["n"] > 0 and fmt_tot["Imagem"]["n"] > 0:
        def _fmt_snap(key):
            f = fmt_tot[key]
            return dict(spend=round(f["spend"], 2), conv=int(f["conv"]),
                        cpl=round(f["spend"] / f["conv"], 2) if f["conv"] else 0)
        vvi = {"video": _fmt_snap("Vídeo"), "imagem": _fmt_snap("Imagem")}
    history_row = {
        "periodo": L["subj"], "modo": args.mode, "gerado_em": gen_str,
        "spend": round(W["spend"], 2), "conversas": int(W["conv"]),
        "cpl": round(W["cpl"], 2), "cpc": round(W["cpc"], 2), "cpm": round(W["cpm"], 2),
        "ctr": round(W["ctr"], 6), "reply_rate": round(W["reply_rate"], 6),
        "blocks": int(W["blocks"]),
        "n_criativos": len(cre_aggs),
        "n_video": fmt_tot["Vídeo"]["n"], "n_imagem": fmt_tot["Imagem"]["n"],
        "campeao": champ_name, "campeao_cpl": round(champ_cpl, 2),
        "video_vs_imagem": vvi,
        "trend_verdict": (analysis.get("trend_verdict") or "") if analysis else "",
        "insights_titulos": [it["title"] for it in items],
        "acao_principal": (acts[0]["action"] if acts else ""),
    }
    (outdir / "history_row.json").write_text(
        json.dumps(history_row, ensure_ascii=False, indent=2))
except Exception as _ex:
    print("WARN: history_row.json nao gravado:", _ex)
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
if W["first_reply"] > 0:
    body_rows += (f"<tr><td><b>Conversas c/ resposta</b></td>"
                  f"<td>{W['first_reply']:.0f} ({pct(W['reply_rate'])})</td></tr>")
def cert_txt(c): return "Confirmado" if c == "confirmado" else "Validar"
email_acts = "".join(
    f'<li style="margin-bottom:6px"><b>{PRIO_LBL[it["prio"]]}</b> · {esc(it["action"])} '
    f'<span style="font-size:11px;color:{"#2c6e2c" if it["cert"]=="confirmado" else "#8a5d00"}">'
    f'[{cert_txt(it["cert"])}]</span></li>' for it in acts[:4])

# resumo executivo: tudo que importa LEGÍVEL sem abrir o PDF. Todo texto vindo do
# agente (trend_verdict, títulos de insight, ação) passa por esc().
summary_bits = []
if analysis is not None and analysis.get("trend_verdict"):
    summary_bits.append('<p style="margin:0 0 10px;background:#fbf7ea;border-left:4px solid #C9A227;'
                        'padding:8px 12px"><b>Tendência:</b> '
                        f'{esc(analysis["trend_verdict"])}</p>')
top_titles = [it["title"] for it in items][:3]
if top_titles:
    lis = "".join(f'<li style="margin-bottom:4px">{esc(t)}</li>' for t in top_titles)
    summary_bits.append('<p style="margin:0 0 4px"><b>Principais pontos:</b></p>'
                        f'<ul style="margin:0 0 10px;padding-left:18px">{lis}</ul>')
if champ_name:
    summary_bits.append(f'<p style="margin:0 0 10px"><b>Campeão:</b> {esc(champ_name)} '
                        f'· CPL {brl(champ_cpl)}</p>')
if format_html:
    _v, _i = fmt_tot["Vídeo"], fmt_tot["Imagem"]
    _vcpl = _v["spend"] / _v["conv"] if _v["conv"] else 0
    _icpl = _i["spend"] / _i["conv"] if _i["conv"] else 0
    summary_bits.append('<p style="margin:0 0 10px"><b>Formato:</b> '
                        f'Vídeo CPL {brl(_vcpl) if _v["conv"] else "—"} vs '
                        f'Imagem CPL {brl(_icpl) if _i["conv"] else "—"}</p>')
exec_summary = "".join(summary_bits)

body_html = f"""<div style="font-family:Helvetica,Arial,sans-serif;color:#1d2530;font-size:14px;line-height:1.55">
<p>Olá,</p>
<p>Segue o resumo de Meta Ads da Tekoan referente a <b>{period_str}</b> {L["per_suffix"]}. PDF completo em anexo.</p>
{exec_summary}
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
