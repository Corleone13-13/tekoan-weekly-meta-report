#!/usr/bin/env bash
# Harness de teste local em DRY-RUN: roda o pipeline completo sem JAMAIS enviar e-mail.
# Uso: bash tests/run_local.sh tests/fixtures/windsor_sample_estatico.json
set -euo pipefail

FIXTURE="${1:?uso: bash tests/run_local.sh <caminho/para/windsor.json>}"

# raiz do repo = pasta-mae da pasta tests/ deste script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# resolve a fixture (aceita caminho relativo a partir da raiz do repo)
if [ ! -f "$FIXTURE" ]; then
  echo "FIXTURE nao encontrada: $FIXTURE" >&2
  exit 2
fi

# prepara entradas do pipeline
cp "$FIXTURE" "$ROOT/windsor.json"
cp "$ROOT/tests/fixtures/windsor_period_sample.json" "$ROOT/windsor_period.json"

# ambiente de teste: DRY garante ZERO envio de e-mail
export DRY=1
export BREVO_API_KEY=x
export MAIL_TO=test@test.com
export REPORT_MODE=weekly

# As fixtures sao congeladas numa data fixa; em producao a janela esperada vem da
# data de execucao (validate.expected_end). Aqui declaramos a janela da propria
# fixture, senao a guarda G1 barra todo teste. Derivado do arquivo de proposito:
# trocar a fixture nao exige lembrar de editar esta linha.
EXPECTED_END="$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));r=d.get('result') or d.get('data') or d;print(max(x['date'] for x in r))" "$FIXTURE")"
export EXPECTED_END

python3 run_from_json.py windsor.json

EMAIL_JSON="$ROOT/out/email.json"
if [ ! -f "$EMAIL_JSON" ]; then
  echo "FALHA: out/email.json nao foi gerado" >&2
  exit 1
fi

# report_html apontado dentro do email.json (corpo completo do relatorio)
REPORT_HTML="$(python3 -c "import json;print(json.load(open('$EMAIL_JSON'))['report_html'])")"
BODY_LEN="$(python3 -c "import json;print(len(json.load(open('$EMAIL_JSON'))['body_html']))")"

echo "out/email.json gerado | body_html: ${BODY_LEN} chars | report_html: ${REPORT_HTML}"

# ----------------------------------------------------------------------------
# Asseroes por tipo de fixture (Parte C). Greps no HTML completo do relatorio.
# ----------------------------------------------------------------------------
case "$FIXTURE" in
  *split_id*)
    echo "Checando asseroes da fixture SPLIT por creative_id..."
    grep -q "Ranking de criativos" "$REPORT_HTML" || { echo "FALHA: faltou 'Ranking de criativos'" >&2; exit 1; }
    # mesmo ad_name, dois creative_ids -> DUAS linhas, cada uma com seu #id
    grep -q "#111" "$REPORT_HTML" || { echo "FALHA: faltou peça #111" >&2; exit 1; }
    grep -q "#222" "$REPORT_HTML" || { echo "FALHA: faltou peça #222" >&2; exit 1; }
    # cada #id aparece nas DUAS tabelas (Consolidado + Ranking) = 2 ocorrencias
    n111="$(grep -o "#111" "$REPORT_HTML" | wc -l | tr -d ' ')"
    [ "$n111" -ge 2 ] || { echo "FALHA: #111 deveria aparecer em 2 tabelas, achei $n111" >&2; exit 1; }
    grep -q "🏆" "$REPORT_HTML" || { echo "FALHA: faltou campeao '🏆'" >&2; exit 1; }
    grep -q "⚠️" "$REPORT_HTML" || { echo "FALHA: faltou pior '⚠️'" >&2; exit 1; }
    echo "OK: duas pecas de mesmo ad_name separadas por #id (#111 campeao, #222 pior)."
    ;;
  *video*)
    echo "Checando asseroes da fixture de VIDEO..."
    grep -q "Ranking de criativos" "$REPORT_HTML" || { echo "FALHA: faltou 'Ranking de criativos'" >&2; exit 1; }
    grep -q ">Vídeos</h2>" "$REPORT_HTML"          || { echo "FALHA: faltou bloco 'Vídeos'" >&2; exit 1; }
    grep -q "🏆" "$REPORT_HTML"                     || { echo "FALHA: faltou marcador campeao '🏆'" >&2; exit 1; }
    grep -q "Vídeo vs Imagem" "$REPORT_HTML"        || { echo "FALHA: faltou bloco 'Vídeo vs Imagem' (fixture mista)" >&2; exit 1; }
    echo "OK: Ranking de criativos + Vídeos + 🏆 + Vídeo vs Imagem presentes."
    ;;
  *estatico*)
    echo "Checando asseroes da fixture ESTATICA..."
    grep -q "Ranking de criativos" "$REPORT_HTML" || { echo "FALHA: faltou 'Ranking de criativos'" >&2; exit 1; }
    if grep -q ">Vídeos</h2>" "$REPORT_HTML"; then
      echo "FALHA: bloco 'Vídeos' nao deveria aparecer sem criativo de video" >&2; exit 1
    fi
    if grep -q "Vídeo vs Imagem" "$REPORT_HTML"; then
      echo "FALHA: bloco 'Vídeo vs Imagem' nao deveria aparecer com um so formato" >&2; exit 1
    fi
    # fallback retrocompatível: sem creative_id, NAO ha rotulo #id (agrupa por nome)
    if grep -q 'class="cid"' "$REPORT_HTML"; then
      echo "FALHA: rotulo #id nao deveria aparecer sem creative_id (fallback por nome)" >&2; exit 1
    fi
    echo "OK: Ranking presente; sem #id (fallback por nome); 'Vídeos'/'Vídeo vs Imagem' ausentes."
    ;;
esac

echo "DRY-RUN ok"
