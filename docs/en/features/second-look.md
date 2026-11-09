# Second look during analysis

An alert is first analysed from its log lines alone. A line says a container stopped; it does not say whether it is still stopped two minutes later. With the second look on, the AI checks the real state of the machines before the alert is stored and notified.

It is off by default: turn it on in **Configuration › Diagnostics › Second look during analysis**. It uses the Docker and SSH connections of the alert's rule, so it needs at least one of them.

## What it does

* **When**: after the analysis of a new alert, or of an alert analysed again (for example after the AI was not answering). Warning and critical alerts by default; info alerts too if you tick the option.
* **What it may check**: only the read-only diagnostic tools. Container list, status and logs through the Docker connection; on the server through SSH, the checks of the diagnostic script (failed services, a service and its journal, mounts, fstab…). **At most 3 checks.** It never proposes an action, never searches the web and never reads other machines than the rule's connections.
* **What it decides**: *worse*, *confirmed*, *better* or *unclear*, with one or two sentences on what it found.
* **The severity**: by default the second look can only **raise** it, for example from warning to critical when the container is still down or restarts in a loop. If you allow it, it can also **lower** it when the service is back and healthy, which can put the alert under your notification threshold.

## What you see

* A small badge next to the severity: **checked**, **raised from warning** or **lowered from critical**. When the checks find things back to normal but the severity stays, because lowering is off or a tool output addressed the AI, the badge says **back to normal** and the section says *severity kept*, with the reason.
* At the end of the analysis, a **Second look** section with the finding and the list of what was checked. Notifications carry it too, and the short summary says why the severity changed.
* **Raw check results**, folded under the analysis, so you can read what the AI read.

## Safety

Log lines can be written by an attacker, and so can a container's logs. The second look is built for that:

* **An alert with a line addressed to the AI gets no second look at all**: that line could choose the checks. Its card shows **not checked**.
* Tool outputs are fenced like log lines in the prompt: data, never instructions.
* **The severity never goes down when a tool output addresses the AI**, even with the option on. The analysis says so.
* Only the rule's own connections, only read-only tools, at most 3 calls, 5 minutes at most. Past that, the alert keeps its first analysis.
* What looks like a password, a token or a private key is masked in the finding and in the raw results. The `fstab` check already masks credentials on the server.

## Cost

Each alert concerned takes two to five more calls to the model: two when the AI finds nothing useful to check (it is asked once more, because a small model tends to answer before checking anything), up to five with three checks. A repeat of the same event reuses the first verdict, with no call at all. If the AI is shared with other uses, keep the default scope (warning and critical).
