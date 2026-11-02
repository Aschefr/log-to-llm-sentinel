# Log-to-LLM Sentinel — User Documentation

Welcome to the official documentation for **Log-to-LLM Sentinel**, an advanced, self-hosted log monitoring and diagnostic platform powered by local Large Language Models (LLMs) via Ollama. 

Sentinel detects critical log events, automatically collects surrounding application context, consults local AI models for diagnosis, triggers instant notifications, and tracks incident resolution.

---

## Table of Contents

### 🖥️ Page & Interface Guides
* [**Dashboard**](pages/dashboard.md) — Main system stats, active rule summaries, MTTR logs, and recent analyses.
* [**Rules**](pages/rules.md) — Overview of every rule, then one page per rule: incident and statistics, live log, analyses, resolution and settings.
* [**AI Analysis Center**](pages/chat.md) — Interactive log chat with dynamic context compression.
* [**Meta-Analysis**](pages/meta-analysis.md) — Scheduling periodic synthese summaries of multiple rules.
* [**Global Configuration**](pages/configuration.md) — SMTP, Apprise, Discord notification settings, Ollama model setups, and backups.

### ⚙️ Deep-Dive Features
* [**Keyword Auto-Learning**](features/keyword-learning.md) — How the AI scans historical logs to learn alert patterns.
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
