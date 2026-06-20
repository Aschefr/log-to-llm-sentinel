# Keyword Auto-Learning

The **Keyword Auto-Learning** feature allows Log-to-LLM Sentinel to scan past logs and build optimal alert and exclusion keyword lists automatically using local AI.

---

## 1. Setup Wizard Flow
From the rule modal under the **Rules** tab, click **Auto-apprendre (🤖)** to open the wizard panel. The configuration process involves:
* **Time Range**: Select the Start and End datetimes representing the historical log segment you want the AI to analyze.
* **Granularity Slider**: Choose the packet size (e.g., `10 minutes`, `1 hour`, `1 day`). Logs are split into chronological packets of this size to fit the LLM context window.
* **Auto-Learning Mode**:
  * *Live Capture (Future)*: Captures incoming logs.
  * *Historical Analysis (Past)*: Scans existing files.
* **Quick Profiles**: Pre-set time ranges (`Quick (2 min)`, `Normal (10 min)`, `Extended (1h)`, `Complete (1d)`).

---

## 2. In-Depth Operational Phases
Once you click **Lancer l'analyse**, a background task is triggered. It operates in four distinct steps shown in the wizard's header progress bar:

### Step 1: Configuration (Config)
The initial setup state. The launch button checks if a name and log file path are provided before starting the session.

### Step 2: Scanning (Scan)
Sentinel reads the log file incrementally, packing lines according to your chosen granularity.
* For each packet, it runs a prompt asking the LLM to identify potential trigger keywords and noise exclusion terms.
* The raw keywords are accumulated inside the database in real-time.
* A live progress bar and keyword cloud are updated dynamically.

### Step 3: Refinement (Refine)
After scanning all packets, the AI consolidates the accumulated raw keyword list (capping it at 40 elements) into a refined list of up to 15 key terms and 2 exclusion patterns. It drafts a diagnostic explanation detailing why these patterns were selected.

### Step 4: Validation (Validated)
Ollama validates the refined list against the raw candidates using a cross-validation loop:
* **YES/NO Validation**: The model verifies if the final list is representative of the raw inputs.
* **Shuffle & Retry**: If validation fails, the prompt format is shuffled and retried up to 3 times.
* **Phrase Extraction**: If a selected keyword is a phrase (> 3 words), the AI extracts shorter terms to use as rules keywords.
* **Success/Error States**: The session transitions to `validated` (applying keywords to the rule), `reverted` (restoring original keywords), or `error` (logging diagnostic details).
