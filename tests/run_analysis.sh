#!/usr/bin/env bash
# Harness do motor hibrido (analysis.json) em DRY-RUN: NUNCA envia e-mail.
# Roda os casos a/b/c/d pelo pipeline real (run_from_json.py -> generate_report.py).
# Uso: bash tests/run_analysis.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"
FX="$ROOT/tests/fixtures"

export DRY=1
export BREVO_API_KEY=x
export MAIL_TO=test@test.com

FAILED=0
REPORT_HTML="$ROOT/out/report.html"

# prepara o pipeline: copia dados (windsor) e, opcionalmente, a analise.
setup() {  # $1 = fixture de dados ; $2 = fixture de analise (ou "-" para nenhuma)
  cp "$1" "$ROOT/windsor.json"
  cp "$FX/windsor_period_sample.json" "$ROOT/windsor_period.json"
  rm -f "$ROOT/analysis.json"
  [ "$2" != "-" ] && cp "$2" "$ROOT/analysis.json"
  rm -f "$ROOT/out/report.html" "$ROOT/out/email.json"
  python3 run_from_json.py windsor.json
}

want()    { if grep -qF "$1" "$REPORT_HTML"; then echo "  OK  contem: $2"; else echo "  FALHA  faltou: $2"; FAILED=1; fi; }
absent()  { if grep -qF "$1" "$REPORT_HTML"; then echo "  FALHA  nao deveria conter: $2"; FAILED=1; else echo "  OK  ausente: $2"; fi; }

echo "============================================================"
echo "CASO (a) — analysis_sample.json (weekly): renderiza analise, esconde legado"
echo "============================================================"
REPORT_MODE=weekly setup "$FX/windsor_sample_video.json" "$FX/analysis_sample.json"
want "A conta melhorou na margem"              "trend_verdict do arquivo"
want "Vídeo Hook 1 puxa a conversão do período" "titulo do insight 1"
want "Base criativa ainda estreita"            "titulo do insight 2"
want "Leitura do vídeo campeão"                 "video_read junto do bloco Videos"
want "Ranking de criativos"                     "tabela de ranking (sempre)"
absent "A conversão é conversa no WhatsApp, não lead qualificado" "regra legada 1"
absent "Volume baixo ("                          "regra legada 6"

echo "============================================================"
echo "CASO (b) — SEM analysis.json (weekly): fallback para regras fixas"
echo "============================================================"
REPORT_MODE=weekly setup "$FX/windsor_sample_video.json" "-"
want "A conversão é conversa no WhatsApp, não lead qualificado" "regra legada 1 (fallback)"
want "Ranking de criativos"                     "tabela de ranking (sempre)"
absent "Vídeo Hook 1 puxa a conversão do período" "insight do arquivo (nao deve existir)"
absent "A conta melhorou na margem"             "trend_verdict do arquivo (nao deve existir)"

echo "============================================================"
echo "CASO (c) — analysis_silence.json (weekly): linha minima + tabelas seguem"
echo "============================================================"
REPORT_MODE=weekly setup "$FX/windsor_sample_video.json" "$FX/analysis_silence.json"
want "Sem novos pontos estratégicos neste período" "linha minima de silencio"
want "Ranking de criativos"                     "Ranking ainda presente"
absent "A conversão é conversa no WhatsApp, não lead qualificado" "regra legada (nao deve existir)"

echo "============================================================"
echo "CASO (d) — MENSAL: analysis_mensal.json (monthly): manager_read + ranking + videos"
echo "============================================================"
REPORT_MODE=monthly setup "$FX/windsor_sample_video.json" "$FX/analysis_mensal.json"
want "Leitura de performance do gestor"         "secao do gestor (manager_read)"
want "A gestão de ads no mês foi consistente"   "texto do manager_read"
want "Ranking de criativos"                     "Ranking presente no mensal"
want ">Vídeos</h2>"                              "bloco Videos presente no mensal"
want "Relatório mensal · Meta Ads"              "modo mensal de fato ativo"

echo "============================================================"
echo "CASO (e) — caracteres especiais: texto do agente vai ESCAPADO no HTML"
echo "============================================================"
REPORT_MODE=weekly setup "$FX/windsor_sample_video.json" "$FX/analysis_chars.json"
want "&lt; 1%"                 "'<' escapado no trend_verdict (&lt;)"
want "&amp; retorno"           "'&' escapado no trend_verdict (&amp;)"
want "&gt; meta"               "'>' escapado no trend_verdict (&gt;)"
want "&lt;b&gt;teste&lt;/b&gt;" "tag injetada no detail virou texto escapado"
absent "< 1%"                  "sequencia crua '< 1%' (nao pode existir)"
absent "<b>teste</b>"          "tag '<b>teste</b>' literal injetada (nao pode existir)"

echo "============================================================"
echo "EXTRA — JSON quebrado nao quebra o relatorio (fail-open -> legacy)"
echo "============================================================"
cp "$FX/windsor_sample_video.json" "$ROOT/windsor.json"
cp "$FX/windsor_period_sample.json" "$ROOT/windsor_period.json"
printf '{ isto nao e json valido,,,' > "$ROOT/analysis.json"
rm -f "$ROOT/out/report.html"
if REPORT_MODE=weekly python3 run_from_json.py windsor.json; then
  want "A conversão é conversa no WhatsApp, não lead qualificado" "caiu no legacy sem crashar"
else
  echo "  FALHA  pipeline quebrou com JSON invalido"; FAILED=1
fi

# limpeza dos artefatos transitorios deste teste
rm -f "$ROOT/analysis.json" "$ROOT/windsor.json" "$ROOT/windsor_period.json" \
      "$ROOT/rows.json" "$ROOT/period.json"

echo "============================================================"
if [ "$FAILED" -eq 0 ]; then echo "TODOS OS CASOS OK (DRY-RUN, zero e-mail enviado)"; exit 0
else echo "HOUVE FALHAS"; exit 1; fi
