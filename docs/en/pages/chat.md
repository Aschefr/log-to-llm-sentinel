# Log Chat

![Log Chat](../../../screenshots/log-to-llm-sentinel_chat.png)

Ask a question about your logs. The AI reads the sources, alerts and history by itself, then answers. It changes nothing without your approval.

---

## 1. State strip
Four joined buttons at the top of the page say what the AI can do right now:
* **Model**: the model in use and whether it is ready, busy (with the number of calls waiting) or out of reach. Opens *Configuration > Ollama*.
* **What it can read**: logs and alerts, plus Docker and the host when [diagnostics](../features/diagnostics.md) are set up. A model without tool support only reads the conversation, and the strip says so. Opens *Configuration > Diagnostics*.
* **To confirm**: the proposed actions waiting for your decision, across all conversations. Opens the first one.
* **Context**: an estimate of how much of the model's context the conversation uses. Opens the context panel.

An unknown state is shown as unknown, for instance when Ollama does not answer.

---

## 2. Conversations
* The list is sorted by latest activity. A conversation shows its source rule and the severity of its alert when it started from one, whether the AI is answering, and how many actions wait for a decision.
* **New** opens an empty page with a few suggested questions. Nothing is created until you send the first message.
* An answer keeps generating if you leave the page, and the page picks it up when you come back.
* While an answer is being written, the page follows it only if you are at the bottom: scroll up to read and nothing moves. *Jump to the end* brings you back.
* **Stop** really stops the model, which frees it for other services. What was already written, looked up or proposed stays in the conversation.

On a phone, the list and the conversation are two separate views.

---

## 3. A conversation about an alert
*Open in chat* and *Investigate* on an alert attach its analysis to the conversation. The **original alert** stays folded at the top, with its log line, its analysis and a link to the rule.

---

## 4. AI tools and investigation
When the selected Ollama model supports tool calling (Sentinel checks the model's capabilities automatically), the assistant can look up information by itself instead of guessing:
* **Read-only tools**: list rules and their alert state, read the latest lines of a rule's source, get the lines around an analysis or a timestamp, search a source for a text, list or open past analyses, get rule statistics (alerts per day, MTTR, most frequent events), read the resolution history, and find **correlated events across all rules** around a given time.
* **What the AI looked at**: each answer shows a folded list with one chip per call (e.g. *Search "NFS" #3*), updated live while the model works. Open a chip to read exactly what the tool returned, so you can check the answer against it.
* **External sources**: when set up, the AI can read the official sheet of a CVE and search the web through your SearXNG. See [Web Search and CVE Sheets](../features/web-search.md).
* **Safety limits**: at most 4 tool rounds and 8 calls per answer (6 and 12 for an investigation), bounded output per tool (100 lines / 4,000 characters), tool results capped at 40% of the context size, duplicate calls skipped. Tools only accept rule or analysis IDs, never file paths.
* **Proposed actions**: the assistant cannot change anything by itself. It can propose an action (add an exclusion pattern, add or remove a keyword, mark a rule as resolved, exclude an analysis from MTTR, resend a notification, run a meta-analysis), shown as a card with **Apply** and **Decline** buttons. Nothing happens until you click **Apply**.
* **Investigate**: available on analysis cards (Dashboard, rule page) and in the original alert. It opens a new conversation where the assistant follows an investigation plan (wider context, recurrence, correlated events, resolution history) and writes a structured report: findings, ranked hypotheses, checks to run and a recommendation. The plan given to the AI stays folded.

> Tool rounds make answers slower: each round is a new generation. With a large local model, an investigation can take several minutes.

---

## 5. Context
A model reads a limited amount of text at once. Beyond it, it forgets the beginning of the conversation. The context panel shows an estimate of the space used and what the AI really receives, and offers three ways to free space:
* **Summarize with the AI**: the AI merges the older messages into one summary, in fixed sections (facts, decisions and why, your preferences, open questions), keeping exact values such as paths, IP addresses and error messages, and noting what you corrected. The last two exchanges stay word for word. Each new summary merges the previous one, so the start of the conversation is never cut. If the model returns a summary that is cut short or loops, it is not kept: the conversation stays as it was and you can try again or forget the oldest messages. Takes a moment.
* **Forget the oldest**: immediate. The first messages stay visible, dimmed, but the AI no longer reads them.

A summary stays in the conversation, where you can correct it or restore the full history.

When a conversation is full, the panel opens and your message goes out once space is freed, unless an automatic choice is set in Configuration.

---

## 6. Settings
The chat has no settings of its own. In [Configuration > Ollama](configuration.md):
* **Chat instructions**: optional text added to every conversation, such as your setup or the tone you want.
* **When the context is full**: ask, summarize or forget the oldest.

The chat answers in the site's language, and uses the same model and context size as the analyses.
