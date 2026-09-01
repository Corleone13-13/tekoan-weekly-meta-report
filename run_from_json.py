#!/usr/bin/env python3
"""Routine cloud Tekoan Meta Ads — envio via API HTTPS do Brevo (SMTP e
bloqueado no sandbox). Os dados do Windsor sao obtidos pelo AGENTE via conector
MCP (REST retorna 403) e salvos em um arquivo JSON; este script LE esse arquivo,
gera o relatorio e envia por e-mail.

Uso:  python3 run_from_json.py <windsor.json>
Aceita {"result":[...]}, {"data":[...]} ou [...].

Variaveis de ambiente: BREVO_API_KEY, MAIL_TO (virgula-separado),
SENDER_EMAIL (opcional, default bonamini.enzo1@gmail.com). DRY=1 pula o envio.
"""
import os, sys, json, base64, hashlib, subprocess, traceback, urllib.request, urllib.error
from pathlib import Path

BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
SENDER_EMAIL  = os.environ.get("SENDER_EMAIL", "bonamini.enzo1@gmail.com")
TO            = [a.strip() for a in os.environ.get("MAIL_TO", "").split(",") if a.strip()]
ALERT_TO      = [a.strip() for a in os.environ.get("ALERT_TO", "enzo@tekoan.com.br").split(",") if a.strip()]


def brevo_send(to_list, subject, html, text=None, attachment_path=None):
    body = {
        "sender": {"name": "Tekoan Reports", "email": SENDER_EMAIL},
        "to": [{"email": a} for a in to_list],
        "subject": subject,
        "htmlContent": html,
    }
    if text:
        body["textContent"] = text
    if attachment_path:
        p = Path(attachment_path)
        body["attachment"] = [{"content": base64.b64encode(p.read_bytes()).decode(),
                               "name": p.name}]
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(body).encode(),
        headers={"api-key": BREVO_API_KEY, "content-type": "application/json",
                 "accept": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.status, r.read().decode()


def main():
    if not BREVO_API_KEY:
        raise SystemExit("BREVO_API_KEY vazio.")
    if not TO:
        raise SystemExit("MAIL_TO vazio.")
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "windsor.json")
    payload = json.loads(src.read_text())
    raw = (payload.get("result") or payload.get("data") or []) if isinstance(payload, dict) else payload
    raw = [x for x in raw if x.get("ad_name")]
    if not raw:
        raise SystemExit("Sem linhas de anuncio no JSON do Windsor — nao enviar e-mail vazio.")

    rows = [{
        "date": x["date"], "ad_name": x["ad_name"],
        "spend": x.get("spend") or 0, "impressions": x.get("impressions") or 0,
        "clicks": x.get("clicks") or 0,
        "conversations": x.get("actions_onsite_conversion_messaging_conversation_started_7d") or 0,
        "leads": (x.get("actions_lead") or 0) + (x.get("actions_offsite_conversion_fb_pixel_lead") or 0),
        # funil de mensagens (aditivos; ausentes em fetch antigo → 0, nunca quebra)
        "first_reply": x.get("actions_onsite_conversion_messaging_first_reply") or 0,
        "blocks": x.get("actions_onsite_conversion_messaging_block") or 0,
        # metricas de video (aditivas; ausentes em criativo estatico/fetch antigo → 0,
        # nunca quebra). p25..p100 sao a curva de retencao decrescente.
        "video_3s": x.get("actions_video_view") or 0,
        "video_plays": x.get("video_play_actions_video_view") or 0,
        "thruplays": x.get("video_thruplay_watched_actions_video_view") or 0,
        "cost_per_thruplay": x.get("cost_per_thruplay_video_view") or 0,
        "v_p25": x.get("video_p25_watched_actions_video_view") or 0,
        "v_p50": x.get("video_p50_watched_actions_video_view") or 0,
        "v_p75": x.get("video_p75_watched_actions_video_view") or 0,
        "v_p95": x.get("video_p95_watched_actions_video_view") or 0,
        "v_p100": x.get("video_p100_watched_actions_video_view") or 0,
        "video_avg_time": x.get("video_avg_time_watched_actions_video_view") or 0,
        "creative_id": x.get("creative_id"),
    } for x in raw]
    # Guardas de conteudo ANTES de gerar e enviar (ver validate.py). Um ERRO vira
    # SystemExit, cai no except de __main__ e dispara o alerta [Tekoan][FALHA] so
    # para ALERT_TO — o relatorio do cliente simplesmente nao sai. Ficam aqui, e nao
    # no verificador de entrega, porque aquele roda 1h depois: la o e-mail errado ja
    # esta na caixa do cliente.
    # Import DENTRO de main() de proposito. A routine nao clona o repo, baixa uma
    # lista fixa de arquivos; se validate.py faltar nessa lista, um import no topo
    # mataria o script ANTES do try/except la embaixo — sem relatorio E sem alerta,
    # que e justamente o modo de falha mudo que este repo ja penou para eliminar.
    # Aqui dentro, o ImportError vira [Tekoan][FALHA] e nada e enviado ao cliente.
    from validate import validate, expected_end

    mode = os.environ.get("REPORT_MODE", "weekly")
    errors, warns = validate(rows, mode, expected_end(mode))
    for w in warns:
        print("AVISO:", w)
    if errors:
        raise SystemExit("Guardas de conteudo bloquearam o envio:\n  - "
                         + "\n  - ".join(errors))

    Path("rows.json").write_text(json.dumps(rows, ensure_ascii=False))

    # alcance/frequência por criativo (OPCIONAL): se o agente salvou windsor_period.json
    # (fetch agregado SEM data: ad_name, reach, frequency, cpp), transformamos em period.json.
    # Reach/frequência não são aditivos por dia, por isso vêm desse fetch à parte.
    period_arg = []
    psrc = Path("windsor_period.json")
    if psrc.exists():
        try:
            ppayload = json.loads(psrc.read_text())
            praw = (ppayload.get("result") or ppayload.get("data") or []) \
                if isinstance(ppayload, dict) else ppayload
            pmap = {}
            for x in praw:
                n = x.get("ad_name")
                if not n:
                    continue
                cid = x.get("creative_id")
                cid = str(cid) if cid not in (None, "", 0) else None
                # CHAVE = creative_id quando houver (peca), senao ad_name. Assim
                # o join de frequencia casa com o mesmo criterio do gerador
                # (piece_period por creative_id) e nao mistura pecas DISTINTAS que
                # compartilham o mesmo ad_name. Retrocompatível: sem creative_id,
                # agrupa por nome como antes.
                key = ("cid", cid) if cid else ("name", n)
                r = x.get("reach") or 0
                f = x.get("frequency") or 0
                c = x.get("cpp") or 0
                if key in pmap:
                    # Mesma chave = mesma peca em fetches parciais. Consolidamos:
                    # reach soma; frequency e cpp viram media ponderada por reach.
                    # Valido porque impressions = freq*reach e spend = cpp*reach/1000
                    # sao ambos aditivos, logo freq_total = sum(freq*reach)/sum(reach)
                    # e cpp_total = sum(cpp*reach)/sum(reach).
                    a = pmap[key]
                    tot = a["reach"] + r
                    if tot > 0:
                        a["frequency"] = (a["frequency"] * a["reach"] + f * r) / tot
                        a["cpp"] = (a["cpp"] * a["reach"] + c * r) / tot
                    a["reach"] = tot
                else:
                    pmap[key] = {"ad_name": n, "creative_id": cid,
                                 "reach": r, "frequency": f, "cpp": c}
            if pmap:
                Path("period.json").write_text(json.dumps(list(pmap.values()), ensure_ascii=False))
                period_arg = ["--period-data", "period.json"]
        except Exception as ex:
            print("WARN: windsor_period.json ignorado:", ex)

    # analise escrita pelo agente (OPCIONAL): se existir analysis.json no cwd, o
    # gerador renderiza essa analise; se nao existir, usa as regras fixas (fallback
    # natural). Nunca quebra: arquivo ausente/invalido cai no legacy dentro do gerador.
    analysis_arg = ["--analysis", "analysis.json"] if Path("analysis.json").exists() else []

    # histórico de períodos anteriores (OPCIONAL): se existir history.json no cwd, o
    # gerador renderiza a seção Evolução; se nao existir, a seção simplesmente nao aparece.
    history_arg = ["--history", "history.json"] if Path("history.json").exists() else []

    subprocess.run(["python3", "generate_report.py", "rows.json",
                    "--outdir", "./out", "--recipient", ",".join(TO),
                    "--mode", mode] + period_arg + analysis_arg + history_arg, check=True)
    e = json.loads(Path("out/email.json").read_text())
    pdf = Path(e["attachment"]) if e.get("attachment") else None
    has_pdf = bool(pdf and pdf.exists())

    if os.environ.get("DRY") == "1":
        print(f"DRY-RUN ok | assunto: {e['subject']} | PDF: {'sim' if has_pdf else 'NAO (fallback HTML)'}")
        return

    # Trava de idempotencia (fail-open): impede envio DUPLICADO se o agente da Routine
    # rodar o bloco de envio duas vezes na MESMA sessao. O marcador fica em /tmp/tekoan
    # (efemero por sessao) e e chaveado pelo assunto, que inclui periodo+modo — logo um
    # relatorio de outro dia/semana/mes NUNCA e bloqueado. So e gravado APOS envio com
    # sucesso, entao uma falha de envio jamais impede uma nova tentativa.
    # O caminho e ABSOLUTO de proposito: gravado no diretorio corrente, o marcador
    # some se o agente rodar o segundo envio de outro cwd — e a trava nao trava.
    mark_dir = Path("/tmp/tekoan")
    try:
        mark_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        mark_dir = Path(".")  # best-effort: sem /tmp gravavel, volta ao comportamento antigo
    marker = mark_dir / f".sent_{hashlib.md5(e['subject'].encode()).hexdigest()}"
    if marker.exists():
        print(f"JA ENVIADO nesta sessao — pulando envio duplicado | assunto: {e['subject']}")
        return

    if has_pdf:
        st, resp = brevo_send(TO, e["subject"], e["body_html"], e["body_text"], str(pdf))
        mode = "com PDF anexo"
    else:
        full_html = Path(e["report_html"]).read_text()
        st, resp = brevo_send(TO, e["subject"], full_html, e["body_text"])
        mode = "relatorio no corpo (sem PDF)"
    try:
        marker.write_text(e["subject"])
    except Exception:
        pass  # marcador e best-effort; nunca deve quebrar o fluxo de envio
    print(f"EMAIL ENVIADO para {', '.join(TO)} | {mode} | Brevo HTTP {st} {resp}")

    # O relatorio saiu, mas com ressalva: avisa SO o ALERT_TO (nunca o cliente).
    # Aviso impresso em log de run headless e aviso que ninguem le.
    if warns:
        nome = "mensal" if mode == "monthly" else "semanal"
        try:
            brevo_send(ALERT_TO,
                       f"[Tekoan][AVISO] Relatório {nome} saiu, mas com ressalva",
                       "<div style=\"font-family:Helvetica,Arial,sans-serif;font-size:14px;color:#1d2530\">"
                       f"<p>O relatório <b>{nome}</b> foi enviado normalmente para "
                       f"{', '.join(TO)}. As guardas de conteúdo não bloquearam o envio, "
                       "mas registraram o seguinte:</p><ul>"
                       + "".join(f"<li>{w.replace('<', '&lt;')}</li>" for w in warns)
                       + "</ul><p>Nada a fazer se cada ponto acima tiver explicação. "
                         "Se não tiver, vale conferir o dado antes do próximo período.</p></div>")
            print("Aviso enviado para:", ", ".join(ALERT_TO))
        except Exception as ex:
            print("WARN: falhou ao enviar o aviso (o relatorio JA foi enviado):", ex)


if __name__ == "__main__":
    try:
        main()
    except (Exception, SystemExit):
        tb = traceback.format_exc()
        print("FALHA:\n" + tb)
        nome = "mensal" if os.environ.get("REPORT_MODE") == "monthly" else "semanal"
        if os.environ.get("DRY") == "1":
            # DRY=1 promete ZERO e-mail. O caminho de alerta ignorava isso e tentava
            # falar com o Brevo mesmo em teste — com uma chave real no ambiente, um
            # teste local mandaria alerta de verdade.
            print("DRY-RUN: alerta de falha NAO enviado.")
            raise
        try:
            brevo_send(ALERT_TO,
                       f"[Tekoan][FALHA] Relatório {nome} de Meta Ads NÃO foi enviado",
                       "<div style=\"font-family:Helvetica,Arial,sans-serif;font-size:14px;color:#1d2530\">"
                       f"<p>O relatório <b>{nome}</b> de Meta Ads da Tekoan <b>falhou e não foi enviado</b> "
                       "nesta execução automática.</p>"
                       "<p>Causa provável (verificar): Windsor indisponível, chave/limite do Brevo, "
                       "restrição de IP reativada, ou rede do ambiente fora de \"Completo\". "
                       "Detalhe técnico abaixo.</p>"
                       "<pre style=\"font-size:11px;background:#f5f5f5;padding:8px;border-radius:4px\">"
                       + tb.replace("<", "&lt;") + "</pre></div>")
            print("Alerta de falha enviado para:", ", ".join(ALERT_TO))
        except Exception as ex2:
            print("Tambem falhou ao enviar o alerta:", ex2)
        raise
