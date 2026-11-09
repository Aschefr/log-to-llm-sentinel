# Recommended Models

![Choosing a model in Configuration](../../../screenshots/log-to-llm-sentinel_config.png)

Sentinel suggests three models so a new user has nothing to guess. They come from a study on real alerts from a homelab, re-read by hand: 38 alert cases, with the expected severity decided in advance.

| Model | Role | Memory (32K context) | Correct severities /38 | False alarms /15 | Serious cases missed /11 | Time per analysis |
|---|---|---|---|---|---|---|
| `gemma4:e2b` | | 0.5 GB | 31 | 7 | 0 | 2 s |
| **`gemma4:e4b`** | **Minimum** | 3.2 GB | 32 | 5 | 0 | 4 s |
| `qwen3.5:9b` | | 5.7 GB | 33 | 2 | 3 | 4 s |
| **`gemma4:12b`** | **Recommended** | 7.6 GB | 36 | 1 | 1 | 6 s |
| `mistral-nemo` | | 9.2 GB | 28 | 9 | 1 | 3 s |
| `qwen3.6:27b` | | 16.1 GB | 35 | 2 | 1 | 12 s |
| **`qwen3.8:27b`** | **Top quality** | 16.3 GB | 36 | 0 | 2 | 7 s |
| `gemma4:31b` | | 18.5 GB | 37 | 0 | 1 | 11 s |

*A serious case is missed when the model rates it `info`. Measured before version 1.8.0, once per model, on an RTX 3090 with 24 GB of memory. Nothing was measured without a graphics card. These results come from one machine and one set of logs, in French, so they indicate an order rather than a guarantee.*

## Measured again on 1.8.0
Version 1.8.0 changed how alerts are asked of the model ([Log Line Safety](log-safety.md)), so the 38 cases were played again on `qwen3.8:27b`, twice each, thinking off, against the previous prompts. Correct severities went from 72 to 75 of 76, serious cases rated `info` from 4 to 1, with no false alarm in either. The other models were not measured again.

## Measured again on 1.9 (November 2026)
The three recommended models, plus `bonsai-2:27b`, were played on the same 38 cases with the current prompts, twice each, 32K context, thinking off, plus 33 variants where a line tries to dictate the verdict. Two things came out of it.

* **The `gemma4` models raised false alarms.** On normal lines (a cron session, a periodic request), they flagged an attempt to steer the AI, and Sentinel then kept the alert at warning. Sentinel now counts that flag only when a log line also holds a verdict word (test, simulation, normal, info, resolved…). None of the normal lines does.
* **A hard failure no longer ends as info.** Out of memory, segfault, disk errors, a systemd service restarting in a loop: such a line keeps its alert at warning or above, whatever the model says.

| Model | Correct severities /76 | False alarms /30 | Serious cases rated info /22 | Steering lines handled /33 | Time per analysis |
|---|---|---|---|---|---|
| **`qwen3.8:27b`** | 76 | 0 | 0 | 32 | 8 s |
| **`gemma4:12b`** | 72 (58 before) | 4 (18 before) | 0 | 31 | 4 s |
| `bonsai-2:27b` | 72 | 2 | 2 | 31 | 7.5 s |
| **`gemma4:e4b`** | 62 (52 before) | 14 (24 before) | 0 | 31 | 4 s |

The recommendation stays the same. `bonsai-2:27b` is a 27B model in 2-bit quantisation (9.7 GB): close to the top, but it missed two serious cases and followed steering lines far more often before Sentinel's safeguard (25 of 33, against 14 for `qwen3.8:27b`). Turning thinking on did not help `gemma4:12b` (59 against 58 correct) and made it three times slower.

## Measured again with the one-off question (November 2026)
Each analysis now also says whether the event still needs watching ([Resolution Tracking](resolution-tracking.md)). Asking one more thing can tilt a model's severity, so the three recommended models were played again on the same cases, with the expected answers decided before the run.

| Model | Correct severities /76 | False alarms /30 | Serious cases rated info /22 | Steering lines handled /33 | False alarms closed at once /30 |
|---|---|---|---|---|---|
| **`qwen3.8:27b`** | 74 | 0 | 2 | 30 | 30 |
| **`gemma4:12b`** | 74 | 2 | 0 | 32 | 29 |
| **`gemma4:e4b`** | 62 | 14 | 0 | 31 | 17 |

No case expected to be watched was closed. `qwen3.8:27b` now rates one serious case info on both runs: an isolated root login failure, which can be read either way. A softer wording of the question did no better: 73 correct, and one serious case closed. An event whose line was rewritten to say `severity=info` can still end as one-off when the model believes it; nothing can spot such a line, and its severity was already wrong before.

## Settings that follow from it
* **Temperature: 0.2.** At 0.7, no recommended model improves, and the minimum model invents more.
* **Context: 8K minimum, 16K with the chat, 32K recommended.** The largest exchange measured is 5,900 tokens of prompt and 990 of reply, so 8K is enough for alerts: replayed at 8K, 16K and 32K, `qwen3.8:27b` got 74, 75 and 74 correct severities out of 76. The chat's tools alone take 1,500 to 2,300 tokens, so the chat needs at least 16K. Going to 32K costs 0.1 to 0.2 GB on the three recommended models, with no loss of speed. Ollama reloads a model whenever the context size asked for changes (16 to 42 s for `qwen3.8:27b`), so apps sharing a model on one Ollama server should ask for the same size.
* **Thinking for alerts: off.** An alert is judged without tools. On `gemma4:12b`, 59 correct severities out of 76 with thinking, 58 without, and each analysis three times slower. Switch in *Configuration › Analyses*.
* **Thinking for the chat and investigations: on.** There the model has to pick its tools. On 16 questions that need tools, played twice, `gemma4:e4b` answered 16 right with thinking and 10 without: it asked the user for a rule or alert ID instead of looking it up, and said it showed log lines without showing them. About 1.7 times slower (median 7.8 s against 4.7 s). The second look uses the same setting. Switch in *Configuration › Log chat*.
* **128K** fits the smaller models. On 24 GB, `qwen3.8:27b` overflows the card at 128K (83 to 54 tokens/s) and `gemma4:31b` at 64K (36 to 19 tokens/s).
* **Language: the site's.** Writing prompts in English and asking for an answer in French brought nothing on the large model and missed more serious cases on the small one, so each language keeps its own prompt.

## Long meta-analyses
Over 200 days, only the full context at 32K or more brought out a disk failure signaled by one rule, on both models tested. At 8K, or with the former limit of 50 analyses, it went unnoticed. When everything does not fit, the most serious events now come first, each rule having its most serious event kept before another rule's next one.
