# Changelog

All notable changes to this project will be documented in this file.

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
