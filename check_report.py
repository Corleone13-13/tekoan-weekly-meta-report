#!/usr/bin/env python3
"""Verificacao pos-envio dos relatorios de Meta Ads da Tekoan.

Consulta o LOG DE ENVIOS do proprio Brevo (HTTPS, fonte confiavel e sem
depender de conector de e-mail interativo) para confirmar que o relatorio
do dia saiu: enviado? para os 3 destinatarios? sem duplicata? Depois manda
um e-mail curto de veredito para o responsavel.

Variaveis de ambiente:
    BREVO_API_KEY   (obrigatorio)
    SENDER_EMAIL    (default bonamini.enzo1@gmail.com)
    CHECK_DAY       data a verificar, YYYY-MM-DD (UTC). default: hoje seria
                    ideal, mas o agente passa a data explicita.
    CHECK_KIND      'semana' (semanal) ou 'mes' (mensal) — filtro do assunto
    CHECK_LABEL     rotulo amigavel ('semanal'/'mensal')
    VERDICT_TO      destinatario do veredito (default enzo@tekoan.com.br)
    EXPECTED_TO     destinatarios esperados do relatorio, virgula-separado
                    (default enzo@tekoan.com.br,lorain@tekoan.com.br,fgimenez.mcc@gmail.com)
"""
import os, json, urllib.request, urllib.parse, unicodedata

KEY     = os.environ["BREVO_API_KEY"]
SENDER  = os.environ.get("SENDER_EMAIL", "bonamini.enzo1@gmail.com")
DAY     = os.environ["CHECK_DAY"]
KIND    = os.environ.get("CHECK_KIND", "semana")
LABEL   = os.environ.get("CHECK_LABEL", "semanal")
VERDICT_TO = os.environ.get("VERDICT_TO", "enzo@tekoan.com.br")
EXPECTED = {a.strip().lower() for a in os.environ.get(
    "EXPECTED_TO",
    "enzo@tekoan.com.br,lorain@tekoan.com.br,fgimenez.mcc@gmail.com").split(",") if a.strip()}


def norm(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn").lower()


def brevo_get(path):
    req = urllib.request.Request("https://api.brevo.com" + path,
                                 headers={"api-key": KEY, "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def brevo_post(body):
    req = urllib.request.Request("https://api.brevo.com/v3/smtp/email",
                                 data=json.dumps(body).encode(),
                                 headers={"api-key": KEY, "content-type": "application/json",
                                          "accept": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


# O endpoint /v3/smtp/emails EXIGE ao menos um filtro (email|messageId|templateId):
# consultar so por data devolve HTTP 400. Por isso consultamos UMA VEZ POR
# destinatario esperado (filtro email=) e agregamos os resultados. Cada envio
# gera um messageId PROPRIO por destinatario (a Brevo NAO compartilha um id unico
# entre os destinatarios de um mesmo envio), entao duplicata NAO e "2+ ids no
# total" e sim "o MESMO destinatario recebeu o relatorio 2+ vezes".
err, hits = None, []
try:
    for rcpt in sorted(EXPECTED):
        data = brevo_get("/v3/smtp/emails?email=%s&startDate=%s&endDate=%s&limit=1000"
                         % (urllib.parse.quote(rcpt), DAY, DAY))
        emails = data.get("transactionalEmails") or data.get("emails") or []
        for e in emails:
            s = e.get("subject")
            if "relatorio meta ads" in norm(s) and KIND in norm(s):
                hits.append({"messageId": e.get("messageId") or e.get("date"),
                             "email": (e.get("email") or rcpt).lower(),
                             "subject": s})
except Exception as ex:  # nunca aborta sem mandar veredito
    err = str(ex)

# agrupa por destinatario -> conjunto de messageIds (envios distintos p/ ele).
by_rcpt = {}
for e in hits:
    by_rcpt.setdefault(e["email"], set()).add(e["messageId"])
recips = set(by_rcpt)
faltando = EXPECTED - recips
dup_map = {r: len(m) for r, m in by_rcpt.items() if len(m) > 1}  # destinatario 2+ vezes
dup = bool(dup_map)
enviado = len(recips) >= 1
n = max((len(m) for m in by_rcpt.values()), default=0)  # rodadas p/ o mais atingido
ok = (err is None) and enviado and (not dup) and (not faltando)
status = "OK" if ok else ("ERRO DE VERIFICACAO" if err else "ATENCAO")
subj_sample = hits[0]["subject"] if hits else "(nenhum encontrado)"

dup_txt = (" — DUPLICADO (" + ", ".join(f"{r}={c}x" for r, c in sorted(dup_map.items())) + ")") if dup else ""
rows = [("Status", status),
        ("Enviado", "sim" if enviado else "NAO ENCONTRADO"),
        ("Envios distintos", str(n) + dup_txt),
        ("Destinatarios", ", ".join(sorted(recips)) or "-")]
if faltando:
    rows.append(("FALTANDO", ", ".join(sorted(faltando))))
rows += [("Assunto", subj_sample), ("Registros no log Brevo", str(len(hits)))]
if err:
    rows.append(("Erro", err))

color = "#2c6e2c" if ok else ("#a83228")
html = ("<div style=\"font-family:Helvetica,Arial,sans-serif;font-size:14px;color:#1d2530\">"
        "<p><b>Checagem do relatório %s</b> (%s) &mdash; "
        "<span style=\"color:%s;font-weight:700\">%s</span></p>"
        "<table style=\"border-collapse:collapse;font-size:14px\">" % (LABEL, DAY, color, status))
for k, v in rows:
    html += "<tr><td style=\"padding:2px 0\"><b>%s</b></td><td style=\"padding:2px 0 2px 14px\">%s</td></tr>" % (k, v)
html += ("</table><p style=\"font-size:12px;color:#6b7785\">Verificação automática via log de "
         "envios do Brevo. Se Status = OK, nenhuma ação necessária.</p></div>")

subject = "[Tekoan][CHECK] Relatório %s %s — %s" % (LABEL, DAY, status)
text = "Checagem %s (%s): %s | enviado=%s | envios distintos=%d | dup=%s | faltando=%s | registros=%d" % (
    LABEL, DAY, status, enviado, n, dup, (", ".join(sorted(faltando)) or "nenhum"), len(hits))

st = brevo_post({"sender": {"name": "Tekoan Check", "email": SENDER},
                 "to": [{"email": VERDICT_TO}],
                 "subject": subject, "htmlContent": html, "textContent": text})
print("CHECK ENVIADO para %s | %s | status=%s | envios=%d | dup=%s | faltando=%s | registros=%d | Brevo HTTP %s"
      % (VERDICT_TO, LABEL, status, n, dup, (", ".join(sorted(faltando)) or "nenhum"), len(hits), st))
