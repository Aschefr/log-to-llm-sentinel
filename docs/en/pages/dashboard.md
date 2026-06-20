# Dashboard Page

The **Dashboard** is the home screen of Log-to-LLM Sentinel. It provides an immediate overview of the system's operational health, statistics, and recent notifications.

---

## 1. System Metrics Header
At the top right of the navigation bar, three real-time server statistics are shown:
* **App/Sys CPU**: Percentage of CPU utilized by the Sentinel process itself, contrasted with the host system's total CPU load.
* **RAM**: Megabytes of system RAM consumed by the Sentinel container.
* **Uptime**: Total duration the Sentinel server process has been running since the last startup.

---

## 2. Global Counters & Stats Cards
The main section of the dashboard displays four summary cards containing:
* **Total Rules**: The total number of monitoring rules configured in the system.
* **Active Rules**: The count of rules that are currently enabled and actively listening to logs.
* **Total Analyses**: The cumulative historical number of LLM log analysis records.
* **Today's Analyses**: The count of LLM analyses completed since 00:00 UTC today, color-coded by severity:
  * 🔴 **Critical** (High severity issues)
  * 🟡 **Warning** (Medium severity warnings)
  * 🔵 **Info** (Low severity or informational logs)

---

## 3. Mean Time to Resolution (MTTR)
The dashboard calculates the average time taken to resolve incidents across all monitored rules:
* **MTTR Display**: Shows the average duration of alerts from trigger time until incident resolution.
* **Reset MTTR Button (↺)**: Allows administrators to reset the MTTR history. Clicking this clears all past resolution logs from the statistical calculation.
  * *UI Control*: Triggers a custom modal confirmation (`showInlineConfirm`) to prevent accidental resets.

---

## 4. Log Files & Ingestion Status
A dedicated table shows the live state of all monitored files:
* **File Path**: The absolute target path on the host/container.
* **Status**: Indicators such as `Normal`, `In Alert` 🔴, or `AI Checking...` 🟡.
* **Last Log Received**: Shows the relative time elapsed since the last line was piped or read from this source (e.g., "Il y a 02:34" / "2:34 ago").

---

## 5. Recent Analyses Feed
A chronological list of recent alerts analyzed by Ollama:
* **Expandable Cards**: Click any card to expand the full LLM explanation, view the matched line, and see the exact keywords.
* **Severity Badges**: Color-coded banners depicting the incident severity.
* **Clear All Button (🗑️)**: Located at the top right of the feed. Permanently deletes all historical analysis records from the database.
  * *Confirmation Modal*: Requires clicking "Confirm" in the custom inline popup to execute.
