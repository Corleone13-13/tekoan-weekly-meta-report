# tekoan-weekly-meta-report

Código de formatação do relatório semanal de Meta Ads da Tekoan, executado por
uma Routine (agente na nuvem) toda sexta-feira às 11h (America/Sao_Paulo).

- `generate_report.py` — gera o PDF estilizado + `out/email.json` a partir de um `rows.json`.
- `run_weekly.py` — busca os dados no Windsor.ai (REST), monta o `rows.json`, gera o PDF e envia por e-mail (SMTP).

**Sem segredos neste repositório.** As credenciais são lidas de variáveis de
ambiente em tempo de execução:

```
WINDSOR_KEY   # api key do Windsor.ai
SMTP_USER     # gmail remetente
SMTP_PASS     # senha de app do gmail
MAIL_TO       # destinatários separados por vírgula
```

Uso:

```bash
python3 -m pip install --break-system-packages weasyprint
WINDSOR_KEY=... SMTP_USER=... SMTP_PASS=... MAIL_TO="a@x.com,b@y.com" python3 run_weekly.py
```
