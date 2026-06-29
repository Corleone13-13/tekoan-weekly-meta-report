# Motor de Análise Híbrido + Histórico + Vídeos Campeões — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Trocar a seção de análise do relatório de Meta Ads (hoje 10 regras fixas que repetem toda semana) por um motor híbrido: números determinísticos no script + análise estratégica escrita pelo agente Sonnet da routine, com histórico permanente em Google Sheet, régua anti-repetição (silêncio quando não há o que dizer) e uma seção dedicada de criativos/vídeos campeões.

**Architecture:** O agente cloud (Sonnet, já roda na routine) vira o analista: lê dados Windsor + histórico do Drive, escreve `analysis.json` seguindo uma régua rígida. `generate_report.py` continua dono dos NÚMEROS (tabelas, KPIs de vídeo, ranking de campeões — zero número inventado) e passa a renderizar a narrativa a partir de `analysis.json` (fallback para as regras atuais se o arquivo faltar/vier inválido). Histórico é append-only numa planilha do Drive, lida na entrada e escrita na saída. Conteúdo de análise vai no PDF **e** no corpo do e-mail.

**Tech Stack:** Python 3 stdlib + weasyprint (PDF). Windsor MCP (dados). Brevo HTTPS (envio). Google Sheets via conector Drive/Windsor `google_sheets` (histórico). Repo: github.com/Corleone13-13/tekoan-weekly-meta-report. Routines: semanal `trig_01BnTcM63gb6TXgREN1ENMWJ`, mensal `trig_01LaBwuWKS3EUayZXd27UKtK`.

---

## Princípios de execução

- **Teste tudo LOCAL com `DRY=1`** antes de tocar nas routines. Nunca dispara e-mail durante o desenvolvimento.
- **Fail-open sempre:** qualquer etapa nova (vídeo, analysis.json, histórico) que falhar NÃO pode derrubar o relatório. Sempre cai no comportamento atual e envia.
- **Determinístico vs gerado:** número é do script. Texto/julgamento é do agente. Nunca o script "inventa" leitura, nunca o agente "inventa" número.
- Commits pequenos e frequentes.

## Fixtures de teste (criar uma vez, Fase 0)

Salvar amostras reais já coletadas em `tests/fixtures/`:
- `windsor_sample.json` — saída real de `get_data last_14d` (10 campos + adicionar campos de vídeo quando existirem). Base: dados de 15–28/06/2026 já validados.
- `windsor_period_sample.json` — saída de `get_data last_7d` (ad_name, reach, frequency, cpp).
- `analysis_sample.json` — exemplo do contrato de analysis.json (ver Fase 2).
- `history_sample.json` — 3 semanas fake de histórico para testar a régua anti-repetição.

---

## Fase 0 — Harness de teste local

### Task 0.1: Script de teste com DRY-run

**Files:**
- Create: `tests/run_local.sh`
- Create: `tests/fixtures/windsor_sample.json` (colar amostra real)
- Create: `tests/fixtures/windsor_period_sample.json`

**Passos:**
1. `tests/run_local.sh` faz: `cp` das fixtures para o cwd como `windsor.json`/`windsor_period.json`, `export DRY=1 BREVO_API_KEY=x MAIL_TO=a@b.com`, roda `python3 run_from_json.py windsor.json`, e dá `grep` em `out/email.json` por marcadores esperados (subject, body_html não-vazio, attachment).
2. Rodar: `bash tests/run_local.sh` → Expected: `DRY-RUN ok` + PDF sim.
3. Commit: `test: harness local DRY-run + fixtures reais`.

---

## Fase 1 — Métricas de vídeo, KPIs e ranking de campeões (determinístico)

Campos de vídeo confirmados no Windsor (connector `facebook`):
`actions_video_view` (3s plays), `video_play_actions_video_view` (plays), `video_thruplay_watched_actions_video_view` (thruplay), `cost_per_thruplay_video_view`, `video_p25/p50/p75/p95/p100_watched_actions_video_view`, `video_avg_time_watched_actions_video_view`.

### Task 1.1: Ampliar o fetch diário (routine prompt + ingest)

**Files:**
- Modify: `run_from_json.py:57-66` (mapeamento de `rows`)
- (Routine prompt) adicionar os campos de vídeo + `creative_id` ao `get_data last_14d`.

**O que fazer:**
1. No `rows`, adicionar chaves (todas com `or 0`): `video_3s` (`actions_video_view`), `video_plays` (`video_play_actions_video_view`), `thruplays` (`video_thruplay_watched_actions_video_view`), `v_p25/v_p50/v_p75/v_p95/v_p100`, `video_avg_time` (`video_avg_time_watched_actions_video_view`), `creative_id`.
2. Manter retrocompatível: campos ausentes em JSON antigo viram 0 (não quebra).
3. Teste: rodar harness com uma fixture que inclua campos de vídeo e uma que NÃO inclua; ambas passam (`DRY-RUN ok`).
4. Commit: `feat: ingest de métricas de vídeo em rows.json (retrocompatível)`.

### Task 1.2: KPIs de vídeo + ranking de campeões em generate_report.py

**Files:**
- Modify: `generate_report.py` (agregação por criativo, hoje em `cre_aggs`; adicionar bloco de vídeo)

**O que fazer:**
1. Na agregação por criativo, somar os campos de vídeo e derivar, por criativo de vídeo (onde `video_plays>0` ou `video_3s>0`):
   - **hook_rate** = `video_3s / impressions`
   - **hold_25/50/75/100** = `v_pXX / video_plays`
   - **thruplay_rate** = `thruplays / impressions`
   - **cpt** = custo por thruplay (somar `cost_per_thruplay_video_view` ponderado, ou `spend/thruplays`)
   - **avg_time** = média de `video_avg_time`
2. Função `champion_score(creative)` explícita e documentada: combina CPL (peso maior, só com volume mínimo de conversas, ex. ≥3), hook_rate e hold_50. Retorna ordenação. Documentar a fórmula em comentário.
3. Renderizar duas coisas novas:
   - **Ranking de criativos** (todos): tabela ordenada por performance de conversão (gasto, conversas, CPL, CTR, freq), marcando 🏆 campeão e pior.
   - **Bloco "Vídeos"** (só aparece se houver ≥1 criativo de vídeo): tabela com hook rate, retenção 25/50/75/100, thruplay, custo/thruplay, tempo médio, + CPL.
4. Numeração de seções é dinâmica (`secnum`) — encaixar os novos blocos sem quebrar a ordem.
5. Teste: fixture com vídeo → output HTML contém "Vídeos" e "campeão"; fixture só-estático → NÃO contém bloco de vídeo, sem erro.
6. Commit: `feat: KPIs de vídeo + ranking de criativos campeões`.

---

## Fase 2 — Motor híbrido: analysis.json escrito pelo agente

### Task 2.1: Contrato analysis.json + renderização (com fallback)

**Files:**
- Modify: `generate_report.py` (seções "Diagnóstico" e "Plano de ação")
- Create: `tests/fixtures/analysis_sample.json`

**Contrato `analysis.json`:**
```json
{
  "trend_verdict": "1-2 frases: a conta está melhorando/piorando/estável e por quê.",
  "insights": [
    {"title": "...", "detail": "...", "action": "... ou null",
     "cert": "confirmado|validar", "prio": 1, "novo": true}
  ],
  "video_read": "leitura do campeão de vídeo e o que replicar (ou null)",
  "manager_read": "só no mensal: leitura da performance do gestor (ou null)",
  "silence": false
}
```

**O que fazer:**
1. `generate_report.py` aceita `--analysis analysis.json`. Se presente e válido: renderiza `trend_verdict`, `insights[]`, `video_read`, `manager_read` no lugar das 10 regras.
2. Se `silence=true` e `insights` vazio: a seção de Diagnóstico/Ações mostra a linha mínima ("Sem novos pontos estratégicos esta semana; números dentro do padrão das últimas semanas.") — mas as TABELAS de números/tendência/vídeo continuam aparecendo sempre.
3. **Fallback:** se `--analysis` ausente, ilegível ou fora do contrato → usa as 10 regras atuais (código atual permanece como função `legacy_diagnostics()`). Nunca quebra.
4. Teste: (a) com `analysis_sample.json` → HTML contém o trend_verdict e os insights do arquivo, NÃO contém textos das regras; (b) sem o arg → HTML contém as regras (fallback); (c) `silence=true` → linha mínima + tabelas presentes.
5. Commit: `feat: renderizar análise do agente via analysis.json + fallback p/ regras`.

### Task 2.2: run_from_json.py passa analysis.json

**Files:**
- Modify: `run_from_json.py:109-112` (chamada do generate_report)

**O que fazer:**
1. Se existir `analysis.json` no cwd, anexar `["--analysis", "analysis.json"]` ao subprocess.
2. Teste: harness com `analysis.json` presente → `DRY-RUN ok`; conferir em `out/email.json` que o body reflete a análise.
3. Commit: `feat: wire analysis.json no pipeline de envio`.

---

## Fase 3 — Histórico permanente (Google Sheet)

Decisão: planilha "Histórico Tekoan Ads" no Drive do Enzo (autorização necessária). Abas `Semanal` e `Mensal`. O AGENTE faz a I/O (tem conector Drive/Sheets); o script só EMITE a linha a ser anexada.

### Task 3.1: generate_report.py emite history_row.json

**Files:**
- Modify: `generate_report.py` (após calcular agregados do período)

**O que fazer:**
1. Escrever `out/history_row.json` com snapshot: `periodo, modo, spend, conversas, cpl, cpc, cpm, ctr, reply_rate, blocks, n_criativos, campeao, hook_rate_campeao, insights_titulos[], acao_principal, trend_verdict`.
2. Teste: harness → `out/history_row.json` existe e tem as chaves.
3. Commit: `feat: emitir history_row.json para append no histórico`.

### Task 3.2: Planilha + I/O no prompt da routine (Fase 5 conecta)

- O agente, no prompt (Fase 5): **antes** de analisar, lê as últimas ~8 linhas da aba do modo (Semanal/Mensal) e usa como contexto da régua anti-repetição; **depois** de gerar, anexa `out/history_row.json` como nova linha.
- Criar a planilha uma vez (manual ou via MCP) e fixar o `spreadsheetId` no prompt.
- Fallback: se a planilha falhar na leitura, o agente analisa sem histórico (só perde o anti-repetição daquela semana); se falhar na escrita, segue sem abortar.

---

## Fase 4 — Corpo do e-mail e PDF, ambos com análise + profundidade mensal

### Task 4.1: body_html do e-mail com resumo executivo

**Files:**
- Modify: `generate_report.py` (montagem de `body_html`/`out/email.json`)

**O que fazer:**
1. `body_html` (corpo do e-mail) passa a conter: trend_verdict + top 3 insights + plano de ação + 1 linha do campeão. Legível sem abrir o PDF. PDF segue com tudo.
2. Teste: `out/email.json` → `body_html` contém o trend_verdict e ≥1 insight.
3. Commit: `feat: resumo executivo com análise no corpo do e-mail`.

### Task 4.2: Profundidade do modo mensal

**Files:**
- Modify: `generate_report.py` (ramos `--mode monthly`)
- (Prompt mensal, Fase 5): instrução de análise mais robusta.

**O que fazer:**
1. No mensal: comparação MoM já existe; adicionar leitura de tendência das 4 semanas (puxa aba Semanal do mês) e a seção `manager_read`.
2. Teste: harness com `REPORT_MODE=monthly` + fixture mensal → HTML contém seção de performance do gestor.
3. Commit: `feat: análise mensal robusta + leitura de performance do gestor`.

---

## Fase 5 — Atualizar prompts das routines (semanal + mensal)

### Task 5.1: Prompt semanal

**Via RemoteTrigger update `trig_01BnTcM63gb6TXgREN1ENMWJ`.** Novo prompt instrui o agente a, em ordem:
1. `get_fields` + `get_data last_14d` com os 10 campos atuais **+ campos de vídeo + creative_id** → `windsor.json`.
2. `get_data last_7d` (reach/frequency/cpp) → `windsor_period.json`.
3. Ler últimas ~8 linhas da aba `Semanal` da planilha de histórico (Drive/Sheets MCP).
4. **Escrever `analysis.json`** seguindo o contrato e a RÉGUA: só insight novo/mudou/material; proibido repetir o que já está no histórico se nada mudou; silêncio (`silence=true`, insights vazio) se nada passa da régua; problema ativo e material pode repetir mas justificando; camada criativa (hipóteses/testes/ângulos); comentar campeão de vídeo.
5. Baixar scripts do repo (curl raw) e rodar `run_from_json.py` (que já pega `analysis.json`).
6. Anexar `out/history_row.json` à aba `Semanal`.
7. Critério de sucesso inalterado: linha `EMAIL ENVIADO`.

**Teste antes do cutover:** disparar a routine com um marcador `DRY` não é trivial na nuvem; em vez disso, validar TODO o pipeline local com `bash tests/run_local.sh` e revisar `out/email.json`/PDF à mão. Só então atualizar o prompt.

### Task 5.2: Prompt mensal

Idem para `trig_01LaBwuWKS3EUayZXd27UKtK` com `REPORT_MODE=monthly`, `last_70d`, aba `Mensal`, e a régua de profundidade mensal + `manager_read`.

---

## Fase 6 — Cutover e verificação

1. Push de todas as mudanças para `main` (os prompts baixam via curl raw de `main`).
2. Disparar a routine semanal manualmente (RemoteTrigger run) e **aguardar ≥35 min** (cold-start).
3. Verificar entrega pelo endpoint `GET /v3/smtp/statistics/events` (NÃO usar `/v3/smtp/emails` — não mostra envios recentes).
4. Conferir à mão: corpo do e-mail tem análise, PDF tem bloco de vídeo + ranking, histórico ganhou a linha nova.
5. A routine verificadora (`trig_01HvfgastNPR46E9krxnVAwX`) confirma OK/FALHA na sexta.

---

## O que preciso do Enzo

1. **Autorizar a criação da planilha** "Histórico Tekoan Ads" no Drive (e me passar/confirmar o acesso do conector).
2. Nada mais — tenho acesso ADMIN ao repo e disparo das routines.

## Riscos e mitigações

- **Vídeo sem dado** (conta ainda em estático): bloco de vídeo só renderiza com `video_plays>0` → sem quebra.
- **Agente gera analysis.json fora do contrato:** validação + fallback para regras legadas.
- **Sheets indisponível:** análise sem histórico naquela semana; escrita best-effort.
- **Cold-start ~30-35 min:** já conhecido; verificação só após a janela.
