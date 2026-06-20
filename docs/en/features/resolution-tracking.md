# Incident Resolution & AI Verdicts

Log-to-LLM Sentinel automatically monitors when active alerts return to a normal state, helping teams track Mean Time to Resolution (MTTR) and audit transient issues.

---

## 1. Resolution Trigger Modes
Configure how Sentinel determines that a problem is solved under the rule modal:
* **Inactivity Timeout**: The alert is resolved after a set period of minutes passes without any new matching errors.
* **Success Keyword**: Resolves when a log line matches one of the defined "Resolution patterns" (e.g., `success, connection established, database recovered`).
* **Timeout OR Success Keyword**: The incident resolves when the timeout expires or a success pattern is matched, whichever happens first.

---

## 2. AI Verification Verdicts
When a potential resolution is triggered, if **AI Validation** is enabled, Sentinel calls Ollama to double-check the logs:
* **Validation Check**: The AI reviews the last 5 logs and the triggering line to confirm if the service is fully restored.
* **Verdicts**:
  * `Accepted`: The AI confirmed the resolution. The rule transitions to `Normal` and notifications are sent.
  * `Rejected by AI`: The AI determined that the issue persists. Monitoring continues in alert status.
  * `Rejected (low confidence)`: The AI check had low certainty, so the incident remains unresolved.
  * `Manual resolution`: The user clicked "Mark as resolved" in the Monitor panel, forcing a resolution.
  * `False positive (user-marked)`: The user marked the resolution pattern as invalid, decrementing its pattern weight.

---

## 3. Pattern Auditing and Weights
Sentinel implements a self-learning weight system for resolution patterns:
* **Pattern Weights**: Each resolution pattern has a weight (starting at 1). If the user marks a pattern match as a false positive, its weight is decremented.
* **AI Audit Button (🤖 AI Audit)**: Located in the Monitor resolution panel. Submits active resolution patterns to the AI, allowing it to purge irrelevant rules and adjust weights based on the service's context history.
