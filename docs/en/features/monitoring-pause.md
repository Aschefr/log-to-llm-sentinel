# Pausing Monitoring

The switch in the header turns monitoring on and off for the whole application, for a maintenance window or to keep the AI free for something else. Pausing asks for confirmation; resuming is immediate. The state survives restarts.

## While paused
Nothing automatic uses the AI or sends a notification:

* no alert analysis, and an event waiting in its grouping delay is dropped;
* no return-to-normal check;
* no keyword review and no autopilot maintenance;
* no scheduled meta-analysis: its period stays due, and the next report covers it;
* no silence warning.

Sources keep being read, so a silent source is still known as silent, and lines keep being written to their logs. What you start yourself still works: the chat, an investigation, a manual meta-analysis, an autopilot setup on a new rule. An analysis already running when you pause finishes normally.

## When it resumes
* **Lines received during the pause are not analyzed.** This avoids a flood of AI calls and notifications about things that may be over. They remain in the logs and the chat can read them.
* **Open incidents are not closed because of the pause.** Nobody looked at the lines, so that silence proves nothing: the calm delay continues from where it stopped.
* A source still silent is reported at the next check.

## Where it shows
The switch is amber on every page, and its tooltip says since when. The dashboard shows *Monitoring paused* as its verdict, also in the browser tab, and never *All good*. If Sentinel stops responding, the switch says the state is unknown.

A backup does not contain this state, so restoring one never pauses monitoring silently.

## Quiet mode
The *Quiet* switch, under *Monitoring*, keeps monitoring on but sends only critical alerts. Everything else stays visible in Sentinel and is not notified: warning and info alerts, keyword changes, autopilot adjustments, resolutions, silent sources and scheduled meta-analyses. What you send yourself still goes out, such as a test or a resent alert.

Two notices pass in quiet mode as well, because Sentinel cannot judge alerts while they are due: the AI is not answering, and the AI is answering again (see [When the AI is not answering](ai-outage.md)). The switch is blue when quiet mode is on, and its tooltip says since when. A backup does not contain this state either.
