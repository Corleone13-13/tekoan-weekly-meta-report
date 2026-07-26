# tekoan-weekly-meta-report

Código dos relatórios de Meta Ads da Tekoan (semanal e mensal), executado por
Routines (agentes headless na nuvem) que baixam estes scripts do `main` via curl.

## Recorte de período (fonte única de verdade)

| Relatório | Quando roda | Período reportado | Comparativo |
|---|---|---|---|
| Semanal | segunda, 11h (America/Sao_Paulo), cron `0 14 * * 1` | semana de **calendário fechada, domingo a sábado** (recorte padrão da Meta) | o domingo a sábado imediatamente anterior |
| Mensal | dia 1º, 11h, cron `0 14 1 * *` | **mês calendário fechado** | mês anterior |

O semanal **não** usa janela rolante de 7 dias. As datas são ancoradas no
calendário por `build_weekly.py` (`week_dates`), de modo que um re-disparo manual
em qualquer dia da semana reporta a MESMA semana em vez de deslizar a janela.
Rodar na segunda (e não no domingo) é decisão de negócio: a semana fecha no
sábado, mas ação corretiva só começa no dia útil, e o dia extra ainda ajuda a
maturar a atribuição de 7 dias das conversas.

Para conferir as datas de uma execução:

```bash
python3 build_weekly.py --print-dates     # CUR_START/CUR_END/PREV_START/PREV_END
python3 build_monthly.py --print-dates
TODAY=2026-07-27 python3 build_weekly.py --print-dates   # simula outra data
```

## Arquitetura em produção

Os dados vêm do **Windsor MCP** (não do REST: o REST devolve 403 do IP do
datacenter) em fetches **AGREGADOS**, sem o campo `date`. Isso é deliberado: com
dado diário o agente headless trava ao escrever o JSON grande, e o relatório não
sai nem dispara alerta de falha. O envio é pela **API HTTPS do Brevo** (SMTP está
bloqueado no sandbox).

- `build_weekly.py` — recebe os 3 fetches agregados da semana (criativos, total
  da semana anterior, alcance) e monta `windsor.json` + `windsor_period.json`.
- `build_monthly.py` — o mesmo para o mês fechado.
- `generate_report.py` — gera o PDF estilizado + `out/email.json`. Aceita
  `--mode weekly|monthly`, `--analysis` (análise escrita pelo agente) e `--history`.
- `run_from_json.py` — mapeia os campos crus, chama o gerador e envia pelo Brevo.
- `check_report.py` — confere no log do Brevo se o relatório foi entregue.
- `run_weekly.py` — **LEGADO, não usar.** Caminho antigo (Windsor REST + SMTP +
  janela rolante `last_14d`), incompatível com o recorte de calendário acima.
  Mantido só como referência histórica e travado por guarda de execução.

Fluxo da routine semanal: `build_weekly.py --print-dates`, 3 `get_data`
agregados via MCP, `build_weekly.py`, o agente escreve `analysis.json`, e
`run_from_json.py` gera e envia.

## Teste local (DRY, nunca envia e-mail)

```bash
bash tests/run_local.sh tests/fixtures/windsor_sample_video.json
bash tests/run_analysis.sh
```

## Segredos

**Nenhum segredo neste repositório.** As credenciais ficam apenas no prompt das
Routines e são lidas do ambiente em tempo de execução:

```
BREVO_API_KEY   # envio (API HTTPS do Brevo)
SENDER_EMAIL    # remetente verificado no Brevo
MAIL_TO         # destinatários separados por vírgula
ALERT_TO        # destino do e-mail de falha
REPORT_MODE     # weekly | monthly
```

A chave do Windsor não é usada por estes scripts: o acesso aos dados é via MCP,
configurado no próprio trigger.
