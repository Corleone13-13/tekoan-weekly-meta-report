#!/usr/bin/env python3
"""Variante da Routine para ambiente cloud: os dados do Windsor sao obtidos pelo
AGENTE via conector MCP (o endpoint REST connectors.windsor.ai retorna 403 a
partir do IP do datacenter) e salvos em um arquivo JSON. Este script LE esse
arquivo, monta rows.json, gera o relatorio e envia por e-mail.

Uso:  python3 run_from_json.py <windsor.json>
Aceita os formatos {"result":[...]}, {"data":[...]} ou [...].

Credenciais por variavel de ambiente: SMTP_USER, SMTP_PASS, MAIL_TO. DRY=1 pula envio.
"""
import os, sys, json, subprocess, smtplib, ssl, traceback
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]
TO        = [a.strip() for a in os.environ.get("MAIL_TO", "").split(",") if a.strip()]


def smtp_send(to_list, msg):
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=90) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, to_list, msg.as_string())


def send_plain(to_list, subject, text):
    m = MIMEText(text, "plain", "utf-8")
    m["From"] = SMTP_USER; m["To"] = ", ".join(to_list); m["Subject"] = subject
    smtp_send(to_list, m)


def main():
    if not TO:
        raise SystemExit("MAIL_TO vazio.")
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "windsor.json")
    payload = json.loads(src.read_text())
    if isinstance(payload, dict):
        raw = payload.get("result") or payload.get("data") or []
    else:
        raw = payload
    raw = [x for x in raw if x.get("ad_name")]
    if not raw:
        raise SystemExit("Sem linhas de anuncio no JSON do Windsor — nao enviar e-mail vazio.")

    rows = [{
        "date": x["date"], "ad_name": x["ad_name"],
        "spend": x.get("spend") or 0, "impressions": x.get("impressions") or 0,
        "clicks": x.get("clicks") or 0,
        "conversations": x.get("actions_onsite_conversion_messaging_conversation_started_7d") or 0,
        "leads": (x.get("actions_lead") or 0) + (x.get("actions_offsite_conversion_fb_pixel_lead") or 0),
    } for x in raw]
    Path("rows.json").write_text(json.dumps(rows, ensure_ascii=False))

    subprocess.run(["python3", "generate_report.py", "rows.json",
                    "--outdir", "./out", "--recipient", ",".join(TO)], check=True)
    e = json.loads(Path("out/email.json").read_text())
    pdf = Path(e["attachment"]) if e.get("attachment") else None
    has_pdf = bool(pdf and pdf.exists())

    if os.environ.get("DRY") == "1":
        print(f"DRY-RUN ok | assunto: {e['subject']} | PDF: {'sim' if has_pdf else 'NAO (fallback HTML)'}")
        return

    msg = MIMEMultipart("mixed")
    msg["From"] = f"Tekoan Reports <{SMTP_USER}>"
    msg["To"] = ", ".join(TO)
    msg["Subject"] = e["subject"]
    if has_pdf:
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(e["body_text"], "plain", "utf-8"))
        alt.attach(MIMEText(e["body_html"], "html", "utf-8"))
        msg.attach(alt)
        part = MIMEApplication(pdf.read_bytes(), _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=pdf.name)
        msg.attach(part)
        mode = "com PDF anexo"
    else:
        full_html = Path(e["report_html"]).read_text()
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(e["body_text"], "plain", "utf-8"))
        alt.attach(MIMEText(full_html, "html", "utf-8"))
        msg.attach(alt)
        mode = "relatorio no corpo (sem PDF)"
    smtp_send(TO, msg)
    print(f"EMAIL ENVIADO para {', '.join(TO)} | {mode} | assunto: {e['subject']}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        print("FALHA:\n" + tb)
        try:
            send_plain([SMTP_USER], "[Tekoan][FALHA] relatorio semanal Meta Ads",
                       "Falha ao gerar/enviar.\n\n" + tb)
        except Exception as ex2:
            print("Tambem falhou ao enviar diagnostico:", ex2)
        raise
