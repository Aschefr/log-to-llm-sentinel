# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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
