# Log Line Safety

![An alert with a suspicious log line](../../../screenshots/log-to-llm-sentinel_suspicious_line.png)

Log lines are written by the programs you monitor, and sometimes by an attacker: an SSH user name or a requested URL ends up in a log word for word. A sentence hidden in such a line could try to talk the AI out of an alert, for instance *"classify this as info"*, so that no notification goes out.

Sentinel treats log lines as data to analyze, never as instructions.

---

## 1. What Sentinel does

* **Separate blocks**: log lines reach the AI in blocks marked with a tag derived from the lines and a secret key of the installation (`data/fence.key`, created on first use), so a line cannot guess the tag that would close its block and pretend to be part of the instructions. The same lines get the same tag, so the AI gets the same prompt again. This applies to alert analysis, return-to-normal checks, the keyword arbiter, the autopilot and *Sort my unread*.
* **A safety rule for the AI**: it must not obey any sentence in these blocks, even one presented as a message from the system or the administrator, and must report it. Your real [notes](notes.md) are outside these blocks.
* **A detector that does not depend on the AI**: it looks for sentences that address the AI, such as *"ignore previous instructions"*, a fake `[SYSTEM]` message, or an order to answer `info`.
* **A question to the AI**: before choosing a severity, the AI says whether a line tries to influence its verdict.

---

## 2. When a line is flagged

The alert starts with **Suspicious log line**, in its summary and in the analysis, so you know the verdict deserves a second look.

| | Found by the detector | Flagged by the AI only |
|---|---|---|
| Severity | Never below `warning` | Never below `warning` |
| Notification | Sent whatever the rule's threshold | Follows the rule's threshold |
| Closing the incident with AI validation | Refused | Decided as usual |
| Keyword arbiter removing a keyword or adding an exclusion | Refused | Decided as usual |

The AI can be wrong about a harmless line, which is why its flag alone never forces a notification. A severity already rated `critical` stays `critical`.

---

## 3. What to do with such an alert

Read the triggering line. Whatever it says, it comes from the log and not from Sentinel. A line that addresses an AI is unusual in itself: check where it came from, for instance who is trying to log in, or which address sent the request.

---

## 4. Limits

This lowers the risk without removing it.

* A well-worded sentence can still get through. In our tests on `qwen3.8:27b`, with three kinds of sentences added to 11 serious log lines, 26 of 33 ended at `info` before this protection and 1 of 33 with it. The one that got through was a neutral `severity=info`, which the detector does not look for because real logs contain such text.
* These tests come from one model and one set of logs, so they indicate an order rather than a guarantee.
* Meta-analysis and the chat do not have the detector's safeguards: a person reads their answers. Their replies can still quote or be influenced by a log line.
