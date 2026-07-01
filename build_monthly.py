#!/usr/bin/env python3
"""Monta windsor.json + windsor_period.json do relatorio MENSAL a partir de
fetches AGREGADOS (sem o campo date) do Windsor.

Motivo: a versao antiga da routine mandava o agente puxar last_70d (dado
DIARIO, ~250 linhas / ~40KB) e ECOAR o JSON inteiro num arquivo. Esse eco de
payload grande e o passo que trava o agente headless (o relatorio nunca sai e
nem o alerta de FALHA dispara). O mensal so precisa de TOTAIS por criativo do
mes fechado + TOTAL do mes anterior (comparativo). Puxando agregado (sem date),
o Windsor ja devolve 1 linha por criativo: ~10-25 linhas, o agente escreve sem
travar.

Fluxo na routine:
  1) `python3 build_monthly.py --print-dates`  -> imprime as 4 datas do mes
     fechado e do mes anterior (fonte unica de verdade das datas).
  2) o agente faz 3 get_data AGREGADOS (sem date) com essas datas e salva os
     resultados crus (objeto {"result":[...]}) em:
        closed_creatives.json  (mes fechado, por criativo: ad_name, creative_id,
            spend, impressions, clicks, e os campos de conversa/lead/1a-resposta/bloqueio)
        prev_total.json        (mes anterior, TOTAL: sem ad_name/creative_id)
        closed_reach.json      (mes fechado, por criativo: ad_name, creative_id,
            reach, frequency, cpp)
  3) `python3 build_monthly.py`  -> escreve windsor.json + windsor_period.json.
  4) run_from_json.py (REPORT_MODE=monthly) gera e envia.

Datas: derivadas de hoje (dia 1 do mes). Override para teste via env TODAY=YYYY-MM-DD.
Fail-safe: se prev_total/closed_reach faltarem, segue so com o mes fechado
(comparativo/alcance simplesmente nao aparecem; o relatorio SAI).
"""
import os, sys, json, datetime as dt
from pathlib import Path

MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
         "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

# campos de conversao/funil (nomes crus do Windsor) — copiados 1:1 para windsor.json,
# pois run_from_json.py e quem mapeia para conversations/leads/first_reply/blocks.
FUNNEL = ["actions_onsite_conversion_messaging_conversation_started_7d",
          "actions_lead", "actions_offsite_conversion_fb_pixel_lead",
          "actions_onsite_conversion_messaging_first_reply",
          "actions_onsite_conversion_messaging_block"]


def month_dates(today):
    first_this = today.replace(day=1)
    closed_end = first_this - dt.timedelta(days=1)      # ultimo dia do mes fechado
    closed_start = closed_end.replace(day=1)
    prev_end = closed_start - dt.timedelta(days=1)       # ultimo dia do mes anterior
    prev_start = prev_end.replace(day=1)
    return closed_start, closed_end, prev_start, prev_end


def rows_of(path):
    """Le um arquivo de fetch cru e devolve a lista de linhas. Aceita
    {"result":[...]}, {"data":[...]} ou lista. Ausente/invalido -> []."""
    try:
        p = Path(path)
        if not p.exists():
            return []
        d = json.loads(p.read_text())
        if isinstance(d, dict):
            return d.get("result") or d.get("data") or []
        return d if isinstance(d, list) else []
    except Exception as e:
        print(f"WARN: {path} ignorado: {e}", file=sys.stderr)
        return []


def num(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def main():
    today = (dt.date.fromisoformat(os.environ["TODAY"])
             if os.environ.get("TODAY") else dt.date.today())
    cs, ce, ps, pe = month_dates(today)

    if "--print-dates" in sys.argv:
        print(f"CLOSED_START={cs.isoformat()}")
        print(f"CLOSED_END={ce.isoformat()}")
        print(f"PREV_START={ps.isoformat()}")
        print(f"PREV_END={pe.isoformat()}")
        print(f"CLOSED_LABEL={MESES[ce.month-1]}/{ce.year}")
        print(f"PREV_LABEL={MESES[pe.month-1]}/{pe.year}")
        return

    closed = rows_of("closed_creatives.json")
    closed = [x for x in closed if x.get("ad_name")]
    if not closed:
        raise SystemExit("closed_creatives.json vazio/sem ad_name — nao da para montar o relatorio mensal.")

    result = []
    # mes fechado: 1 linha por criativo, carimbada com o ultimo dia do mes fechado
    for x in closed:
        row = {"date": ce.isoformat(), "ad_name": x.get("ad_name"),
               "creative_id": x.get("creative_id"),
               "spend": num(x.get("spend")), "impressions": num(x.get("impressions")),
               "clicks": num(x.get("clicks"))}
        for k in FUNNEL:
            row[k] = num(x.get(k))
        result.append(row)

    # mes anterior: 1 linha consolidada (TOTAL), so alimenta o comparativo.
    prev = rows_of("prev_total.json")
    if prev:
        pt = prev[0]
        row = {"date": pe.isoformat(), "ad_name": f"{MESES[pe.month-1]} (consolidado)",
               "creative_id": None,
               "spend": num(pt.get("spend")), "impressions": num(pt.get("impressions")),
               "clicks": num(pt.get("clicks"))}
        for k in FUNNEL:
            row[k] = num(pt.get(k))
        result.append(row)
    else:
        print("WARN: prev_total.json ausente — relatorio sai sem comparativo mes anterior.", file=sys.stderr)

    Path("windsor.json").write_text(json.dumps({"result": result}, ensure_ascii=False))

    # alcance/frequencia por criativo (mes fechado) — mantem creative_id p/ o join correto.
    reach = [x for x in rows_of("closed_reach.json") if x.get("ad_name")]
    if reach:
        per = [{"ad_name": x.get("ad_name"), "creative_id": x.get("creative_id"),
                "reach": num(x.get("reach")), "frequency": num(x.get("frequency")),
                "cpp": num(x.get("cpp"))} for x in reach]
        Path("windsor_period.json").write_text(json.dumps(per, ensure_ascii=False))

    print(f"OK build_monthly: mes fechado {MESES[ce.month-1]}/{ce.year} "
          f"({cs} a {ce}), {len(closed)} criativos; comparativo {MESES[pe.month-1]}/{pe.year} "
          f"{'sim' if prev else 'NAO'}; alcance {'sim' if reach else 'NAO'}.")


if __name__ == "__main__":
    main()
