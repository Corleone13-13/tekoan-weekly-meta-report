#!/usr/bin/env python3
"""Orquestrador da Routine semanal Tekoan Meta Ads (cloud, self-contained).

Le credenciais de variaveis de ambiente (NUNCA hardcoded neste repo):
    WINDSOR_KEY  - api key do Windsor.ai
    SMTP_USER    - gmail remetente
    SMTP_PASS    - senha de app do gmail (16 chars)
    MAIL_TO      - destinatarios separados por virgula

Fluxo: busca dados do Windsor via REST -> monta rows.json -> gera o PDF com
generate_report.py -> envia por e-mail via SMTP. DRY=1 pula o envio.
"""
import os, json, subprocess, smtplib, ssl, urllib.request, urllib.parse
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

WINDSOR_KEY = os.environ["WINDSOR_KEY"]
SMTP_USER   = os.environ["SMTP_USER"]
SMTP_PASS   = os.environ["SMTP_PASS"]
TO          = [a.strip() for a in os.environ.get("MAIL_TO", "").split(",") if a.strip()]
if not TO:
    raise SystemExit("MAIL_TO vazio — defina os destinatarios.")

FIELDS = ["date", "ad_name", "spend", "impressions", "clicks",
          "actions_onsite_conversion_messaging_conversation_started_7d",
          "actions_lead", "actions_offsite_conversion_fb_pixel_lead"]

# 1) buscar dados (Windsor REST) — ultimos 14 dias
qs = urllib.parse.urlencode({"api_key": WINDSOR_KEY,
                             "date_preset": "last_14d",
                             "fields": ",".join(FIELDS)})
with urllib.request.urlopen("https://connectors.windsor.ai/all?" + qs, timeout=90) as r:
    payload = json.loads(r.read().decode())
raw = payload.get("data", payload if isinstance(payload, list) else [])
raw = [x for x in raw if x.get("ad_name")]  # so linhas de anuncio (facebook)
if not raw:
    raise SystemExit("Windsor retornou 0 linhas — nao enviar e-mail vazio.")

# 2) transformar para o formato do gerador
rows = [{
    "date": x["date"], "ad_name": x["ad_name"],
    "spend": x.get("spend") or 0,
    "impressions": x.get("impressions") or 0,
    "clicks": x.get("clicks") or 0,
    "conversations": x.get("actions_onsite_conversion_messaging_conversation_started_7d") or 0,
    "leads": (x.get("actions_lead") or 0) + (x.get("actions_offsite_conversion_fb_pixel_lead") or 0),
} for x in raw]
Path("rows.json").write_text(json.dumps(rows, ensure_ascii=False))

# 3) gerar PDF + email.json
subprocess.run(["python3", "generate_report.py", "rows.json",
                "--outdir", "./out", "--recipient", ",".join(TO)], check=True)

e = json.loads(Path("out/email.json").read_text())
pdf = Path(e["attachment"])

if os.environ.get("DRY") == "1":
    print("DRY-RUN ok | assunto:", e["subject"], "| PDF:", pdf, f"({pdf.stat().st_size} bytes)")
    raise SystemExit(0)

# 4) enviar por e-mail (SMTP Gmail, STARTTLS)
msg = MIMEMultipart("mixed")
msg["From"] = f"Tekoan Reports <{SMTP_USER}>"
msg["To"] = ", ".join(TO)
msg["Subject"] = e["subject"]
alt = MIMEMultipart("alternative")
alt.attach(MIMEText(e["body_text"], "plain", "utf-8"))
alt.attach(MIMEText(e["body_html"], "html", "utf-8"))
msg.attach(alt)
part = MIMEApplication(pdf.read_bytes(), _subtype="pdf")
part.add_header("Content-Disposition", "attachment", filename=pdf.name)
msg.attach(part)
ctx = ssl.create_default_context()
with smtplib.SMTP("smtp.gmail.com", 587, timeout=90) as s:
    s.starttls(context=ctx)
    s.login(SMTP_USER, SMTP_PASS)
    s.sendmail(SMTP_USER, TO, msg.as_string())
print("EMAIL ENVIADO para", ", ".join(TO), "| assunto:", e["subject"])
