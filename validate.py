#!/usr/bin/env python3
"""Guardas de conteudo executadas ANTES do envio do relatorio.

Por que aqui e nao no verificador de entrega: aquele roda ~1h DEPOIS do envio.
Se o numero saiu errado, o e-mail ja esta na caixa do cliente e nenhum veredito
desfaz isso. Estas guardas rodam dentro de run_from_json.py, antes de gerar o
relatorio, e reusam o caminho fail-closed que ja existia: erro vira SystemExit,
o relatorio NAO e enviado e o alerta [Tekoan][FALHA] vai so para ALERT_TO.

Dois niveis:
  ERRO  -> bloqueia o envio (janela errada ou dado impossivel)
  AVISO -> nao bloqueia; sai no log e num e-mail curto so para ALERT_TO

A guarda mais importante e a da JANELA. generate_report.py deriva o periodo
reportado da MAIOR data presente nos dados (`maxd`): no mensal o mes de maxd,
no semanal os 7 dias que terminam em maxd. Ou seja, o relatorio nao tem como
perceber sozinho que esta reportando o periodo errado — ele passa a chamar de
"mes fechado" o que quer que tenha chegado. Um closed_creatives.json velho
sobrando no diretorio, um fetch com date_from/date_to trocados ou um re-disparo
mal parametrizado saem bonitos e coerentes, so que do periodo errado. Aqui a
janela esperada e RECALCULADA da data de execucao pelas mesmas funcoes que o
build usa (week_dates / month_dates), entao a checagem e independente do dado.

Override legitimo: EXPECTED_END=YYYY-MM-DD, para re-enviar um periodo antigo de
proposito. Nao existe chave para desligar as guardas — se existisse, ela acabaria
colada no prompt da routine e as guardas morreriam em silencio.
"""
import os
import datetime as dt

from build_weekly import week_dates
from build_monthly import month_dates

# campos numericos que run_from_json.py monta em cada row
NUMERIC = ("spend", "impressions", "clicks", "conversations", "leads",
           "first_reply", "blocks", "video_3s", "video_plays", "thruplays",
           "cost_per_thruplay", "v_p25", "v_p50", "v_p75", "v_p95", "v_p100",
           "video_avg_time")

# curva de retencao de video: e monotona nao-crescente por definicao
CURVE = ("v_p25", "v_p50", "v_p75", "v_p95", "v_p100")

# desvio de investimento contra o periodo anterior que vira AVISO (nao bloqueia).
# Comeca largo de proposito: alarme falso todo mes treina a gente a ignorar alerta.
SPEND_DEV_MAX = float(os.environ.get("SPEND_DEV_MAX", "0.7"))


def today_date():
    """Data de execucao. TODAY=YYYY-MM-DD sobrescreve (mesma convencao do build)."""
    t = os.environ.get("TODAY", "").strip()
    return dt.date.fromisoformat(t) if t else dt.date.today()


def expected_end(mode, today=None):
    """Ultimo dia do periodo fechado que ESTE run deveria reportar.

    Reusa week_dates/month_dates do build para nao criar uma segunda fonte de
    verdade das datas. Ambas sao ancoradas em calendario, entao um re-disparo
    manual em outro dia continua apontando para o mesmo periodo fechado.
    """
    override = os.environ.get("EXPECTED_END", "").strip()
    if override:
        return dt.date.fromisoformat(override)
    t = today or today_date()
    return month_dates(t)[1] if mode == "monthly" else week_dates(t)[1]


def _d(r):
    return dt.date.fromisoformat(r["date"])


def split(rows, mode, maxd):
    """Separa periodo atual x anterior.

    ESPELHO de generate_report.py (bloco `if args.mode == "monthly"`). Se o
    recorte mudar la, tem que mudar aqui junto — senao a guarda valida um
    recorte e o relatorio publica outro.
    """
    if mode == "monthly":
        y, m = maxd.year, maxd.month
        pm = (y - 1, 12) if m == 1 else (y, m - 1)
        cur = [r for r in rows if (_d(r).year, _d(r).month) == (y, m)]
        prev = [r for r in rows if (_d(r).year, _d(r).month) == pm]
    else:
        cut = maxd - dt.timedelta(days=6)
        prev_cut = cut - dt.timedelta(days=7)
        cur = [r for r in rows if _d(r) >= cut]
        prev = [r for r in rows if prev_cut <= _d(r) < cut]
    return cur, prev


def _key(r):
    """Identidade da peca: creative_id quando houver, senao ad_name — mesmo
    criterio de desempate que o gerador usa no ranking."""
    cid = r.get("creative_id")
    return ("cid", str(cid)) if cid not in (None, "", 0) else ("name", r.get("ad_name"))


def validate(rows, mode, exp_end):
    """Devolve (erros, avisos). Erro bloqueia o envio; aviso so avisa."""
    errors, warns = [], []

    dates = sorted({r["date"] for r in rows})
    if not dates:
        return ["Sem linhas com data — nada a reportar."], warns
    maxd = dt.date.fromisoformat(dates[-1])

    # ---- G1 JANELA: a guarda que o relatorio nao consegue fazer por si -------
    if maxd != exp_end:
        errors.append(
            f"Janela errada: os dados terminam em {maxd.isoformat()}, mas o periodo "
            f"fechado que este run deveria reportar termina em {exp_end.isoformat()}. "
            f"Causa tipica: arquivo de fetch de outro periodo sobrando no diretorio, "
            f"ou date_from/date_to errados no get_data. "
            f"(Para reenviar um periodo antigo de proposito: EXPECTED_END={maxd.isoformat()}.)")

    cur, prev = split(rows, mode, maxd)

    # ---- G2 o periodo reportado precisa existir e ter investimento ----------
    if not cur:
        errors.append("Nenhuma linha caiu no periodo reportado.")
        return errors, warns

    spend = sum(r["spend"] for r in cur)
    if spend <= 0:
        errors.append(f"Investimento total do periodo e {spend:.2f} — relatorio "
                      f"de spend zero nao vai para cliente.")

    # ---- G3 dupla contagem: mesma peca no mesmo dia duas vezes -------------
    # Acontece quando dois fetches parciais sao concatenados no mesmo arquivo.
    # Soma tudo em dobro e o relatorio sai coerente, so que com o dobro do valor.
    seen, dups = set(), set()
    for r in cur:
        k = (_key(r), r["date"])
        if k in seen:
            dups.add(f'{r.get("ad_name")} em {r["date"]}')
        seen.add(k)
    if dups:
        errors.append("Linha duplicada (mesma peca, mesmo dia) — o total sairia "
                      "dobrado: " + "; ".join(sorted(dups)[:5]))

    # ---- G4 aritmetica impossivel -----------------------------------------
    negs, click_gt_imp, imp_zero = [], [], []
    for r in cur:
        for k in NUMERIC:
            if (r.get(k) or 0) < 0:
                negs.append(f'{r.get("ad_name")}.{k}={r[k]}')
        if r["clicks"] > r["impressions"]:
            click_gt_imp.append(f'{r.get("ad_name")} ({r["clicks"]} cliques / '
                                f'{r["impressions"]} impressoes)')
        if r["impressions"] == 0 and r["spend"] > 0:
            imp_zero.append(f'{r.get("ad_name")} (R$ {r["spend"]:.2f} sem impressao)')
    if negs:
        errors.append("Valor negativo: " + "; ".join(negs[:5]))
    if click_gt_imp:
        errors.append("Mais cliques que impressoes: " + "; ".join(click_gt_imp[:5]))
    if imp_zero:
        errors.append("Gasto sem impressao: " + "; ".join(imp_zero[:5]))

    # ---- AVISOS -----------------------------------------------------------
    if not prev:
        warns.append("Sem periodo anterior nos dados — o relatorio sai sem comparativo.")
    else:
        pspend = sum(r["spend"] for r in prev)
        if pspend > 0:
            if abs(spend - pspend) < 0.005:
                warns.append(
                    f"Investimento identico ao periodo anterior (R$ {spend:.2f}). "
                    f"Coincidencia exata e improvavel — checar se o mesmo fetch foi "
                    f"usado para os dois periodos.")
            dev = (spend - pspend) / pspend
            if abs(dev) > SPEND_DEV_MAX:
                warns.append(
                    f"Investimento variou {dev*100:+.0f}% contra o periodo anterior "
                    f"(R$ {pspend:.2f} -> R$ {spend:.2f}). Pode ser real, mas tambem "
                    f"e a cara de um fetch que voltou pela metade.")

    for r in cur:
        curve = [r.get(k) or 0 for k in CURVE]
        if any(curve) and any(a < b for a, b in zip(curve, curve[1:])):
            warns.append(
                f'Curva de retencao de "{r.get("ad_name")}" sobe em algum ponto '
                f'({" >= ".join(str(int(v)) for v in curve)}) — deveria so cair.')

    return errors, warns
