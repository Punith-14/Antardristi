"""
Sending the two emails the app sends: "confirm your email" and "reset your
password". Plain SMTP with STARTTLS - for Gmail, smtp.gmail.com port 587 and
an app password (Google account -> Security -> App passwords).

When SMTP is not configured nothing is sent: the link is written to the
server log instead, so an administrator can pass it on, and verification is
switched off (settings.email_verification_required) so nobody is locked out.

Sending happens on a background thread: a slow mail server must not make
sign-up or "forgot password" hang, and a failure is logged, not shown - the
page says the same thing whether or not an account exists.
"""

import logging
import os
import smtplib
import ssl
import threading
from email.message import EmailMessage

from core import settings

log = logging.getLogger("antardrishti")

_sent = []           # tests read what would have been sent


def link(base_url, route, token):
    """An app link such as https://host/#/reset?token=..."""
    root = (settings.app_url() or base_url or "").rstrip("/")
    return f"{root}/#/{route}?token={token}"


def _send(message):
    config = settings.smtp()
    _sent.append(message)
    if not config:
        log.warning("email not configured; would have sent", extra={"fields": {
            "to": message["To"], "subject": message["Subject"],
            "link": message.get("X-Antardrishti-Link")}})
        return
    try:
        with smtplib.SMTP(config["host"], config["port"], timeout=20) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if config["user"]:
                smtp.login(config["user"], config["password"])
            smtp.send_message(message)
    except Exception as exc:                     # noqa: BLE001 - logged, never shown
        log.error("email failed", extra={"fields": {"to": message["To"], "error": type(exc).__name__}})


def send(to, subject, text, html, url=None, background=True):
    config = settings.smtp() or {}
    message = EmailMessage()
    message["From"] = config.get("sender") or "Antardrishti <no-reply@antardrishti.local>"
    message["To"] = to
    message["Subject"] = subject
    if url:
        message["X-Antardrishti-Link"] = url
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    if background and os.environ.get("MAIL_SYNC") != "1":
        threading.Thread(target=_send, args=(message,), daemon=True).start()
    else:
        _send(message)
    return message


def _html(heading, body, button, url, foot):
    return f"""<!doctype html><html><body style="margin:0;background:#F7F9FC;font-family:Segoe UI,Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 12px">
<table width="520" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:14px;border:1px solid #DDE3EA">
<tr><td style="background:#0B2545;color:#fff;padding:18px 24px;font-size:20px;font-weight:700;border-radius:14px 14px 0 0">Antardrishti</td></tr>
<tr><td style="padding:24px;color:#1F2937;font-size:15px;line-height:1.6">
<h2 style="color:#0B2545;margin:0 0 12px">{heading}</h2><p>{body}</p>
<p style="margin:24px 0"><a href="{url}" style="background:#0F8B8D;color:#fff;padding:12px 22px;border-radius:10px;text-decoration:none;font-weight:700">{button}</a></p>
<p style="color:#5F6B7A;font-size:13px">If the button does not work, paste this into your browser:<br><a href="{url}" style="color:#0F8B8D;word-break:break-all">{url}</a></p>
<p style="color:#5F6B7A;font-size:13px">{foot}</p></td></tr></table></td></tr></table></body></html>"""


def send_verification(user, url, background=True):
    name = (user.get("full_name") or "").split(" ")[0] or "there"
    text = (f"Hello {name},\n\nConfirm your email to start using Antardrishti:\n{url}\n\n"
            "The link works once and expires in 24 hours. If you did not sign up, ignore this email.")
    html = _html(f"Welcome, {name}", "Confirm your email address to start using Antardrishti.",
                 "Confirm my email", url, "The link works once and expires in 24 hours. "
                 "If you did not sign up, you can ignore this email.")
    return send(user.get("email") or user["username"], "Confirm your email for Antardrishti", text, html,
                url, background)


def send_reset(user, url, background=True):
    name = (user.get("full_name") or "").split(" ")[0] or "there"
    text = (f"Hello {name},\n\nSomeone asked to reset your Antardrishti password. To choose a new one:\n{url}\n\n"
            "The link works once and expires in 30 minutes. If it was not you, ignore this email - "
            "your password stays the same.")
    html = _html("Reset your password", "Someone asked to reset your Antardrishti password. "
                 "Choose a new one with the button below.", "Choose a new password", url,
                 "The link works once and expires in 30 minutes. If it was not you, ignore this email - "
                 "your password stays the same.")
    return send(user.get("email") or user["username"], "Reset your Antardrishti password", text, html,
                url, background)
