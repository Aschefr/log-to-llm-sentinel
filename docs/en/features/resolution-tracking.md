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
When a potential resolution is triggered, if **AI Validation** is enabled, Sentinel asks Ollama for a structured verdict (resolved, confidence, explanation):
* **What the AI receives**: The incident (start, duration, time since the last error, summaries of its unresolved analyses), the trigger (timeout reached, or the line that matched a resolution pattern), the number of normal lines received since the last error (a silent log may mean the service stopped), and the last 30 lines of the source.
* **Verdicts**:
  * `Accepted`: The AI confirmed the resolution. The rule transitions to `Normal` and notifications are sent.
  * `Rejected by AI`: The AI determined that the issue persists. Monitoring continues in alert status.
  * `Rejected (low confidence)`: The AI's confidence was below 50% (30% for a pattern with a weight of 3 or more), so the incident remains unresolved.
  * `Manual resolution`: The user clicked "Mark as resolved" in the Monitor panel, forcing a resolution.
  * `False positive (user-marked)`: The user marked an accepted resolution as wrong: the weights of the patterns involved are decremented and, if the rule is still `Normal`, it goes back to alert with the analyses that resolution had closed.
* **Safeguards**:
  * Only one attempt per rule runs at a time, and an attempt is abandoned if a new error arrives during the AI check.
  * After a rejection, the next timeout attempt waits a full timeout period; a pattern-triggered attempt waits 5 minutes.
  * Lines that trigger the rule (error keyword, not excluded) never count as resolution lines.
  * Verdicts older than 30 days are purged; the 20 most recent per rule are always kept.

---

## 3. Pattern Auditing and Weights
Sentinel implements a self-learning weight system for resolution patterns:
* **Pattern Weights**: Each resolution pattern has a weight (starting at 1). It grows by 1 each time the pattern triggers a resolution validated by the AI, and decreases when the user marks a resolution as a false positive or when the pattern has not been validated for 14 days (it is removed at 0).
* **Self-Learning**: After a pattern-triggered or manual resolution with AI Validation enabled, the AI may suggest new patterns from the normal log lines. Suggestions are discarded when they are generic (e.g. `exiting`, `Starting`, `message repeated`), overlap an error keyword, appear in the incident's error lines, or do not occur in the logs. Timeout resolutions do not feed the learning.
* **AI Audit Button (🤖 AI Audit)**: Located in the Monitor resolution panel. Submits active resolution patterns to the AI, allowing it to purge irrelevant patterns based on the rule's context.
