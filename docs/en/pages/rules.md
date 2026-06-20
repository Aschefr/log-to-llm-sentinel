# Rules Management

The **Rules** tab allows users to define what logs are monitored, set the parameters for AI analysis, and configure alert criteria.

---

## 1. Rule Creation and Editing
Click the **+ New Rule** button to open the rule setup modal. The modal contains the following input fields, switches, and options:

### General Settings
* **Name**: The display name of the rule (e.g., "SSH Authentication Alerts").
* **Source of Logs**: Select from three ingestion channels:
  1. **📁 Local File**: Reads a log file mounted from the host system.
     * *Browse Button*: Opens a file explorer to browse folders inside `/logs` or directories mounted in the container.
     * *Show Hidden Files*: Checkbox to display files starting with a dot (`.`).
  2. **🔗 Webhook API**: Generates a unique HTTP URL to post logs directly to Sentinel.
     * *Copy URL Button*: Instantly copies a `curl` template to the clipboard for integration with external scripts.
  3. **📨 Syslog Server**: Binds to the built-in UDP syslog receiver using a specified hostname or wildcard (`*`).

### Match Criteria
* **Keywords**: A comma-separated list of positive patterns that trigger an analysis (e.g., `failed, error, exception`).
  * *Keyword Suggestions*: Clicking on suggestions below the field automatically appends them.
* **Negative Keywords / Exclusion Patterns**: A comma-separated list of terms. If a log line contains any of these terms, the alert is bypassed (e.g., `healthcheck, GET /status`).
* **Auto-Learn Button (🤖)**: Opens the keyword auto-learning wizard to dynamically scan past logs and build keyword lists (see [Auto-Learning Feature](../features/keyword-learning.md)).

### AI Context Settings
* **Number of context lines**: Number of lines before/after the matched line sent to Ollama (default: `5`).
* **Application context**: A description of the service's purpose (e.g., "This is an Nginx reverse proxy serving our public website"). This context is appended to the Ollama prompt to improve diagnostic relevance.

### Inactivity Alerts
* **Send an alert if inactive**: Toggle switch to enable inactivity warnings.
* **Inactivity warning**: Numerical field specifying the maximum hours allowed without any logs before triggering an inactivity notification.

### Resolution Monitoring
* **Enable resolution detection**: Toggle switch to monitor when the rule returns to a normal status.
* **Resolution mode**:
  * *Inactivity timeout*: Automatically resolves the alert if no new errors occur for a specified number of minutes.
  * *Success keyword*: Resolves the alert if a log line matches one of the defined "Resolution patterns".
  * *Timeout OR Success keyword*: Resolves when either condition is met.
* **AI Validation**: If enabled, Ollama validates the log line to ensure the problem is truly solved before resolving the alert.

### Notification Threshold
* **Notification threshold (Min severity)**: Bypasses notifications if the LLM-evaluated severity is below this threshold (choose from `Info`, `Warning`, or `Critical`).
* **Notify on match**: Master toggle switch to enable/disable alerts for this rule.

---

## 2. Rule Actions & Cards
Each configured rule is displayed as a card on the Rules page, featuring:
* **Status Badges**: Shows `Enabled` (green check) or `Disabled` (red cross).
* **Test Button (🧪)**: Triggers a mock log entry matching the rule criteria to verify the pipeline.
  * *Stop Button*: Allows aborting the test in progress.
* **Edit Button (✏️)**: Opens the modal pre-filled with the rule's current configuration.
* **Delete Button (🗑️)**: Prompts a custom inline confirmation before removing the rule and all associated analyses.
