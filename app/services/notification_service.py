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
        logger.debug("Notification", f"Méthode de notification : {method}")

        if method == "smtp":
            return self._send_smtp(subject, body, config, to_email)
        elif method == "apprise":
            return self._send_apprise(subject, body, config)
        elif method == "discord":
            return self._send_discord(subject, body, config)
        else:
            logger.warning("Notification", f"Méthode inconnue: {method}")
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

        logger.debug("Notification", (
            f"SMTP → host={smtp_host} port={smtp_port} user={smtp_user} "
            f"mode={ssl_mode} recipient={recipient}"
        ))

        if not all([smtp_host, smtp_user, smtp_password]):
            logger.error("Notification", "Configuration SMTP incomplète (host/user/password manquants)")
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
                logger.debug("Notification", f"Connexion SMTP_SSL sur {smtp_host}:{smtp_port}")
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, context=context)
                server.ehlo()
            else:
                # Connexion plain puis upgrade STARTTLS si demandé
                logger.debug("Notification", f"Connexion SMTP plain sur {smtp_host}:{smtp_port}")
                server = smtplib.SMTP(smtp_host, smtp_port)
                server.ehlo()
                if ssl_mode == "starttls":
                    logger.debug("Notification", "Lancement STARTTLS")
                    server.starttls(context=context)
                    server.ehlo()

            server.login(smtp_user, smtp_password)
            logger.debug("Notification", "Login SMTP réussi")
            server.sendmail(smtp_user, recipient, msg.as_string())
            server.quit()

            logger.info("Notification", f"Email envoyé à {recipient}")
            return True

        except Exception as e:
            logger.error("Notification", f"Erreur SMTP: {e}")
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
            logger.warning("Notification", "URL Apprise non configurée")
            return False

        logger.debug("Notification", f"Apprise POST → {apprise_url}")

        try:
            # Discord et d'autres services ont des limites de caractères (souvent 2000).
            # On utilise la limite configurée (apprise_max_chars).
            max_chars = config.get("apprise_max_chars", 1900)
            safe_body = body
            if len(body) > max_chars:
                trunc_msg = "\n\n⚠️ **Message tronqué car trop long...**"
                safe_body = body[:max_chars - len(trunc_msg)] + trunc_msg

            payload = {
                "title": subject,
                "body": safe_body,
                "type": "info",
                "format": "markdown",  # On utilise maintenant le markdown pour Apprise (plus lisible sur Discord/Telegram)
            }
            
            apprise_tags = config.get("apprise_tags", "").strip()
            if apprise_tags:
                payload["tags"] = apprise_tags

            data_str = json.dumps(payload)
            logger.debug("Notification", f"Apprise Payload: {data_str}")
            
            data = data_str.encode("utf-8")
            req = urllib.request.Request(
                apprise_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                status = getattr(response, "status", 200)
                logger.debug("Notification", f"Apprise réponse HTTP {status}")
                return 200 <= status < 300

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except:
                pass
            logger.error("Notification", f"Erreur Apprise HTTP {e.code}: {e.reason} - {error_body}")
            return False
        except Exception as e:
            logger.error("Notification", f"Erreur Apprise: {e}")
            return False

    def _send_discord(
        self,
        subject: str,
        body: str,
        config: dict,
    ) -> bool:
        """
        Envoie une notification via un Webhook Discord.
        """
        webhook_url = config.get("discord_webhook_url", "")
        if not webhook_url:
            logger.warning("Notification", "URL Webhook Discord non configurée")
            return False

        logger.debug("Notification", f"Discord POST → {webhook_url}")

        try:
            # Discord limit is 2000 chars per embed description
            max_chars = 1900
            safe_body = body
            if len(body) > max_chars:
                trunc_msg = "\n\n⚠️ **Message tronqué car trop long...**"
                safe_body = body[:max_chars - len(trunc_msg)] + trunc_msg

            color = 0x3498db
            if "CRITICAL" in subject or "🚨" in subject:
                color = 0xe74c3c
            elif "WARNING" in subject or "⚠️" in subject:
                color = 0xf1c40f
            elif "Test" in subject:
                color = 0x2ecc71

            payload = {
                "username": "Log-to-LLM Sentinel",
                "embeds": [
                    {
                        "title": subject,
                        "description": safe_body,
                        "color": color
                    }
                ]
            }

            data_str = json.dumps(payload)
            data = data_str.encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Log-to-LLM-Sentinel/1.0"
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                status = getattr(response, "status", 200)
                logger.debug("Notification", f"Discord réponse HTTP {status}")
                return 200 <= status < 300

        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except:
                pass
            logger.error("Notification", f"Erreur Discord HTTP {e.code}: {e.reason} - {error_body}")
            return False
        except Exception as e:
            logger.error("Notification", f"Erreur Discord: {e}")
            return False
