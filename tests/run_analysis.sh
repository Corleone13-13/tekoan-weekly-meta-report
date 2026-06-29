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
EMAIL_JSON="$ROOT/out/email.json"
HISTORY_ROW="$ROOT/out/history_row.json"

# prepara o pipeline: copia dados (windsor) e, opcionalmente, analise e historico.
setup() {  # $1 = fixture de dados ; $2 = analise (ou "-") ; $3 = historico (opcional, ou "-")
  cp "$1" "$ROOT/windsor.json"
  cp "$FX/windsor_period_sample.json" "$ROOT/windsor_period.json"
  rm -f "$ROOT/analysis.json" "$ROOT/history.json"
  [ "$2" != "-" ] && cp "$2" "$ROOT/analysis.json"
  [ "${3:-"-"}" != "-" ] && cp "$3" "$ROOT/history.json"
  rm -f "$ROOT/out/report.html" "$ROOT/out/email.json" "$ROOT/out/history_row.json"
  python3 run_from_json.py windsor.json
}

want()    { if grep -qF "$1" "$REPORT_HTML"; then echo "  OK  contem: $2"; else echo "  FALHA  faltou: $2"; FAILED=1; fi; }
absent()  { if grep -qF "$1" "$REPORT_HTML"; then echo "  FALHA  nao deveria conter: $2"; FAILED=1; else echo "  OK  ausente: $2"; fi; }
# valida que o snapshot out/history_row.json tem as chaves principais
hwant()   { if python3 -c "import json,sys;d=json.load(open('$HISTORY_ROW'));sys.exit(0 if all(k in d for k in sys.argv[1].split(',')) else 1)" "$1"; then echo "  OK  history_row.json tem chaves: $1"; else echo "  FALHA  history_row.json sem chaves: $1"; FAILED=1; fi; }
# busca no body_html dentro de out/email.json (resumo executivo do e-mail)
bwant()   { if python3 -c "import json,sys;sys.exit(0 if sys.argv[1] in json.load(open('$EMAIL_JSON'))['body_html'] else 1)" "$1"; then echo "  OK  body_html contem: $2"; else echo "  FALHA  body_html faltou: $2"; FAILED=1; fi; }

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
# snapshot de histórico do período atual
hwant "periodo,modo,spend,conversas,cpl,campeao"  "snapshot gerado"
# resumo executivo no corpo do e-mail
bwant "A conta melhorou na margem"               "trend_verdict no body_html"
bwant "Vídeo Hook 1 puxa a conversão do período" "titulo de insight no body_html"
bwant "Campeão:"                                  "linha do campeao no body_html"

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
# body_html tambem escapa o texto do agente
bwant "CTR &lt; 1%"            "trend_verdict escapado no body_html"
python3 -c "import json,sys;sys.exit(1 if '< 1%' in json.load(open('$EMAIL_JSON'))['body_html'] else 0)" \
  && echo "  OK  body_html sem sequencia crua '< 1%'" || { echo "  FALHA  body_html tem '< 1%' cru"; FAILED=1; }

echo "============================================================"
echo "CASO (f) — MENSAL com --history: seccao Evolucao + 3 periodos"
echo "============================================================"
REPORT_MODE=monthly setup "$FX/windsor_sample_video.json" "$FX/analysis_mensal.json" "$FX/history_sample.json"
want "Evolução"                "secao Evolucao no mensal"
want "mês Março/2026"          "periodo 1 do historico"
want "mês Abril/2026"          "periodo 2 do historico"
want "mês Maio/2026"           "periodo 3 do historico"
want "Leitura de performance do gestor" "manager_read segue no mensal (nao-regressao)"
hwant "periodo,modo,spend,conversas,cpl,campeao" "snapshot mensal gerado"

echo "============================================================"
echo "CASO (g) — sem --history: Evolucao NAO aparece (fallback natural)"
echo "============================================================"
REPORT_MODE=weekly setup "$FX/windsor_sample_video.json" "$FX/analysis_sample.json" "-"
absent ">Evolução</h2>"        "secao Evolucao (nao deve existir sem historico)"

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
rm -f "$ROOT/analysis.json" "$ROOT/history.json" "$ROOT/windsor.json" \
      "$ROOT/windsor_period.json" "$ROOT/rows.json" "$ROOT/period.json"

echo "============================================================"
if [ "$FAILED" -eq 0 ]; then echo "TODOS OS CASOS OK (DRY-RUN, zero e-mail enviado)"; exit 0
else echo "HOUVE FALHAS"; exit 1; fi
