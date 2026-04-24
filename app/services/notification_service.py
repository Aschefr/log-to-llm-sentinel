import smtplib
import ssl
import urllib.request
import urllib.error
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from app import logger


class NotificationService:
    """
    Service d'envoi de notifications (SMTP par défaut, Apprise en option).
    Modes SMTP supportés : 'ssl' (port 465), 'starttls' (port 587), 'none' (plain).
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
        logger.debug("NotificationService", f"Méthode de notification : {method}")

        if method == "smtp":
            return self._send_smtp(subject, body, config, to_email)
        elif method == "apprise":
            return self._send_apprise(subject, body, config)
        else:
            logger.warning("NotificationService", f"Méthode inconnue: {method}")
            return False

    def _send_smtp(
        self,
        subject: str,
        body: str,
        config: dict,
        to_email: Optional[str] = None,
    ) -> bool:
        """Envoie un email via SMTP (SSL, STARTTLS, ou plain)."""
        smtp_host = config.get("smtp_host", "")
        smtp_port = config.get("smtp_port", 587)
        smtp_user = config.get("smtp_user", "")
        smtp_password = config.get("smtp_password", "")
        smtp_recipient = config.get("smtp_recipient", "")

        # ssl_mode prioritaire ; fallback sur le champ legacy smtp_tls
        ssl_mode = config.get("smtp_ssl_mode", None)
        if not ssl_mode:
            ssl_mode = "starttls" if config.get("smtp_tls", True) else "none"

        recipient = to_email or smtp_recipient or smtp_user

        logger.debug("NotificationService", (
            f"SMTP → host={smtp_host} port={smtp_port} user={smtp_user} "
            f"mode={ssl_mode} recipient={recipient}"
        ))

        if not all([smtp_host, smtp_user, smtp_password]):
            logger.error("NotificationService", "Configuration SMTP incomplète (host/user/password manquants)")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = smtp_user
            msg["To"] = recipient
            msg.attach(MIMEText(body, "html"))

            context = ssl.create_default_context()

            if ssl_mode == "ssl":
                # Connexion directement en SSL (port 465)
                logger.debug("NotificationService", f"Connexion SMTP_SSL sur {smtp_host}:{smtp_port}")
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, context=context)
                server.ehlo()
            else:
                # Connexion plain puis upgrade STARTTLS si demandé
                logger.debug("NotificationService", f"Connexion SMTP plain sur {smtp_host}:{smtp_port}")
                server = smtplib.SMTP(smtp_host, smtp_port)
                server.ehlo()
                if ssl_mode == "starttls":
                    logger.debug("NotificationService", "Lancement STARTTLS")
                    server.starttls(context=context)
                    server.ehlo()

            server.login(smtp_user, smtp_password)
            logger.debug("NotificationService", "Login SMTP réussi")
            server.sendmail(smtp_user, recipient, msg.as_string())
            server.quit()

            logger.info("NotificationService", f"Email envoyé à {recipient}")
            return True

        except Exception as e:
            logger.error("NotificationService", f"Erreur SMTP: {e}")
            return False

    def _send_apprise(
        self,
        subject: str,
        body: str,
        config: dict,
    ) -> bool:
        """
        Envoie une notification via Apprise API.
        URL attendue : http://<host>:<port>/notify/<tag>
        ou            http://<host>:<port>/notify/?tag=<tag>
        """
        apprise_url = config.get("apprise_url", "")
        if not apprise_url:
            logger.warning("NotificationService", "URL Apprise non configurée")
            return False

        logger.debug("NotificationService", f"Apprise POST → {apprise_url}")

        try:
            payload = {
                "title": subject,
                "body": body,
                "type": "info",
                "format": "html",
            }
            
            apprise_tags = config.get("apprise_tags", "").strip()
            if apprise_tags:
                payload["tag"] = apprise_tags

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                apprise_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                status = getattr(response, "status", 200)
                logger.debug("NotificationService", f"Apprise réponse HTTP {status}")
                return 200 <= status < 300

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except:
                pass
            logger.error("NotificationService", f"Erreur Apprise HTTP {e.code}: {e.reason} - {error_body}")
            return False
        except Exception as e:
            logger.error("NotificationService", f"Erreur Apprise: {e}")
            return False
