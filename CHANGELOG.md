# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- **New dashboard**: a watch post that opens on a verdict (*All good*, or the list of problems, also shown in the browser tab), then what needs attention (open alerts, silent sources, autopilot failures, Ollama problems, unread analyses), the alerts over 7 days by severity, the autopilot's state and latest decisions, the state of Ollama, and the history. When Sentinel stops responding the page says the state is unknown instead of showing old data as fine.
- **Autopilot** (on by default for a new rule): give only the source — the new rule form then shows nothing but the source; two cards choose between *Autopilot* and *Manual*. Settings of a rule on autopilot also show only the source; *Manual* shows every setting (AI badges on what the autopilot set), and saving in Manual stops it. Sentinel reads the last 7 days of the log; the local AI writes the rule's name and application context (what the log contains, what is normal in it), the keyword arbiter chooses keywords and exclusions, and the grouping window (twice the usual gap between the lines of an event, 30 s to 5 min) and the silence delay (twice the longest usual silence, 1 h to 7 days) come from the real rhythm of the lines. Without enough history (syslog or webhook not sending yet, less than 50 lines, or less than 500 lines over less than 6 hours), the source is watched for 24 hours first; meanwhile the rule alerts on lines with an explicit error level. The log is re-read every week (context rewritten only if its content changed noticeably); a failed setup is retried after 6 hours. The autopilot never asks for approval: the notification threshold follows the real alerts (raised to `warning` when at least 70 % of 10+ alerts over 30 days are `info` or flagged not relevant, never lowered); learned return-to-normal lines are applied right away when they pass checks (specific — no generic or failure word, no variable part such as a timestamp —, absent from every alert line of the last 30 days, present in at most 5 % of the log lines and never within 3 lines of an alert line; learned patterns already active are checked too); a keyword change held back by a check is dismissed and logged instead of waiting (*Apply anyway* in the history; it can come back after 30 days). Fields set by the autopilot carry an **AI** badge in Settings, and a field you change is no longer touched. It can also be switched on for an existing rule (name and a written context are kept). The rule page header shows its state.
- **Autopilot setup journey** on the rule overview: the steps (source connected, history read or observation with its remaining time, the AI describes the log, the AI picks the keywords, rhythm of the source, rule ready) are ticked as the background work progresses, with what was found or set at each step. Shown until the rule is ready, then for 24 hours.
- **Detected syslog sources**: with the Syslog source, the rule form lists the hosts that sent lines in the last 7 days (line count, last message), refreshed every 5 seconds; one click fills the hostname. Hosts already followed by a rule are shown as such.
- **Watchdog**: if the application's main loop stops responding for more than 5 seconds, the logs show where it is stuck (once per stall), to diagnose an unresponsive interface.

### Changed
- *Reset the MTTR* and *Delete all analyses* moved from the dashboard to **Configuration > Maintenance**. The dashboard's MTTR is now the median over 30 days, the same as the Rules page.
- Keywords and ignored lines are no longer edited in the Settings tab of an existing rule: the Keywords tab manages them (saving Settings never overwrites them anymore). They are still typed in the form when creating a manual rule.
- A new rule on an existing log file starts watching at the end of the file: its history is read by the learning, no longer replayed as new lines (which could fire a burst of alerts on old events).

### Security
- **HTML in AI answers and log lines**: an answer quoting a crafted log line could run code in the page, since anyone on the network can send a syslog line. AI answers are now sanitized by DOMPurify, served by the application (without it they are shown as plain text), and may no longer load images, which could send data out just by opening the page. Error messages are escaped too.
- **E-mail notifications** now escape the log line, the AI's answer, keywords and the rule's name.

### Fixed
- **Deleting a rule** now also deletes its analyses, resolution history and learning sessions. Its number can be reused: a rule created afterwards could inherit them, and a pending autopilot session prevented its setup, then applied the deleted rule's source to it.
- **Changing a rule's source**: a learning session still running on the old source is stopped, and a rule on autopilot starts learning again from the new source. A rule on autopilot whose setup could not start now retries by itself.
- **Autopilot**: an event repeating all day (e.g. Docker Desktop's `event streamer: error: EOF`, every 10 seconds) no longer sends an analysis to the AI every 30 seconds nor keeps the rule in alert: once its last 3 analyses are rated `info`, it is only analyzed again every 6 hours.
- **Dashboard**: the automatic refresh no longer closes the analysis being read, nor reloads the list from the start. The list is usable on a phone and with the keyboard (an analysis card opens with a real button, on every page).
- Markdown rendering no longer depends on a CDN: without internet access the analyses list stopped displaying altogether. The library is now served by the application, with its version pinned.
- A new rule that has not received any line yet is no longer shown as a *silent source* right away; and while the autopilot sets up a rule, the silence delay is not known yet, so no *silent source* warning is shown or sent.
- **Log history**: the learning and the autopilot read every rotated file of the period, not only `.1` (e.g. Docker Desktop's `com.docker.backend.exe.log.20261102-170332.916`; compressed archives are skipped).
- **Syslog**: UniFi messages (`Nov 02 17:48:08 2026-11-02T17:48:08.617Z Dream Machine Special Edition CEF:0|…`) were read with their second timestamp as the hostname, so each message went to a file of its own and no rule could follow the device. The hostname of a CEF message is now everything before `CEF:` (spaces allowed), a second ISO timestamp after the header is skipped, and RFC 5424 headers are read too.
- **Chat**: a reply could stay stuck on "thinking" until the page was reloaded when the model took long to start answering (busy with an analysis, loading…): an idle connection was closed by a proxy or the browser and the page did not reconnect. The stream now sends a keep-alive every 14 s, disables proxy buffering, reconnects when the connection closes before the end, and reloads the conversation if the generation is no longer known by the server.
- **Rule page**: opening unread alerts now updates the unread count of the Analyses tab and of the *Unread* filter right away (only *Mark all as read* did).

## [1.6.0] - 2026-11-02

### Added
- **Self-tuning keywords**: The local AI acts as an arbiter that keeps each rule's keywords and exclusions up to date. Sentinel counts, per day, the lines matched by every keyword and exclusion, and groups the error lines that no keyword catches ("blind spots") into templates. Every hour the arbiter decides (structured output) to add a keyword for recurring blind spots, and to keep, exclude or remove noisy keywords (mostly `info` alerts, or alerts flagged *Not relevant*). Each decision is checked against the data before being applied: the keyword or exclusion must literally appear in the real lines, must not be generic or contain a variable part, an exclusion must not hide a `warning`/`critical` alert of the last 30 days nor neutralize a keyword, the last keyword is never removed. Checked decisions are applied (mode *Automatic*, default); the others are offered for review. Rejected or undone changes are never proposed again, and manual edits win. One notification sums up each review.
- **Keywords tab** on the rule page: tuning mode (*Automatic*, *Suggestions*, *Off*), *Review now*, history analysis and observation, changes to review with their reason and the lines involved, keywords with alerts over 30 days (severities), lines per day, last alert and origin, exclusions with lines ignored per day, a warning when an exclusion neutralizes a keyword, blind spots being watched, change history with undo.
- **Not relevant** button on alert cards: the arbiter reviews the rule right away and targets those lines with a precise exclusion.
- A new rule analyzes the last 7 days of its log on creation (option in the form), so its keywords can be left empty.
- Keyword changes made from the chat (with confirmation) are logged in the rule's history and can be undone there.

### Changed
- **Keyword learning rewritten**: instead of sending the first characters of each time packet to the AI, a session reads the whole period in a single pass (current and rotated `.1` file), groups every line into templates and gives the arbiter the blind spots and, per keyword, the templates it catches. Lines without a timestamp (stack traces) belong to the previous line; time zones are honored. Sessions are *Analyze history* (last 1-30 days) or *Observe* (next 1-24 hours) and their changes can be undone at once. The former wizard (period, granularity, packets, YES/NO cross-validation, up to ten notifications per session) is removed.
- **Keyword matching**: a keyword or exclusion that is a number or 3 characters or fewer must now match a whole word (`502` no longer matches a port `:50231` or an interface `vethb68c502`, `oom` no longer matches `zoom`). Longer keywords still match as substrings.
- *Ignore these lines…* (recurring events) now opens the Keywords tab with the exclusion pre-filled.

### Fixed
- Keyword learning no longer replaces the whole keyword and exclusion lists (manual entries were lost), restores the exclusions on revert, saves candidate exclusions during the scan, and no longer copies the examples of its own prompt as keywords or exclusions.
- The old per-session debug logs (`data/learning_logs/`), which contained raw log lines, are deleted after 30 days.

## [1.5.1] - 2026-11-02

### Changed
- **Diagnostics setup**: A single **Test and save** button per card tests the values typed in the form and saves them when the test succeeds (**Save anyway** is offered when it fails; an empty field disables). The proxy address and SSH user are now real pre-filled values matching the selected platform (docker compose / Unraid, Linux / Unraid) instead of placeholders, and the server address is pre-filled with the server IP from the general settings. The SSH test no longer requires saving first.
- **Server diagnostics install block easier to review**: A header sums up what the block does, the few lines that change the server come first under numbered steps, and the read-only checks script comes last with a comment above each check (Docker noise filters are now named). The checks themselves are unchanged, so an already installed script does not need to be pasted again.

### Added
- **Remove server access**: Once the key exists, **Configuration › Diagnostics › Server › Remove access** shows the block that undoes the installation (Linux: key, script, `sentinel-diag` user; Unraid: key line on the flash drive and in the session, script), plus a **Forget the server in Sentinel** button.

### Fixed
- **Configuration page auto-save and diagnostics**: Typing in the diagnostics fields no longer shows "Auto-saved" (those fields were never part of the general auto-save and were not saved).
- The server diagnostics script no longer fails when run by hand outside an SSH session.

## [1.5.0] - 2026-11-02

### Added
- **Docker diagnostics (read-only)**: Through a read-only Docker socket proxy (`tecnativa/docker-socket-proxy`, `CONTAINERS=1`, `INFO=1`, `POST=0`), the chat and investigation assistant can list containers and check their state, health and last healthchecks, restart count, exit code, OOM, logs and CPU/memory usage itself, instead of asking the user to run `docker ps` / `docker logs`. Environment variables and labels are never passed to the AI. Sentinel never mounts the Docker socket.
- **Server diagnostics (restricted SSH)**: Sentinel generates an SSH key and the block to paste on the server (Linux or Unraid variant). On the server the key is restricted (`restrict,command=`) to a script that only runs a fixed list of read-only checks: overview, disk, memory, load, processes, network shares, network, kernel messages, system log, failed services, service status and journal. Service names are validated on both sides; the server's key fingerprint is pinned on first connection.
- **Configuration › Diagnostics**: Proxy address with a connection test, key generation, copy-ready install blocks for Linux and Unraid, connection test showing the server overview. Diagnostic tools are only offered to the AI once configured, and only in the chat and investigations (never in the automatic alert analysis).
- **Docker configurator**: Platform choice (docker compose or Unraid step-by-step), optional Docker diagnostics service, syslog port mapping. The repository `docker-compose.yml` includes the optional proxy.
- **Unraid-aware checks** (validated on Unraid 7.3): `array` reports the array state and each disk's status, errors and temperature (software RAID / ZFS elsewhere); systemd checks point to `syslog`, `array` and `processes` on Unraid; Docker network noise and Sentinel's own SSH sessions are filtered out of `kernel`, `network` and `syslog`; "containers with problems" ignores containers stopped on purpose (never started, exit code 0 or 143).
- **Script versioning**: the connection test warns when the script installed on the server is older than the one Sentinel ships, so the block can be pasted again.
- **Diagnostics guide**: `docs/en/features/diagnostics.md` (what to type on the server, what to fill in Sentinel, how to revoke, how to update the script).

### Changed
- The Docker image now includes `openssh-client` (used only by server diagnostics).

### Fixed
- **Learned patterns describing a failure**: Phrases such as "stop_after time has been exceeded", "container exited", "unhealthy" or "No such container" are no longer suggested as return-to-normal patterns.

## [1.4.1] - 2026-11-02

### Fixed
- **Learned patterns describing a failure**: Phrases such as "login failed" or "connection timed out" are no longer suggested as return-to-normal patterns (failure words are filtered out, in English and French).
- **Empty summary on some legacy analyses**: When the "Summary:" label stands alone in its paragraph, the summary is now taken from the next paragraph.
- **"resolved in —"**: Analyses whose resolution time is unknown or excluded from the MTTR now simply show "resolved".
- **Incident timeline order**: "Incident opened" now comes before the first analysis logged at the same time.

## [1.4.0] - 2026-11-02

### Added
- **Rules overview**: The Rules page now lists every rule — disabled ones included, with a toggle to re-enable them — with its state, alerts over 7 days, last incident summary, median MTTR and source liveness, plus global indicators (rules in alert, alerts over 7 days, median MTTR, silent sources), state filters and a search field.
- **Rule page with tabs** (`/rules/{id}`): *Overview* (ongoing incident with next check, required calm, log liveness and probe result; activity; 30-day alert chart; MTTR, auto-resolution rate, notifications and AI calls; recurring events over 7 days with a shortcut to ignore them; latest analyses), *Live log* (the former Monitor viewer), *Analyses*, *Resolution* and *Settings*.
- **Adaptive calm**: An incident now closes after three times the median interval between its errors (never below the rule's minimum, at most 24 h), so recurring errors no longer flap open/closed. A fixed duration remains available.
- **Health probe**: Optional per-rule HTTP(S) or TCP probe, defined by the user and tested from the settings. During an incident it runs every 60 s; while it fails, the incident stays open even if the log is silent.
- **AI validation modes**: *If ambiguous* (default — the AI only checks success lines, a full calm closes without an LLM call), *Always* or *Never*.
- **Suggested resolution patterns**: Patterns learned by the AI are now suggestions to accept or reject in the Resolution tab; only accepted patterns are used, rejected ones are never suggested again and unreviewed ones expire after 14 days. Patterns show their origin and accepted/rejected trigger counts, and can be added manually.
- **Analyses list**: Collapsed cards show the one-sentence summary, a 7-day recurrence badge and the resolution time; filters (unread, severity, period, open incidents only) show real counts.

### Changed
- **Monitor merged into the rule pages**: The Monitor menu entry is gone; old `/monitor` links (notifications, chat, meta-analysis) redirect to the matching rule page, analysis or log line.
- **Rule settings** are a page with five sections (Source, Detection, AI analysis, Notifications, Return to normal) instead of a long modal. Renamed: *Grouping window* (anti-spam delay) and *Context lines around the event* (lines sent to Ollama). The two inactivity switches are merged into *Alert when the source goes silent*. Existing *success keyword only* rules now also close after the calm period.
- **Resolution history** is shown 10 entries at a time, with readable triggers and verdicts.

### Removed
- The *Notify when the AI searches for a resolution* option and its "searching" / "denied" notifications.

### Fixed
- Legacy analyses without a stored summary now show their "Summary:" paragraph (markdown stripped) instead of their last paragraph.

## [1.3.1] - 2026-11-02

### Fixed
- **Resolution Check Loop**: When the AI rejected an inactivity-timeout resolution, the check ran again every minute (one full LLM call each time — tens of thousands of calls on some instances). After a rejection the next timeout attempt now waits a full timeout period, and a pattern-triggered attempt waits 5 minutes.
- **Concurrent Resolution Attempts**: New log batches could start another resolution attempt while one was still being validated, piling up LLM calls behind the alert analyses. Only one attempt per rule runs at a time.
- **Resolution Despite a New Error**: If an error arrived while the AI was validating, a positive verdict still resolved the incident. The attempt is now abandoned.
- **Resolution Patterns Matched on Error Lines**: Lines that trigger the rule (keyword match, not excluded) can no longer satisfy a resolution pattern.
- **Self-Learning Polluted the Patterns**: Learned resolution patterns are now rejected when they are generic (`exiting`, `Starting`, `message repeated`…), contain or are contained in an error keyword, appear in the incident's error lines, or do not occur in the logs. Only normal lines are sent to the LLM, existing patterns are no longer re-weighted each time the LLM proposes them (a pattern's weight now only grows when it triggers an AI-validated resolution), and timeout resolutions no longer feed the learning.
- **"False Positive" Did Not Reopen the Incident**: The verdict outcome was overwritten before being tested, so the rule never went back to alert. The rule now returns to alert with the analyses that resolution had closed.
- **Restart Recovery**: A rule caught in `resolving` during a restart was never checked again by timeout; it is restored as `alert`, and the last error time is taken from the latest unresolved analysis instead of the incident start.

### Changed
- **AI Resolution Check**: Uses structured output and the configured context size (the fixed 4096 caused a model reload between calls). The prompt now contains the incident (start, duration, time since the last error, summaries of its unresolved analyses), the trigger, the number of normal lines received since the last error, and the last lines of the source — including for timeout checks, which previously sent no logs at all and were almost always rejected. The global system prompt applies.
- **Resolution Verdict Retention**: Verdicts older than 30 days are purged (the 20 most recent per rule are always kept).

## [1.3.0] - 2026-11-01

### Added
- **Aggregated Meta-Analysis**: The meta-analysis no longer pastes each analysis one by one. Identical events are grouped (count, first/last occurrence, hours, severity, unresolved count, sample line, latest summary), each rule gets its totals, resolution rate, MTTR and current state, rules without alerts are listed, and the total number of alerts over the whole period is always reported (the "Max Included Analyses" limit still caps the detailed ones). Groups are filled by severity within the configured context size.
- **Trend Against the Previous Synthesis**: The previous run's TL;DR is included so the synthesis can flag what is new, persistent or gone.
- **Structured Meta-Analysis Result**: Overall status (🟢 OK / 🟠 Degraded / 🔴 Critical), TL;DR, key points, detailed synthesis and recommendations, stored with each result (`tldr`, `status`, `result_json`).
- **Prompt Visibility**: New "📜 Prompt" button on analyses (Dashboard, Monitor) showing the exact messages sent to the LLM.
- **Prompt Regression Set**: `scripts/llm_regression.py` replays log cases against the configured model and checks the resulting severity; an anonymized example set is provided (`scripts/llm_regression.example.json`). See BUILD_GUIDE §4.

### Changed
- **Semantic Versioning**: Versions now follow `MAJOR.MINOR.PATCH` (starting with 1.3.0) instead of being derived from the commit count. `version.txt` is tracked and bumped by `scripts/publish_release.ps1 -Bump patch|minor|major`, which also dates the `[Unreleased]` CHANGELOG section and uses it as the GitHub release notes. The in-app update indicator now compares the running version with the latest GitHub release.
- **Meta-Analysis Notifications**: When the synthesis is too long for Discord/Apprise, the notification uses the stored status, TL;DR and key points directly instead of a second LLM call.
- **Preview Exclusions & Annotations**: Manual meta-analysis runs now send the excluded analysis IDs and annotations; the aggregation is done server-side (the previous free-text context is still accepted).

### Fixed
- **Unclosed Ollama Streams**: Structured LLM calls and the chat tool loop now close the HTTP stream as soon as the answer is complete instead of leaving it to the garbage collector.
- **Chat Action Cards Clicked Mid-Answer**: An action proposed by the AI appeared (with active buttons) while the model was still writing; after the click the answer kept growing, which looked like the conversation changing by itself. Cards received during generation are now locked ("The AI is finishing its answer…") until the turn ends, and the model is instructed to propose actions *before* writing its final answer (chat and investigation prompts).
- **Unclear Effect of Applied Actions**: Applied actions now state their concrete effect and where to see it (e.g. "Analysis #947 is no longer counted in the dashboard MTTR. Rules and future alerts are unchanged."). The tool description also clarifies that `exclude_from_mttr` targets a single analysis and that recurring noise calls for an exclusion pattern or keyword removal instead.

## [1.2.303] - 2026-11-01

### Added
- **AI Chat Tools**: When the Ollama model supports tool calling (detected automatically), the chat assistant can look things up instead of guessing: list rules and their state, read the latest lines of a source, get the lines around an analysis or timestamp, search a source, list/open analyses, rule statistics (alerts per day, MTTR, most frequent events), resolution history, and events correlated across all rules around a given time. Each answer shows a live, collapsible "Tools used" list.
- **Runaway Protection**: At most 4 tool rounds and 8 calls per answer (6/12 for investigations), a final answer round without tools, bounded tool output (100 lines / 4,000 characters), tool results capped at 40% of the context, duplicate calls skipped, per-turn timeout. Tools accept rule/analysis IDs only, never file paths, and search is substring-based (no model-supplied regex).
- **Confirmed Actions**: The assistant can only *propose* changes (exclusion pattern, add/remove keyword, mark resolved, exclude from MTTR, resend notification, run a meta-analysis). Proposals appear as cards with Apply / Decline buttons and are executed only after explicit user confirmation.
- **Guided Investigation (🔎)**: New "Investigate" button on analysis cards and in the chat's attached analysis. It opens a conversation in which the assistant follows an investigation plan (wider context, recurrence, correlated events, resolution history) and writes a structured report.
- **Richer Chat Context**: Conversations attached to an analysis now include the rule, the triggering events, the surrounding source lines and the initial analysis; the rule list shows IDs and alert state. Tool results from the last two tool-using turns are replayed so follow-up questions keep that knowledge.
- **Context-Rich Alert Analysis**: Every alert analysis (automatic detections, retry, manual line analysis and rule test now share one pipeline) sends the LLM a structured prompt with real `system`/`user` roles containing: the rule and its source type, the current date, the triggering events, the **surrounding lines from the log source** (using the rule's existing "context lines" setting, which was previously ignored), and the **7-day history of similar alerts** for the rule (count, hours of occurrence, previous severity, resolution time and summary). Recurring, self-resolving events can now be recognised as such.
- **Duplicate Line Grouping**: Grouped detections collapse lines that only differ by timestamps, IPs, ports, PIDs or IDs into a single `[×N]` entry, so the LLM sees distinct events instead of up to 30 near-identical lines. Short numbers (error and HTTP codes) are preserved.
- **Structured LLM Output**: Alert analyses use Ollama structured outputs (JSON schema): severity, confidence, short summary, probable cause, explanation and actions. The UI still shows a Markdown rendering. Severity no longer relies on keyword guessing, and responses that ignore the schema fall back to the previous text parsing.
- **Stored Summaries & Prompts**: Each analysis now stores its short summary, raw structured response and the exact messages sent to the LLM (`summary`, `analysis_json`, `prompt_json`). Discord/Apprise notifications use the stored summary (plus actions when they fit) instead of a second LLM call, and meta-analyses use it as the per-event digest.
- **Token Budget**: Prompt sections are filled by priority (events, history, then context) within the configured context size, keeping room for the answer.

### Changed
- **Chat Roles**: The chat now sends a real `system` message followed by `user`/`assistant` messages instead of one flattened text prompt.
- **Single Configuration Source**: The duplicated configuration dictionaries were replaced by one shared helper.
- **Monitor Follow-up Questions**: The inline question box on analyses now uses the same context as the chat (system message with events, source context and initial analysis) instead of re-sending the old analysis prompt.

### Fixed
- **Chat Prompt Formatting**: The chat context sent to the LLM contained literal `\n` sequences instead of real line breaks (system block, rules list and conversation history), so the model received the whole context as a single garbled line. Line breaks are now real, and the context usage estimate in the chat UI matches.
- **Analysis Language Ignored**: The orchestrator's config never included `ollama_prompt_lang` / `site_lang`, so automatic analyses and their notifications always used French prompts regardless of settings. Manual line analysis and retry now also honour the prompt language.
- **Grouped Lines Order**: When several matching lines were bundled in one analysis, they were sent to the LLM newest-first, breaking cause-and-effect reasoning. Lines are now sent in chronological order (the most recent lines are still kept first when the character limit is reached).
- **Retry of Grouped Analyses**: The raw matching lines are now stored with each analysis (`triggered_lines_json`), so retrying a grouped analysis rebuilds the original prompt instead of wrapping the group header inside a new "Triggering line".
- **Retry Ignored Model Settings**: `config.py` defined `_get_config_dict` twice and the effective one lacked `ollama_ctx`, `ollama_temp` and `ollama_think`, so retried analyses always ran with a 4096-token context and default settings.
- **Rule Test Crash**: The rule test endpoint used `asyncio` without importing it.
- **Notification Summaries**: The AI summary used for Discord/Apprise notifications (alerts and meta-analyses) was requested with a 2048-token context, which truncated long analyses and forced Ollama to reload the model, often ending in a timeout and a raw `[TRUNCATED]` message. Summaries now reuse the analysis context size, and the meta-analysis summary now waits for the shared Ollama lock instead of running concurrently with other analyses.

## [1.2.297] - 2026-06-30

### Fixed
- **Syslog Keyword Match & Detached Instance**: Fixed an issue where keyword detection would fail for syslog logs because only the message body was analyzed instead of the complete log line with the timestamp and hostname. Resolved a `DetachedInstanceError` caused by using an expired session object within an asynchronous orchestrator task.

## [1.2.296] - 2026-06-27

### Added
- **Complete Internationalization (i18n) & English Fallbacks**: Conducted an exhaustive scan of the codebase to translate all remaining user-facing French text hidden in templates, modales, tooltips, and default prompts. Added 15 new translation keys to `fr.json` and `en.json`. Overhauled fallback values in JavaScript (`meta.js`, `keyword_wizard.js`, `config.js`) to display English universally if the selected language pack fails to load, securing global application accessibility.

### Fixed
- **Light Theme Readability in Meta-Analysis Results**: Fixed a styling bug where inline `<code>`, `<pre>` code blocks, and `<blockquote>` quotes rendered with a hardcoded dark background (`#2d2d2d`) in light mode, making text completely unreadable. Swapped hardcoded background colors for theme-aware CSS variables and added explicit light theme overrides (`#f1f5f9` backgrounds with `#1e293b` text).

## [1.2.294] - 2026-06-27

### Added
- **Resolution Monitoring Redesign**: Completely overhauled the resolution monitoring section in the rule configuration modal. The old dropdown + disconnected toggle switches have been replaced by three explicit clickable mode cards (⏱ Inactivity Timeout, 🔍 Keyword in Logs, ⚡ Both), each with a title, a short description of the ideal use case, and inline configuration where relevant. The AI Validation and notification options are now grouped in a unified card list with icon, title, and description per row, making parent/child relationships (AI search notification indented under AI Validation) immediately clear. Fully responsive, keyboard-navigable, and i18n-compliant (fr + en).

### Fixed
- **Syslog/Webhook Pipeline — Missing AI Analysis**: Fixed a critical bug where log lines matched via syslog triggered the yellow highlight in the real-time monitor but never proceeded to AI analysis. Root cause: `asyncio.run_coroutine_threadsafe` was called from within the asyncio loop, causing a deadlock. Replaced with `asyncio.ensure_future` for correct coroutine scheduling from a synchronous threaded context.
- **Resolution Service — Lock Held During AI Call (BUG-RES-01)**: Refactored `_try_resolve` in `resolution_service.py` into three distinct phases. The `_state_lock` is now only held during short state mutation phases (transition to `resolving`, then final result application), never during the LLM call (which can take up to 180s). This eliminates a deadlock that froze all concurrent match processing during AI resolution validation.
- **Resolution Service — Stuck 'resolving' State (BUG-RES-02)**: Each phase of `_try_resolve` now has its own `except` block that resets both the in-memory state and the database `alert_status` back to `alert` on failure, preventing rules from being permanently stuck in `resolving`.
- **Resolution Service — Double Resolution Trigger (BUG-RES-EXTRA)**: `check_timeout_resolutions` was incorrectly re-triggering resolution for rules already in `resolving` state. Fixed by changing the status filter from `not in ("alert", "resolving")` to `!= "alert"`.
- **Resolution Service — Syslog/Webhook Paths in AI Audit (BUG-RES-04)**: `audit_patterns_with_ai` was attempting to open `[SYSLOG]:hostname` as a literal file path. Virtual paths are now resolved to their actual disk file (`/app/data/syslog/<hostname>.log` or `/app/data/webhooks/<token>.log`) before reading log context.
- **Orchestrator — Double SQLAlchemy Session Close (BUG-ORCH-01)**: Restructured `_flush_buffer` into two clean, independent `try/finally` blocks. The first opens a short-lived session to read `anti_spam_delay` and closes it before `await asyncio.sleep(...)`. The second opens a fresh session for actual processing. Eliminates the manual `db.close()` mid-try that caused a double-close on the `finally` block.
- **Syslog/Webhook Buffer Limit (BUG-SYS-01)**: Raised `_BUFFER_MAX` from 500 to 2000 lines in both `syslog_receiver.py` and `webhook.py`, matching the actual needs of production log volumes and eliminating the premature tail truncation reported by users.

## [1.2.285] - 2026-06-14

### Added
- **Configuration Export/Import**: Implemented a compressed ZIP-based configuration backup and restore system. Users can export their entire global settings and alert rules, with or without complete operational history (analyses, chat sessions, meta-analyses, and auto-learning history) and restore it seamlessly on another server instance.

## [1.2.284] - 2026-06-14

### Fixed
- **Meta-Analysis Variable Initialization**: Resolved a `NameError` crash (`lang` referenced before assignment) by initializing configuration variables (`lang`, `ollama`, `ollama_url`, `ollama_model`) early in the execution sequence.
- **Deepen with AI Button**: Fixed the "Approfondir avec l'IA" button on the meta-analysis result interface by directing requests to the correct router prefix (`/chat/api/create`), formatting the context fields as required by the backend schema, and pointing the redirect parameter to `id` instead of `conv`.

## [1.2.283] - 2026-06-13

### Fixed
- **Inactivity Notification Formatting**: Cleaned up HTML tags and formatted the inactivity warnings using Markdown for Discord and Apprise notification endpoints, preventing raw HTML code from appearing in alerts.
- **Syslog Path Resolution in Services**: Resolved `[SYSLOG]:hostname` formatted rule paths to actual server log files (`/app/data/syslog/<hostname>.log`) in both the keyword auto-learning service and the manual resolution service, preventing "File not found" errors when processing syslog sources.
- **Keyword Auto-Learning Syslog Source**: Fixed a bug where the auto-learning wizard failed to activate or launch when the Syslog source card was selected.
- **Config Update Endpoint**: Resolved config update endpoint failure causing UI "unknown error" warnings on settings page.
- **Browser Cache Busting**: Appended static query parameters in script references to ensure latest Javascript fixes load immediately without requiring manual cache refresh (CTRL+F5).

## [1.1.0] - 2026-06-11

### Added
- **Syslog Ingestion**: Turn Sentinel into a syslog UDP receiver (listening on port 10514 UDP on host / 514 UDP in container).
- **Syslog Relay**: Forward raw incoming syslog packets to an optional secondary backup server (e.g., secondary Unraid server).
- **Multi-Source Rule Routing**: Stream logs based on hostname prefix (`[SYSLOG]:hostname` or catch-all `[SYSLOG]:*`).
- **Interactive Configuration Helpers**: Settings page and Rule modal now display the host's actual LAN IP address (e.g., `192.168.22.167`) and port (`10514`) for easy copy-pasting into remote syslog configurations.
- **Global Syslog Buffer**: Added support for global catch-all buffer (`*`) tracking all syslog events simultaneously.

### Fixed
- **FastAPI Event Loop Sync-Reload Issue**: Fixed a bug where saving settings triggered a `no running event loop` error when reloading the UDP listener protocol.
- **I18n Accented Character Corruptions**: Protected JSON files using proper `utf-8-sig` encoding throughout updates.
