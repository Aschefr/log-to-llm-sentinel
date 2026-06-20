# Monitor Page

![Monitor Page](../../../screenshots/log-to-llm-sentinel_monitor.png)

The **Monitor** page is a real-time console designed for log streaming, filtering, and manual diagnostic interventions.

---

## 1. Sidebar Rules Selection
The left panel lists all configured rules:
* **All Rules**: Selects a global view across all files.
* **Active Status Dots**: Displays green (Active), red (Alert), or yellow (AI Resolving) dots indicating the status of each rule.
* **Search Input (🔍)**: Search by log keywords or rule names to filter the list.

---

## 2. Real-Time Log Streaming & Buffer Controls
The main section features a live log window showing incoming lines:
* **Freeze / Resume Button**: Freezes the log stream to allow inspection of a specific log sequence. Clicking "Resume" unblocks the scroll and updates the window with accumulated lines.
* **Filter View Toggle**:
  * *View: Matches Only*: Hides standard lines, displaying only log entries that trigger rule keywords.
  * *View: Full Log*: Displays the entire log stream, highlighting matched lines in yellow.
* **Keyword Highlights**: Keywords that matched the rules are highlighted with a distinct background for quick visual scanning.

---

## 3. Search & Log Diagnostics Panel
* **Search Input Field**: Enter a specific Detection ID (e.g., `#abc123`) to fetch its context instantly.
* **Log Lines Range Slider**: Adjusts how many surrounding lines are displayed in the viewport.
* **Analyze with Ollama**: If a log line matches keywords but lacks an automatic LLM review (e.g., if notifications were disabled), this button triggers a manual diagnostic call to the local model.
* **Manual Resolution Card**: If the rule is in alert status, users can click **✅ Mark as resolved** to force a return to normal state.
  * *AI history*: Displays past resolution verdicts, confidence scores, and pattern weights.
