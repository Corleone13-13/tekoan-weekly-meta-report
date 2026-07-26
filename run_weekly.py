#!/usr/bin/env python3
"""LEGADO / DEPRECADO — NAO USAR EM PRODUCAO.

Este era o orquestrador original do relatorio semanal. Foi substituido pelo par
`build_weekly.py` + `run_from_json.py`, que a routine da nuvem usa hoje. Ficou
incompativel com a producao em TRES pontos:

  1) JANELA ERRADA. Usa `date_preset=last_14d` e deixa o gerador recortar os
     ultimos 7 dias a partir da ultima data com entrega, ou seja, janela
     ROLANTE. Desde 24/07/2026 o relatorio semanal usa a semana de CALENDARIO
     fechada, DOMINGO a SABADO (recorte padrao da Meta), ancorada em
     `build_weekly.py:week_dates`. Rodar este script produz numeros que NAO
     batem com o relatorio oficial.
  2) Windsor via REST (`connectors.windsor.ai`), que devolve 403 do IP do
     datacenter. A producao le por MCP.
  3) Envio por SMTP, com as portas 25/465/587 bloqueadas no sandbox. A producao
     envia pela API HTTPS do Brevo.

Mantido apenas como referencia historica. A execucao esta travada de proposito:
para rodar mesmo assim (fora de producao), exporte RUN_LEGACY=1.

Le credenciais de variaveis de ambiente (NUNCA hardcoded neste repo):
    WINDSOR_KEY, SMTP_USER, SMTP_PASS, MAIL_TO (virgula-separado)

Fluxo: busca dados (Windsor REST) -> rows.json -> gera relatorio
(generate_report.py: PDF best-effort + HTML completo sempre) -> envia por SMTP.
- Se o PDF existir: e-mail = resumo + PDF anexo.
- Se nao (weasyprint indisponivel): e-mail = relatorio COMPLETO no corpo (HTML), sem anexo.
- Qualquer falha global: envia diagnostico (texto puro) para SMTP_USER.
DRY=1 pula o envio.
"""
import os, json, subprocess, smtplib, ssl, traceback, urllib.request, urllib.parse
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

# Guarda de execucao: evita que este caminho legado seja rodado por engano e
# produza um relatorio com janela ROLANTE, divergente do oficial (dom a sab).
if not os.environ.get("RUN_LEGACY"):
    raise SystemExit(
        "run_weekly.py esta DEPRECADO e nao deve ser usado.\n"
        "Producao: build_weekly.py (janela de calendario, domingo a sabado) + "
        "run_from_json.py (envio pela API do Brevo).\n"
        "Este script usa janela ROLANTE (last_14d), Windsor REST (403) e SMTP "
        "(bloqueado), entao os numeros NAO batem com o relatorio oficial.\n"
        "Se ainda assim quiser rodar fora de producao: RUN_LEGACY=1 python3 run_weekly.py"
    )

SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]
TO        = [a.strip() for a in os.environ.get("MAIL_TO", "").split(",") if a.strip()]


def smtp_send(to_list, msg):
    ctx = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=90) as s:
        s.starttls(context=ctx)
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, to_list, msg.as_string())


def send_plain(to_list, subject, text):
    m = MIMEText(text, "plain", "utf-8")
    m["From"] = SMTP_USER
    m["To"] = ", ".join(to_list)
    m["Subject"] = subject
    smtp_send(to_list, m)


def main():
    WINDSOR_KEY = os.environ["WINDSOR_KEY"]
    if not TO:
        raise SystemExit("MAIL_TO vazio.")
    FIELDS = ["date", "ad_name", "spend", "impressions", "clicks",
              "actions_onsite_conversion_messaging_conversation_started_7d",
              "actions_lead", "actions_offsite_conversion_fb_pixel_lead"]

    # 1) buscar dados (Windsor REST) — ultimos 14 dias
    qs = urllib.parse.urlencode({"api_key": WINDSOR_KEY, "date_preset": "last_14d",
                                 "fields": ",".join(FIELDS)})
    with urllib.request.urlopen("https://connectors.windsor.ai/all?" + qs, timeout=90) as r:
        payload = json.loads(r.read().decode())
    raw = payload.get("data", payload if isinstance(payload, list) else [])
    raw = [x for x in raw if x.get("ad_name")]
    if not raw:
        raise SystemExit("Windsor retornou 0 linhas — nao enviar e-mail vazio.")

    # 2) transformar
    rows = [{
        "date": x["date"], "ad_name": x["ad_name"],
        "spend": x.get("spend") or 0, "impressions": x.get("impressions") or 0,
        "clicks": x.get("clicks") or 0,
        "conversations": x.get("actions_onsite_conversion_messaging_conversation_started_7d") or 0,
        "leads": (x.get("actions_lead") or 0) + (x.get("actions_offsite_conversion_fb_pixel_lead") or 0),
    } for x in raw]
    Path("rows.json").write_text(json.dumps(rows, ensure_ascii=False))

    # 3) gerar relatorio (PDF best-effort + report.html sempre)
    subprocess.run(["python3", "generate_report.py", "rows.json",
                    "--outdir", "./out", "--recipient", ",".join(TO)], check=True)
    e = json.loads(Path("out/email.json").read_text())

    pdf = Path(e["attachment"]) if e.get("attachment") else None
    has_pdf = bool(pdf and pdf.exists())

    if os.environ.get("DRY") == "1":
        print(f"DRY-RUN ok | assunto: {e['subject']} | PDF: {'sim' if has_pdf else 'NAO (fallback HTML)'}")
        return

    # 4) montar e enviar
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
        # fallback: relatorio COMPLETO no corpo do e-mail (sem depender de weasyprint)
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
                       "A Routine falhou ao gerar/enviar o relatorio.\n\n" + tb)
        except Exception as ex2:
            print("Tambem falhou ao enviar diagnostico:", ex2)
        raise
