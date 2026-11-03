# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

People who run their own servers (homelab, Unraid, Docker) and want to know when something goes wrong without reading logs. They are often away from the machine, come back to the page after a notification, and use it on a desktop or a phone. They read French or English.

## Product Purpose

Log to LLM Sentinel watches logs (files, syslog, webhooks), detects the lines that matter and has a local LLM explain them. Success: the user trusts that silence means nothing needs attention, and that an alert is worth opening.

## Positioning

The AI runs locally through Ollama, so logs stay on the user's infrastructure. It does more than summarize: it tunes keywords, detects the return to normal, and the autopilot sets up and maintains a whole rule from its source alone.

## Operating Context

- Self-hosted container on a local network, reached over plain HTTP.
- Runs for months unattended; notifications bring the user back to the page.
- Volumes vary widely: a source can send thousands of lines a day or a handful a week.
- A single LLM call runs at a time, and an analysis can take minutes.

## Capabilities and Constraints

- Rules, alerts with an AI analysis, automatic resolution, chat and investigation, meta-analysis, read-only diagnostics, autopilot.
- FastAPI, SQLite, Jinja2 templates, vanilla JavaScript, no build step.
- French and English interface, light and dark themes.
- Write actions proposed by the LLM always need explicit confirmation; the automatic alert analysis uses no tools.
- The page must keep working when the internet is unreachable, apart from optional extras such as web fonts.

## Brand Commitments

- Name: Log to LLM Sentinel.
- "If we promise an autopilot, it must really drive every aspect of a rule on its own, without intervention and with the greatest possible relevance."

## Evidence on Hand

No customers, testimonials or benchmarks. Screenshots must not show real production log lines.

## Product Principles

1. Calm is a result: nothing competes for attention when nothing needs it.
2. Never claim more than is known: an unknown state is shown as unknown, never as fine.
3. The AI acts on its own and shows what it did, and every decision can be reviewed.
4. Reading is never interrupted: live updates must not move or close what the user is reading.
