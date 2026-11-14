"""Send a report by every channel that is set up.

1. GitHub issue in this repo  -> GitHub emails the repo owner (works with no setup).
2. Direct email over SMTP     -> needs secrets SMTP_HOST, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO.
3. WhatsApp via CallMeBot     -> needs secrets CALLMEBOT_PHONE, CALLMEBOT_APIKEY (free, personal use).
"""
from __future__ import annotations

import os
import smtplib
import urllib.parse
from email.mime.text import MIMEText

import requests


def github_issue(title: str, body: str, labels: list[str]) -> str | None:
    token, repo = os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        print("GitHub issue skipped (not running in GitHub Actions)")
        return None
    owner = os.getenv("GITHUB_REPOSITORY_OWNER", repo.split("/")[0])
    url = f"https://api.github.com/repos/{repo}/issues"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    payload = {"title": title, "body": f"@{owner}\n\n{body}", "labels": labels}
    r = requests.post(url, headers=hdr, json=payload, timeout=30)
    if r.status_code == 422:  # labels not allowed -> try without
        payload.pop("labels")
        r = requests.post(url, headers=hdr, json=payload, timeout=30)
    if r.ok:
        url = r.json()["html_url"]
        print("Issue created:", url)
        return url
    print("Issue failed:", r.status_code, r.text[:300])
    return None


def smtp_email(subject: str, body: str) -> None:
    host, user, pwd, to = (os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "ALERT_EMAIL_TO"))
    if not all([host, user, pwd, to]):
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    with smtplib.SMTP_SSL(host, int(os.getenv("SMTP_PORT", "465")), timeout=30) as s:
        s.login(user, pwd)
        s.send_message(msg)
    print("Email sent to", to)


def whatsapp(text: str) -> None:
    phone, key = os.getenv("CALLMEBOT_PHONE"), os.getenv("CALLMEBOT_APIKEY")
    if not phone or not key:
        return
    for i in range(0, len(text), 1400):  # keep each message short
        q = urllib.parse.urlencode({"phone": phone, "text": text[i:i + 1400], "apikey": key})
        r = requests.get(f"https://api.callmebot.com/whatsapp.php?{q}", timeout=30)
        print("WhatsApp:", r.status_code)


def send(title: str, markdown: str, short_text: str, labels: list[str]) -> None:
    github_issue(title, markdown, labels)
    try:
        smtp_email(title, short_text)
    except Exception as e:  # noqa: BLE001
        print("Email failed:", e)
    try:
        whatsapp(f"{title}\n{short_text}")
    except Exception as e:  # noqa: BLE001
        print("WhatsApp failed:", e)
