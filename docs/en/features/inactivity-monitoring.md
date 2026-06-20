# Inactivity Monitoring

**Inactivity Monitoring** is a feature in Log-to-LLM Sentinel designed to alert users when a service stops logging, indicating a crashed application, stuck daemon, or stopped container.

---

## 1. How It Works
Rather than checking for positive error patterns, the Inactivity Monitor watches the timestamps of incoming log lines:
* **Background Check**: A background scheduler runs a check across all rules every minute.
* **Timestamp Evaluation**: It compares the current UTC time against the timestamp of the last logged line.
* **Alert Trigger**: If the difference exceeds the configured threshold, a critical alert is triggered.

---

## 2. Setting Up Inactivity Gaps
* **Activation**: Toggle **Send an alert if inactive** in the rule modal.
* **Warning Delay (Hours)**: Input the maximum hours allowed without log activity before an alert is dispatched.
* **Notifications**: When triggered, a notification is sent via Apprise, Discord, or SMTP.
  * *Subject Format*: `[Sentinel ALERT] Inactivity detected: <rule_name>`
  * *Body Content*: Details when the last line was received and lists the monitored path.
