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

echo "DRY-RUN ok"
