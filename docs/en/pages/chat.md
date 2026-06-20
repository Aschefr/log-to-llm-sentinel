# AI Analysis Center (Log Chat)

The **AI Analysis Center** provides an interactive chat interface to troubleshoot and diagnose complex logs with the local LLM.

---

## 1. Chat Interface
* **Conversations Sidebar**: Lists all current active diagnostics.
  * *New Button (+ New)*: Creates a blank conversation.
  * *Delete Button (🗑️)*: Permanently deletes the selected conversation.
* **Message History**: Displays user queries, Ollama responses, and attached contexts.
* **Query Input Field**: Enter questions, code fragments, or log snippets. Press **Send** to query the model.
  * *Ollama is thinking...*: Visual loading indicator that appears while waiting for model stream responses.

---

## 2. Dynamic Context & Compression Settings
Ollama contexts are limited by token sizes. To prevent crashes or memory loss, Sentinel includes automated saturation management:
* **Settings Gear (⚙️)**: Located in the chat header, this opens the context tuning panel:
  * **System Prompt**: Customize system instructions for the LLM during chat sessions.
  * **Query Language**: Set the default language for responses.
  * **Management Mode**:
    * *Always ask*: Prompts the user to select a compression method once the token limit is reached.
    * *Truncate*: Discards oldest messages to fit the limit instantly.
    * *Compact*: Minimizes space usage (whitespace removal) preserving all data.
    * *AI Summary*: Uses a lightweight LLM task to summarize the history, saving up to 70% of context tokens.
* **Attached Analysis Context**: Clicking this accordion shows the exact log line and previous analysis injected into the chat memory.
