# Log-to-LLM Sentinel — User Documentation

Welcome to the official documentation for **Log-to-LLM Sentinel**, an advanced, self-hosted log monitoring and diagnostic platform powered by local Large Language Models (LLMs) via Ollama. 

Sentinel detects critical log events, automatically collects surrounding application context, consults local AI models for diagnosis, triggers instant notifications, and tracks incident resolution.

---

## Table of Contents

### 🖥️ Page & Interface Guides
* [**Dashboard**](pages/dashboard.md) — Watch post: a verdict, what needs attention, the alert trend, the autopilot, Ollama and the history.
* [**Rules**](pages/rules.md) — Overview of every rule, then one page per rule: incident and statistics, live log, analyses, resolution and settings.
* [**Log Chat**](pages/chat.md) — Ask about your logs: the AI reads the sources and alerts by itself, and changes nothing without your approval.
* [**Meta-Analysis**](pages/meta-analysis.md) — A long-term dashboard across several rules, with periodic AI reports.
* [**Global Configuration**](pages/configuration.md) — Ollama model, notifications, syslog sources, diagnostics, data retention and backups.

### ⚙️ Deep-Dive Features
* [**Autopilot**](features/autopilot.md) — Give only the source: the local AI sets up the rule (name, context, keywords, rhythm) from the log history, or after watching it for 24 h.
* [**Keyword tuning**](features/keyword-learning.md) — How the local AI keeps keywords and exclusions up to date (blind spots, noise, history analysis), with checks and undo.
* [**Notes for the AI**](features/notes.md) — Tell the AI what the logs cannot, for the next alerts on that event or source.
* [**Recommended Models**](features/recommended-models.md) — Which model to run, measured on real alerts, and the settings that follow.
* [**Log Line Safety**](features/log-safety.md) — Why a log line cannot talk the AI out of an alert, and what a *Suspicious log line* means.
* [**Pausing Monitoring**](features/monitoring-pause.md) — The header switch: what stops, what happens on resume.
* [**Inactivity Monitoring**](features/inactivity-monitoring.md) — Tracking log ingestion gaps to alert on dead processes.
* [**Incident Resolution**](features/resolution-tracking.md) — Adaptive calm, health probes, AI validation modes and suggested patterns.
* [**Infrastructure Diagnostics**](features/diagnostics.md) — Let the chat AI check containers and the server itself (read-only Docker proxy, restricted SSH key), with step-by-step setup for Linux and Unraid.

### 🛠️ Setup & Ingestion Wizards
* **[Docker Configurator](../../docker-setup.html)** — Interactive wizard to generate custom `docker-compose.yml` configurations with GPU acceleration and journal bridges.
* **[Webhook Assistant](../../webhook-setup.html)** — Multi-platform script generator (Linux, Unraid, Windows) to stream logs directly to Sentinel webhooks.

---

## Directory Translation Structure
To support future translations, all documentation files are stored under language code subdirectories:
* `docs/en/` — English Documentation (Active)
* `docs/fr/` — French Documentation (Placeholder for future translation)
