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
- `run_from_json.py` — mapeia os campos crus, roda as guardas, chama o gerador e
  envia pelo Brevo.
- `validate.py` — guardas de conteúdo executadas **antes** de gerar e enviar.
- `check_report.py` — confere no log do Brevo se o relatório foi entregue.
- `run_weekly.py` — **LEGADO, não usar.** Caminho antigo (Windsor REST + SMTP +
  janela rolante `last_14d`), incompatível com o recorte de calendário acima.
  Mantido só como referência histórica e travado por guarda de execução.

Fluxo da routine semanal: `build_weekly.py --print-dates`, 3 `get_data`
agregados via MCP, `build_weekly.py`, o agente escreve `analysis.json`, e
`run_from_json.py` gera e envia.

## Guardas de conteúdo (`validate.py`)

Rodam dentro de `run_from_json.py`, **antes** de gerar o relatório. Existem aqui, e
não no verificador de entrega, porque aquele roda ~1h depois do envio: quando ele
acusa, o e-mail errado já está na caixa do cliente.

Um **ERRO** bloqueia o envio — cai no `SystemExit` que já existia, dispara
`[Tekoan][FALHA]` só para `ALERT_TO` e o cliente não recebe nada. Um **AVISO** não
bloqueia: o relatório sai e um `[Tekoan][AVISO]` vai só para `ALERT_TO`.

| Nível | Guarda |
|---|---|
| ERRO | janela do dado ≠ período fechado que este run deveria reportar |
| ERRO | nenhuma linha no período, ou investimento total zero |
| ERRO | mesma peça no mesmo dia duas vezes (total sairia dobrado) |
| ERRO | valor negativo, mais cliques que impressões, gasto sem impressão |
| AVISO | sem período anterior (relatório sai sem comparativo) |
| AVISO | investimento idêntico ao período anterior (cheiro de fetch reaproveitado) |
| AVISO | investimento variou mais que `SPEND_DEV_MAX` contra o período anterior |
| AVISO | curva de retenção de vídeo que sobe |

A guarda de janela é a que justifica o resto. `generate_report.py` **deriva** o
período reportado da maior data presente nos dados (`maxd`), então ele não tem como
notar sozinho que está reportando o mês errado — passa a chamar de "mês fechado" o
que quer que tenha chegado, e o relatório sai coerente e bonito com o período
errado. `validate.expected_end()` recalcula a janela a partir da data de execução
usando as **mesmas** `week_dates`/`month_dates` do build, então a checagem é
independente do dado.

**A routine precisa baixar `validate.py`.** Ela não clona o repo, baixa uma lista
fixa de arquivos — `validate.py` tem de estar nessa lista, junto do `build_*.py` do
modo em uso. Duas defesas para o caso de alguém esquecer: `run_from_json.py` importa
`validate` **dentro de `main()`**, então a ausência do arquivo vira `[Tekoan][FALHA]`
em vez de matar o script antes do `try/except` (sem relatório *e* sem alerta); e
`validate.py` importa o `build_*` do modo em uso **só na hora**, para o semanal não
exigir `build_monthly.py` e vice-versa.

Não existe chave para desligar as guardas — se existisse, acabaria colada no prompt
da routine e elas morreriam em silêncio. Para reenviar um período antigo de
propósito, declare a janela: `EXPECTED_END=2026-06-30`.

## Teste local (DRY, nunca envia e-mail)

```bash
bash tests/run_local.sh tests/fixtures/windsor_sample_video.json
bash tests/run_analysis.sh          # inclui os casos de bloqueio + os unitários
python3 tests/test_validate.py      # só as guardas
```

As fixtures são congeladas numa data fixa, então os harnesses declaram
`EXPECTED_END` derivado da própria fixture — em produção a janela vem da data de
execução. `DRY=1` garante zero e-mail, inclusive no caminho de alerta.

## Segredos

**Nenhum segredo neste repositório.** As credenciais ficam apenas no prompt das
Routines e são lidas do ambiente em tempo de execução:

```
BREVO_API_KEY   # envio (API HTTPS do Brevo)
SENDER_EMAIL    # remetente verificado no Brevo
MAIL_TO         # destinatários separados por vírgula
ALERT_TO        # destino do e-mail de falha e do e-mail de aviso
REPORT_MODE     # weekly | monthly
EXPECTED_END    # opcional: força a janela esperada (reenvio de período antigo)
SPEND_DEV_MAX   # opcional: desvio de investimento que vira aviso (default 0.7)
TODAY           # opcional: simula outra data de execução (testes)
```

A chave do Windsor não é usada por estes scripts: o acesso aos dados é via MCP,
configurado no próprio trigger.
