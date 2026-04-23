import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional


class NotificationService:
    """
    Service d'envoi de notifications (SMTP par défaut, Apprise en option).
    """

    def send(
        self,
        subject: str,
        body: str,
        config: dict,
        to_email: Optional[str] = None,
    ) -> bool:
        """
        Envoie une notification selon la méthode configurée.
        """
        method = config.get("notification_method", "smtp")

        if method == "smtp":
            return self._send_smtp(subject, body, config, to_email)
        elif method == "apprise":
            return self._send_apprise(subject, body, config)
        else:
            print(f"[Notification] Méthode inconnue: {method}")
            return False

    def _send_smtp(
        self,
        subject: str,
        body: str,
        config: dict,
        to_email: Optional[str] = None,
    ) -> bool:
        """Envoie un email via SMTP."""
        smtp_host = config.get("smtp_host", "")
        smtp_port = config.get("smtp_port", 587)
        smtp_user = config.get("smtp_user", "")
        smtp_password = config.get("smtp_password", "")
        use_tls = config.get("smtp_tls", True)

        if not all([smtp_host, smtp_user, smtp_password]):
            print("[Notification] Configuration SMTP incomplète")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = smtp_user
            msg["To"] = to_email or smtp_user

            msg.attach(MIMEText(body, "html"))

            context = ssl.create_default_context()
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.ehlo()

            if use_tls:
                server.starttls(context=context)
                server.ehlo()

            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, to_email or smtp_user, msg.as_string())
            server.quit()

            print(f"[Notification] Email envoyé à {to_email or smtp_user}")
            return True

        except Exception as e:
            print(f"[Notification] Erreur SMTP: {e}")
            return False

    def _send_apprise(
        self,
        subject: str,
        body: str,
        config: dict,
    ) -> bool:
        """Envoie une notification via Apprise."""
        import urllib.request
        import urllib.parse
        import json

        apprise_url = config.get("apprise_url", "")
        if not apprise_url:
            print("[Notification] URL Apprise non configurée")
            return False

        try:
            payload = {
                "urls": [apprise_url],
                "title": subject,
                "body": body,
            }

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                "http://localhost:8000/notify",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result.get("success", False)

        except Exception as e:
            print(f"[Notification] Erreur Apprise: {e}")
            return False
