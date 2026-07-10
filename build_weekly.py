#!/usr/bin/env python3
"""Monta windsor.json + windsor_period.json do relatorio SEMANAL a partir de
fetches AGREGADOS (sem o campo date) do Windsor.

Motivo (identico ao mensal, ver build_monthly.py): a versao antiga da routine
mandava o agente puxar last_14d DIARIO (dia x criativo). Com os criativos de
video+imagem no ar isso virou ~80 linhas x ~21 campos (~48KB) e o agente
headless TRAVA ao ECOAR esse JSON grande num arquivo — o relatorio nunca sai e
nem o alerta de FALHA dispara (mesma falha ja corrigida no mensal em 01/07).
A semana reportada so precisa de TOTAIS por criativo (7 dias) + TOTAL da semana
anterior (comparativo). Puxando AGREGADO (sem date), o Windsor devolve 1 linha
por criativo (~10 linhas) e ja consolida corretamente ate as metricas de razao
(cost_per_thruplay, frequency, cpp, video_avg_time), que NAO sao aditivas por dia.

Fluxo na routine:
  1) `python3 build_weekly.py --print-dates` -> imprime as 4 datas. Semana
     reportada = ultimos 7 dias TERMINANDO ONTEM (mesma janela do last_14d, que
     nao inclui hoje); semana anterior = os 7 dias imediatamente antes.
  2) o agente faz 3 get_data AGREGADOS (sem date, usando date_from/date_to) e
     salva os resultados crus (objeto {"result":[...]}) em:
        cur_creatives.json  (semana reportada, por criativo: ad_name, creative_id,
            spend, impressions, clicks, funil de mensagens e metricas de video)
        prev_total.json     (semana anterior, TOTAL: sem ad_name/creative_id,
            so spend/impressions/clicks + funil — alimenta a comparacao WoW)
        cur_reach.json      (semana reportada, por criativo: ad_name, creative_id,
            reach, frequency, cpp)
  3) `python3 build_weekly.py` -> escreve windsor.json + windsor_period.json.
  4) run_from_json.py (REPORT_MODE=weekly, o default) gera e envia.

Carimbo de datas: o gerador (generate_report.py, modo weekly) separa "esta
semana" de "semana anterior" pela DATA das linhas (cut = maxd-6). Por isso os
criativos da semana reportada sao carimbados com CUR_END (a data maxima, define
a janela reportada) e o total anterior com PREV_END (que cai na janela anterior).

Datas derivadas de hoje. Override para teste via env TODAY=YYYY-MM-DD.
Fail-safe: se prev_total/cur_reach faltarem, segue so com a semana reportada
(comparativo/alcance simplesmente nao aparecem; o relatorio SAI).
"""
import os, sys, json, datetime as dt
from pathlib import Path

# campos de conversao/funil (nomes crus do Windsor) — copiados 1:1 para windsor.json,
# pois run_from_json.py e quem mapeia para conversations/leads/first_reply/blocks.
FUNNEL = ["actions_onsite_conversion_messaging_conversation_started_7d",
          "actions_lead", "actions_offsite_conversion_fb_pixel_lead",
          "actions_onsite_conversion_messaging_first_reply",
          "actions_onsite_conversion_messaging_block"]

# metricas de video (nomes crus do Windsor). Agregadas pelo Windsor no fetch sem
# date: contagens (3s, plays, thruplays, p25..p100) somam; cost_per_thruplay e
# video_avg_time ja vem consolidadas para a janela. run_from_json.py mapeia.
VIDEO = ["actions_video_view", "video_play_actions_video_view",
         "video_thruplay_watched_actions_video_view", "cost_per_thruplay_video_view",
         "video_p25_watched_actions_video_view", "video_p50_watched_actions_video_view",
         "video_p75_watched_actions_video_view", "video_p95_watched_actions_video_view",
         "video_p100_watched_actions_video_view", "video_avg_time_watched_actions_video_view"]


def week_dates(today):
    cur_end = today - dt.timedelta(days=1)          # ontem (last_14d termina ontem)
    cur_start = cur_end - dt.timedelta(days=6)      # janela reportada = 7 dias
    prev_end = cur_start - dt.timedelta(days=1)     # semana anterior = os 7 antes
    prev_start = prev_end - dt.timedelta(days=6)
    return cur_start, cur_end, prev_start, prev_end


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
    cs, ce, ps, pe = week_dates(today)

    if "--print-dates" in sys.argv:
        print(f"CUR_START={cs.isoformat()}")
        print(f"CUR_END={ce.isoformat()}")
        print(f"PREV_START={ps.isoformat()}")
        print(f"PREV_END={pe.isoformat()}")
        print(f"CUR_LABEL={cs.strftime('%d/%m')} a {ce.strftime('%d/%m/%Y')}")
        print(f"PREV_LABEL={ps.strftime('%d/%m')} a {pe.strftime('%d/%m/%Y')}")
        return

    cur = rows_of("cur_creatives.json")
    cur = [x for x in cur if x.get("ad_name")]
    if not cur:
        raise SystemExit("cur_creatives.json vazio/sem ad_name — nao da para montar o relatorio semanal.")

    result = []
    # semana reportada: 1 linha por criativo, carimbada com CUR_END (define a janela).
    for x in cur:
        row = {"date": ce.isoformat(), "ad_name": x.get("ad_name"),
               "creative_id": x.get("creative_id"),
               "spend": num(x.get("spend")), "impressions": num(x.get("impressions")),
               "clicks": num(x.get("clicks"))}
        for k in FUNNEL:
            row[k] = num(x.get(k))
        for k in VIDEO:
            row[k] = num(x.get(k))
        result.append(row)

    # semana anterior: 1 linha consolidada (TOTAL), carimbada com PREV_END, so
    # alimenta o comparativo WoW (spend/impressions/clicks/conversas).
    prev = rows_of("prev_total.json")
    if prev:
        pt = prev[0]
        row = {"date": pe.isoformat(), "ad_name": "Semana anterior (consolidado)",
               "creative_id": None,
               "spend": num(pt.get("spend")), "impressions": num(pt.get("impressions")),
               "clicks": num(pt.get("clicks"))}
        for k in FUNNEL:
            row[k] = num(pt.get(k))
        result.append(row)
    else:
        print("WARN: prev_total.json ausente — relatorio sai sem comparativo da semana anterior.", file=sys.stderr)

    Path("windsor.json").write_text(json.dumps({"result": result}, ensure_ascii=False))

    # alcance/frequencia por criativo (semana reportada) — mantem creative_id p/ o join correto.
    reach = [x for x in rows_of("cur_reach.json") if x.get("ad_name")]
    if reach:
        per = [{"ad_name": x.get("ad_name"), "creative_id": x.get("creative_id"),
                "reach": num(x.get("reach")), "frequency": num(x.get("frequency")),
                "cpp": num(x.get("cpp"))} for x in reach]
        Path("windsor_period.json").write_text(json.dumps(per, ensure_ascii=False))

    print(f"OK build_weekly: semana reportada {cs.strftime('%d/%m')} a {ce.strftime('%d/%m/%Y')} "
          f"({len(cur)} criativos); comparativo {ps.strftime('%d/%m')} a {pe.strftime('%d/%m')} "
          f"{'sim' if prev else 'NAO'}; alcance {'sim' if reach else 'NAO'}.")


if __name__ == "__main__":
    main()
