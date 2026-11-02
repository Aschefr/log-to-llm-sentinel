# Autopilot

With the autopilot, you only give a rule its **source** (a log file, a syslog host or a webhook). Sentinel reads the log and the local AI sets up the rest of the rule, then keeps it up to date. The autopilot is on by default for a new rule.

---

## 1. Setting up a rule

When the rule is created, Sentinel reads the last 7 days of the log (current file and rotated `.1` file):

* The AI writes the rule's **name** and its **application context**: what this log contains (system, application, services) and what is normal in it. The context then helps the AI judge every alert.
* The [keyword arbiter](keyword-learning.md) chooses the **keywords** and **exclusions**, with the usual checks.
* Two settings come from the real rhythm of the lines, without the AI:
  * the **grouping window**: long enough to gather the lines of one event (twice the usual gap between them, 30 s to 5 min);
  * the **silence delay**: twice the longest usual silence of the source (1 h to 7 days), before warning that the source is silent.

The rule page shows *Autopilot · reading history* in its header, and the setup card in the **Keywords** tab. One notification sums up what was set.

## 2. Without history

A syslog host or a webhook that has not sent anything yet, or a log with less than 50 lines or 6 hours of history, is **watched for 24 hours** first (the lines already there count too). The AI then sets up the rule as above. If nothing arrives, the observation starts over, up to 7 days.

While the rule is not set up yet, it alerts on lines that carry an **explicit error level** (syslog priority error or worse, JSON `level`, `ERROR`/`FATAL`/`CRIT`…). These alerts show the keyword `[error level]`.

## 3. Afterwards

* The keyword arbiter keeps reviewing the rule every hour, as for any rule.
* Every week the log is read again: the grouping window and the silence delay are recomputed, and the context is rewritten only if the content of the log changed noticeably (less than half of its most frequent lines are still there).
* If the setup fails (AI unreachable…), the rule keeps alerting on error-level lines and Sentinel tries again 6 hours later. Cancelling the setup stops these retries; *Analyze history* in the Keywords tab resumes it.

## 4. You keep control

* In **Settings**, the fields set by the autopilot carry an **AI** badge. Change one and the autopilot no longer touches it.
* Keywords and exclusions are managed in the **Keywords** tab, where every change can be undone.
* The autopilot can be switched on or off in Settings. Switched on for an existing rule, it keeps the rule's name and a context you wrote, and manages the grouping window, the silence delay, and the context if it is empty.
* A *manual* rule (autopilot off) is set up by hand, as before; the quick templates fill a manual rule.
