# Meta-Analysis Page

![Meta-Analysis](../../../screenshots/log-to-llm-sentinel_meta_analysis.png)

An alert tells one moment. A **meta-analysis** re-reads weeks of alerts across several rules to say what a single alert cannot show: what comes back, what gets worse and what has gone.

Create as many as you need, one per machine or service to follow each over time, or one over everything to see the links between machines. Each writes **reports** on its own schedule.

---

## 1. The dashboard
One button per meta-analysis sits at the top, with the state of its latest report: *All good*, *Worth watching*, *Critical*, *Analysis failed*, or *No report yet*. Its color only reflects that state, and a meta-analysis whose rules no longer exist says so.

**The AI's report** comes first: the state, the period, a short summary, the highlights and the recommendations. The detailed analysis, the keywords, the alerts behind it and the exact context sent to the AI are one click away. Next to it:

* **Next report**: when it runs, how often, the rules and the sources it covers, with **Run now** and **Settings**.
* **Reports**: earlier reports with their state, newest first. Clicking one opens it in place.

**How it works** shows the path with the real numbers: the rules, their alerts over the period, the events once repeats are grouped, then the report. It also states the consequences: a meta-analysis changes neither rules nor alerts, the next report starts from this one, the AI is busy while it runs so alert analyses wait their turn, and whether a notification is sent.

A report arriving while you read does not replace the page: a banner offers to show it.

### What the alerts say
This part is computed directly from the alerts, without AI, so it is there before the first report. Choose a period of 7, 30, 90 or 180 days.

* **What changed** since the period before. Each button filters the list of events:
    * **New**: never seen before this period.
    * **Getting worse**: more serious than before, or at least twice as frequent with at least 3 more occurrences.
    * **Coming back**: seen before, not getting worse.
    * **Not seen anymore**: there before, absent since. Only claimed when the rule is running and its source still sends lines; otherwise nobody can know.
* **Activity by rule**: one row per rule, one column per day (per week beyond 31 days), in the worst severity of the day, stronger when there are more alerts. Rows side by side show what happens at the same time on different machines. Hover or tap a cell for its numbers; a table view is available.
* **Events in this period**, the most worrying first, each opening its alert.

Without alerts older than the period, nothing is compared rather than calling everything new, and the page says when alerts start. If only part of the previous period is left (old data removed, recent rule), the comparison is flagged as partial. An event flagged *not relevant* never counts as getting worse, and a non-critical one weighs as an info, as on the dashboard.

---

## 2. Next analysis
**Run now** opens a page showing when the next report runs and which period it covers, with **Run now** and **Stop**. Two optional steps stay folded:

* **Choose the period**: target a custom window.
* **Review and exclude events**: remove an event from this analysis, or add a remark for this analysis only. For lasting information, use a [note for the AI](../features/notes.md) on the rule.

If the analysis fails (AI unreachable…), the failure is shown and the period stays due: the next run covers it, and a scheduled meta-analysis tries again at most once an hour.

---

## 3. Settings
* **Name** and **followed rules**, grouped by source; a checkbox selects a whole source. None checked means all rules. When creating one, a source can be picked in one click. Deleting a rule removes it from its meta-analyses, and one left with no rule is disabled rather than widened to all rules.
* **Frequency**: every day, every week or every month, at a local time.
* **Enabled** and **Notify when done**.

The model, the context size and the other AI settings have a single place: the [Configuration](configuration.md) page.

---

## 4. How a Report Is Built
* **Aggregated context**: Instead of pasting every analysis, Sentinel groups identical events (same message once timestamps, IPs, ports and IDs are ignored) and sends one line per group: occurrence count, first/last occurrence, hours of occurrence, severity, unresolved count, a sample line and the latest analysis summary. Each rule gets its totals, resolution rate, MTTR and current alert state, and rules without alerts are listed.
* **Every analysis of the period counts**: most periods fit once grouped. If the context is still too small, the most serious rules and events come first, and the synthesis is told how many were left out.
* **Trend vs. previous run**: The TL;DR of the previous synthesis is included, so the model can flag what is new, persistent or gone.
* **Structured result**: Each synthesis has an overall status (🟢 OK / 🟠 Degraded / 🔴 Critical), a short TL;DR, key points, the detailed synthesis and recommendations.
* **Notifications**: Discord/Apprise notifications use the status, TL;DR and key points directly when the full synthesis is too long, without a second LLM call.
* **Preview exclusions & annotations** are applied server-side to the aggregated context.
