# Global Configuration

The **Configuration** tab handles global integration credentials, LLM parameters, maintenance settings, and system backup/restoration.

---

## 1. Notification & Alerts Config
* **Primary Notification Method**: Dropdown selector to choose between `Email (SMTP)`, `Apprise`, or `Discord Webhook`.
* **SMTP Settings**:
  * *SMTP Host / Port*: Address of your email provider (e.g., `smtp.gmail.com`).
  * *Encryption Mode*: Choose `TLS / STARTTLS` (Port 587), `SSL / TLS` (Port 465), or none.
  * *Credentials*: Username and Password fields.
  * *Recipient*: Address where alert emails are sent.
  * *Test SMTP Button*: Sends a test email to verify credentials.
* **Apprise Settings**:
  * *Apprise URL*: The target URL of your Apprise agent endpoint.
  * *Apprise Tags*: Comma-separated tags to route alerts.
  * *Character Limit (AI Summary)*: Sets the character threshold before compressing text for mobile-friendly alerts.
  * *Test Apprise Button*: Sends a test notification.
* **Discord Settings**:
  * *Discord Webhook URL*: Paste your Discord channel integration webhook.
  * *Test Discord Button*: Sends a test message.

---

## 2. Ollama Settings
* **Ollama URL**: The access endpoint of your Ollama instance (default: `http://host.docker.internal:11434` for Docker hosts).
* **Model Selection**: Dropdown list populated dynamically by queries to Ollama.
* **Model (Manual Override)**: Text field to specify a model manually (e.g., `llama3:latest`).
* **System Prompt**: Default diagnostic prompt sent to Ollama for all rule triggers.
* **Performance Settings**:
  * *Temperature*: Creativity slider (0.0 = deterministic, 1.0 = creative).
  * *Context size*: Context window limits (e.g., `4096` tokens).
  * *Quick Profiles*: Setup templates (Eco, Balanced, GPU/Gamer) to quickly adjust temperature/tokens.
* **Model Management (Download)**: Enter a model tag (e.g., `gemma2:2b`) and click **Download** to pull a new model onto the host.

---

## 3. Syslog Receiver & Relay Setup
Sentinel listens on UDP port 514 inside the container (mapped to host port 10514 by default):
* **Enable Syslog Receiver**: Master checkbox to activate the UDP listener.
* **Forwarding Address (Relay)**: Enter an IP:Port to forward raw incoming Syslog packets to another destination (e.g., a secondary NAS or SIEM server).
* **Copy Helpers**: Displays the host's LAN IP address and ports to quickly configure remote syslog clients (e.g., Unraid, routers, switches).

---

## 4. Maintenance, Backup & Restore
* **Auto-Delete Old Data**: Toggle switch to purge old logs.
* **Retention Period**: Purge frequency (`1 week`, `1 month`, `6 months`, `1 year`).
* **Clean Up Now Button**: Triggers an immediate database cleanup.
* **Export Configuration**:
  * *Include history*: Checkbox to export historical logs and chat databases.
  * *Generate Zip Button*: Generates and downloads a ZIP backup of rules, configs, and history.
* **Import Configuration**:
  * *Select Backup File*: Choose a previously exported ZIP file.
  * *Restore Backup Button*: Restores all rules, settings, and histories, overwriting current database entries.
