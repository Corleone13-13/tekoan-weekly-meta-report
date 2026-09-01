#!/usr/bin/env python3
"""Testes das guardas de conteudo (validate.py). Sem dependencia externa:
    python3 tests/test_validate.py
Cada caso monta rows no MESMO formato que run_from_json.py entrega ao gerador.
"""
import os
import sys
import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("EXPECTED_END", None)
os.environ.pop("TODAY", None)

from validate import validate, expected_end, NUMERIC  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'OK  ' if cond else 'FALHA'}  {name}" + (f" — {detail}" if not cond and detail else ""))
    if not cond:
        FAILED.append(name)


def row(date, name, spend=100.0, imp=5000, clicks=100, cid=None, **kw):
    r = {"date": date, "ad_name": name, "creative_id": cid}
    for k in NUMERIC:
        r[k] = 0
    r.update(spend=spend, impressions=imp, clicks=clicks)
    r.update(kw)
    return r


# formato REAL do mensal: build_monthly carimba todo criativo com o ultimo dia do
# mes fechado e o consolidado do mes anterior com o ultimo dia daquele mes.
def mensal_ok():
    return [row("2026-08-31", "Vídeo Hook 1", 900.0, 40000, 800, cid="111"),
            row("2026-08-31", "Estático A", 600.0, 30000, 500, cid="222"),
            row("2026-07-31", "Julho (consolidado)", 1400.0, 65000, 1200)]


def semanal_ok():
    return [row("2026-08-29", "Vídeo Hook 1", 200.0, 9000, 180, cid="111"),
            row("2026-08-29", "Estático A", 150.0, 7000, 140, cid="222"),
            row("2026-08-22", "Semana anterior (consolidado)", 330.0, 15000, 300)]


AGO, JUL = dt.date(2026, 8, 31), dt.date(2026, 7, 31)
SAB = dt.date(2026, 8, 29)

print("=== janela esperada (derivada da data de execucao) ===")
check("mensal rodando em 01/09 aponta para 31/08",
      expected_end("monthly", dt.date(2026, 9, 1)) == AGO)
check("mensal re-disparado em 05/09 continua apontando para 31/08",
      expected_end("monthly", dt.date(2026, 9, 5)) == AGO)
check("semanal rodando na segunda 31/08 aponta para o sabado 29/08",
      expected_end("weekly", dt.date(2026, 8, 31)) == SAB)
check("semanal re-disparado na quarta 02/09 continua no mesmo sabado 29/08",
      expected_end("weekly", dt.date(2026, 9, 2)) == SAB)
os.environ["EXPECTED_END"] = "2026-06-30"
check("EXPECTED_END sobrescreve (reenvio proposital de periodo antigo)",
      expected_end("monthly", dt.date(2026, 9, 1)) == dt.date(2026, 6, 30))
os.environ.pop("EXPECTED_END")

print("=== caminho feliz: nao bloqueia e nao inventa aviso ===")
e, w = validate(mensal_ok(), "monthly", AGO)
check("mensal correto passa sem erro", e == [], str(e))
check("mensal correto passa sem aviso", w == [], str(w))
e, w = validate(semanal_ok(), "weekly", SAB)
check("semanal correto passa sem erro", e == [], str(e))
check("semanal correto passa sem aviso", w == [], str(w))

print("=== G1 janela (o modo de falha que o relatorio nao denuncia sozinho) ===")
e, _ = validate(mensal_ok(), "monthly", dt.date(2026, 9, 30))
check("dado de agosto num run que esperava setembro e bloqueado",
      any("Janela errada" in x for x in e), str(e))
stale = [row("2026-07-31", "Vídeo Hook 1", 900.0, 40000, 800, cid="111"),
         row("2026-06-30", "Junho (consolidado)", 800.0, 35000, 700)]
e, _ = validate(stale, "monthly", AGO)
check("fetch velho de julho sobrando no diretorio e bloqueado",
      any("Janela errada" in x for x in e), str(e))
e, _ = validate(semanal_ok(), "weekly", dt.date(2026, 9, 5))
check("semana deslizada e bloqueada", any("Janela errada" in x for x in e), str(e))

print("=== G2/G3/G4 dado impossivel ===")
r = mensal_ok()
r[0]["spend"] = 0.0
r[1]["spend"] = 0.0
e, _ = validate(r, "monthly", AGO)
check("investimento zero no periodo e bloqueado",
      any("Investimento total" in x for x in e), str(e))

r = mensal_ok()
r.insert(1, row("2026-08-31", "Vídeo Hook 1", 900.0, 40000, 800, cid="111"))
e, _ = validate(r, "monthly", AGO)
check("mesma peca no mesmo dia duas vezes (total dobrado) e bloqueada",
      any("duplicada" in x for x in e), str(e))

r = mensal_ok()
r[0]["clicks"] = r[0]["impressions"] + 1
e, _ = validate(r, "monthly", AGO)
check("mais cliques que impressoes e bloqueado",
      any("Mais cliques" in x for x in e), str(e))

r = mensal_ok()
r[0]["leads"] = -3
e, _ = validate(r, "monthly", AGO)
check("valor negativo e bloqueado", any("negativo" in x for x in e), str(e))

r = mensal_ok()
r[0]["impressions"] = 0
r[0]["clicks"] = 0
e, _ = validate(r, "monthly", AGO)
check("gasto sem impressao e bloqueado", any("sem impressao" in x for x in e), str(e))

e, _ = validate([], "monthly", AGO)
check("lista vazia e bloqueada", e != [], str(e))

print("=== avisos: relatorio SAI, mas com ressalva ===")
r = mensal_ok()
r[0]["spend"] = 100.0
r[1]["spend"] = 100.0   # 200 contra 1400 do mes anterior = -86%
e, w = validate(r, "monthly", AGO)
check("queda de investimento fora da faixa vira aviso, nao erro", e == [], str(e))
check("...e o aviso menciona a variacao", any("variou" in x for x in w), str(w))

r = mensal_ok()
r[0]["spend"], r[1]["spend"] = 700.0, 700.0  # igual ao mes anterior (1400)
e, w = validate(r, "monthly", AGO)
check("investimento identico ao periodo anterior vira aviso",
      e == [] and any("identico" in x for x in w), f"{e} {w}")

e, w = validate(mensal_ok()[:2], "monthly", AGO)
check("sem periodo anterior vira aviso (relatorio sai sem comparativo)",
      e == [] and any("Sem periodo anterior" in x for x in w), f"{e} {w}")

r = mensal_ok()
r[0].update(v_p25=100, v_p50=120, v_p75=40, v_p95=20, v_p100=10)  # p50 > p25: impossivel
e, w = validate(r, "monthly", AGO)
check("curva de retencao que sobe vira aviso",
      e == [] and any("retencao" in x for x in w), f"{e} {w}")

print("=" * 60)
if FAILED:
    print(f"HOUVE FALHAS ({len(FAILED)}): " + "; ".join(FAILED))
    sys.exit(1)
print("TODOS OS CASOS DE validate.py OK")
