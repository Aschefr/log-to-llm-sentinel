# Dashboard Page

The **Dashboard** is the home screen of Log-to-LLM Sentinel. It is a watch post: it first answers "is everything fine?", then shows only what needs an action.

The page updates by itself every 15 seconds, and right away when you come back to its tab. An update never closes or moves what you are reading.

---

## 1. Verdict
The heading of the page, also shown in the browser tab:
* **All good**: nothing needs attention.
* **The list of problems** (e.g. *1 open alert · 1 silent source*): red when a critical alert is open, orange for anything else (an alert rated below critical, a silent source, an autopilot failure, an Ollama problem).
* **Unknown state**: Sentinel stopped responding. The page never shows *All good* on old data, and offers **Retry**.
* **No source is being watched** or **Monitoring is off**: there is no rule yet, or every rule is disabled.

Below it: the number of sources watched and under observation, and the time of the last analysis.

---

## 2. Needs attention
Only what calls for an action, most serious first:
* **Open alerts**, with the AI's summary and how long they have been open. A row opens the alert on its rule page.
* **Autopilot failures**: the autopilot will try again on its own.
* **Ollama problems**: server unreachable, model not installed or no server configured.
* **Silent sources**: no line for longer than the rule's limit. A rule under autopilot observation is not listed: its rhythm is not known yet, so its silence is shown in the Autopilot block instead.
* **Unread events**: **View** opens the *Unread* filter of the history. They do not change the verdict.

---

## 3. Alerts over 7 days
One bar per day, split by severity, with the total, the comparison with the previous 7 days and the median return to normal over 30 days (the same MTTR as the Rules page). Hover or focus a bar for its values, or open **Show the table**. Days follow the server's UTC calendar.

---

## 4. Autopilot
The rules on autopilot with their state (reading the history, under observation until a given time, active, failed), then the latest decisions: settings defined, return-to-normal patterns applied or removed, keywords and exclusions changed or dismissed. A rule opens its page, where each decision can be reviewed.

---

## 5. Ollama
The configured model and whether the server responds, with the analysis running and those waiting.

---

## 6. History
Analyses grouped by **event**: analyses of a rule with the same error lines (or the same lines when none looks like an error), apart from variable parts such as dates, durations, addresses and identifiers. A new error line always makes a new event. Newest first, filtered by severity or unread.

* A row shows the latest summary, the number of occurrences and the event's highest severity. It opens the full explanation, the triggering lines, the actions (retry, notify, deepen in the chat, flag as not relevant, delete) and the list of the other occurrences.
* The unread count is a count of events. Opening one marks all its occurrences as read. It becomes unread again only if a new occurrence is more serious than what you have read.
* Nothing is deleted by grouping: statistics, the 7-day chart and the keyword tuning still count every analysis. Deleting a row deletes all the occurrences of that event.

The **Analyses** tab of a rule works the same way.

---

## Moved to Configuration
*Reset the MTTR* and *Delete all analyses* are now in **Configuration > Maintenance**, each with a confirmation.

The server statistics (CPU, RAM, uptime) stay in the navigation bar.
