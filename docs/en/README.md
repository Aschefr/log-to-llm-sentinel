# Log-to-LLM Sentinel — User Documentation

Welcome to the official documentation for **Log-to-LLM Sentinel**, an advanced, self-hosted log monitoring and diagnostic platform powered by local Large Language Models (LLMs) via Ollama. 

Sentinel detects critical log events, automatically collects surrounding application context, consults local AI models for diagnosis, triggers instant notifications, and tracks incident resolution.

---

## Table of Contents

### 🖥️ Page & Interface Guides
* [**Dashboard**](pages/dashboard.md) — Main system stats, active rule summaries, MTTR logs, and recent analyses.
* [**Rules Management**](pages/rules.md) — Configuring log sources (local files, webhook HTTP, Syslog UDP) and alert parameters.
* [**Monitor**](pages/monitor.md) — Real-time log buffer, search filters, log freeze controls, and manual diagnostics.
* [**AI Analysis Center**](pages/chat.md) — Interactive log chat with dynamic context compression.
* [**Meta-Analysis**](pages/meta-analysis.md) — Scheduling periodic synthese summaries of multiple rules.
* [**Global Configuration**](pages/configuration.md) — SMTP, Apprise, Discord notification settings, Ollama model setups, and backups.

### ⚙️ Deep-Dive Features
* [**Keyword Auto-Learning**](features/keyword-learning.md) — How the AI scans historical logs to learn alert patterns.
* [**Inactivity Monitoring**](features/inactivity-monitoring.md) — Tracking log ingestion gaps to alert on dead processes.
* [**Incident Resolution & AI Verdicts**](features/resolution-tracking.md) — Auto-detecting resolution states, auditing weight relevance, and AI confirmation rules.

### 🛠️ Setup & Ingestion Wizards
* **[Docker Configurator](../../docker-setup.html)** — Interactive wizard to generate custom `docker-compose.yml` configurations with GPU acceleration and journal bridges.
* **[Webhook Assistant](../../webhook-setup.html)** — Multi-platform script generator (Linux, Unraid, Windows) to stream logs directly to Sentinel webhooks.

---

## Directory Translation Structure
To support future translations, all documentation files are stored under language code subdirectories:
* `docs/en/` — English Documentation (Active)
* `docs/fr/` — French Documentation (Placeholder for future translation)
