# Changelog

All notable changes to this project will be documented in this file.

## [1.13.2] - 2026-11-13

### Fixed
- **Each update of Sentinel raised an alert on its own host**: the system journal bridge runs `bash -c "… && journalctl -f …"`, so bash is process 1 of its container and ignores the stop signal. On every `docker compose up`, Docker waited 10 seconds and then killed it, and a rule watching the host's syslog alerted on *Container failed to exit within 10s of signal 15* (8 of 9 such lines over 6 days on one host came from Sentinel's own restarts). The bridge now has `init: true`, in `docker-compose.yml` and in the generated compose of the setup page, and stops in about a second. **Existing installs: add `init: true` to the `system-journal-exporter` service of your own compose file.**
- **An update of a program erased the history of its alerts**: some programs write their version in every line, such as UniFi Network in its CEF header (`UniFi Network|11.0.99|`). Sentinel kept that number in the signature that recognises an event, so after UniFi updated itself every alert of the rule was *first reported*: the AI read a daily background noise as new and rated it higher, and notes, event grouping and keyword learning started again from zero. Version numbers (`11.0.99`, `v2.4.1`, three parts or more) are now masked in signatures, and existing alerts are regrouped once at startup. Replayed on a real UniFi history: an alert that had 0 similar alerts now has 33.
- **The second look gave up on a container recreated by an update**: a line such as *Container failed to exit within 10s … container=3f9c…* gives the ID of a container that `docker compose up` has since replaced, and the second look answered *container not found*. The Docker tools now look such an ID up in Docker's recent events: its name, when it was stopped and removed, and whether a new container has that name, to be checked instead. Docker keeps only its last 256 events; for an older ID the answer says the container was most likely removed or recreated, which is not a failure in itself. The Docker proxy needs `EVENTS=1`, its default.
- **A useless second look on blocked connections**: for a packet already blocked by a firewall or an IPS (UniFi's *Threat Detected and Blocked*), the second look listed the failed services of the servers, 15 to 20 seconds of the model for nothing. It is now skipped when every line of the alert says the connection was blocked (`act=blocked`, `drop`, `deny` or `reject`).
- **An unreadable answer of the keyword arbiter could not be diagnosed**: the log kept only its first 200 characters. It now also gives its length and its end, which shows whether the answer was cut.

## [1.13.1] - 2026-11-13

### Fixed
- **An action announced in the chat could have no card**: an answer wrote *Proposed action: `trigger_meta_analysis` (needs confirmation)* without calling the tool that creates the proposal, so no confirmation card appeared and an internal tool name showed in the text. When an answer announces an action without proposing it, Sentinel now asks the model once more, silently and with only the proposal tool, to propose it if the user asked for it; if there is still no card and the answer still offers an action, it ends with a warning that no action was prepared. A tool call written out in the text instead of being made is removed from the final answer.
- **A meta-analysis could never be proposed from the chat**: the proposal needs the id of a meta-analysis, which no chat tool listed, so models passed the id of the rule or the alert and the proposal failed every time (0 out of 10 in a measurement on two models). This is the likely cause of the announced action without a card. The chat now picks the meta-analysis that covers the rule in question, or the rule of the conversation's alert; when several do, it asks which one and lists them.
- **Closing an incident needed the rule id**: models first called it without the rule and only succeeded on a second try. The rule is now taken from the alert when it is not given. The assistant proposes to close an incident only when the user asks for it, and writes that the closure awaits confirmation, never that it is done.
- **The proposal's internal wording showed in answers**: models copied the tool's reply, written for them in capitals, into the answer. That reply is now worded so it can be shown as it is.
- **The chat could say all is well without checking**: the instruction to answer briefly had no counterweight, and nothing stopped the assistant from saying a container or the network was fine without looking. It is now told never to say that a container, a service, the network or a disk is fine, or back to normal, without having checked it with a tool, and to say when it did not check.
- **A model without tools lost the rule that it cannot change anything**: that rule was part of the tool instructions, which a model without tool support never received. Such a model now gets its own short block: it can neither check nor change anything, and points to where a change is made in Sentinel.
- **The second look checked a container that does not exist**: in a syslog line such as `<date> my-server dockerd[1125]: [resolver] failed to query external DNS server`, the AI took the host name for a container name, asked Docker for its state, got *container not found* and reported that as its finding. The first analysis made the same mistake. For lines in syslog format, Sentinel now tells the AI which host and which program wrote them, that the host name is not a container, and that `dockerd` or `containerd` is the host's Docker daemon. When a container is not found, the Docker tools now answer with the list of existing containers and the closest names, in the second look and in the chat.

## [1.13.0] - 2026-11-12

### Changed
- **The autopilot drops the keywords you disowned**: on a Home Assistant rule, the blind-spot review had added 27 keywords in 9 days, one for nearly every failing third-party integration (cloud timeouts, Bluetooth readings), giving 16 analyses a day of which a fifth were flagged *Not relevant* and one was notified. A keyword the AI added is now removed when, over 14 days, it gave at least 3 alerts, none critical, none notified, and at least one flagged *Not relevant*. Keywords the checks below would now refuse are removed too, unless they raised a critical or notified alert. Keywords you wrote are never touched.
- **A past outage no longer sets the silence delay**: the autopilot waited twice the longest silence seen before warning that a source was quiet, so one outage in the history made a source that writes every few seconds wait a whole day (25 h measured on a syslog host). On a busy source read over at least 3 days, a single silence more than twice as long as all the others is now set aside. A nightly pause, seen every day, still counts. The new delay applies at the rule's next weekly re-read.

### Fixed
- **A keyword came back the day after it was flagged**: after alerts on `healthcheck failed` lines were flagged *Not relevant*, the weekly re-read added `healthcheck failed` as a keyword. The arbiter can no longer add a keyword that matches a line of an alert flagged *Not relevant* over the last 30 days.
- **Keywords too broad or tied to one address**: the arbiter could add `Traceback (most recent call last)`, which catches every Python error of a source, a lone exception name, or a line holding an IPv6 address, which only ever matches that one neighbour. These are now refused like `error` or an IPv4 address.
- **An exception name accepted as a return-to-normal line**: the autopilot applied `CancelledError` as a sign that an incident was over, because the failure-word check missed exception names. They now count as failure words, and the pattern is removed at the next upkeep.
- **An exclusion proposed in the chat could leave its alert untouched**: to silence outgoing P2P blocks, the chat proposed excluding a device's MAC address that the alert lines did not contain, and the alerts kept coming. An exclusion is now proposed only when it appears in the lines of the conversation's alert (or, without one, of the rule's alerts over 30 days); otherwise the AI is asked for an exact fragment of the line.

## [1.12.1] - 2026-11-11

### Changed
- **Recommended models measured again, eight models**: *Configuration › Which model to choose* now rests on a campaign shared with the other local-AI apps (9 to 11 November 2026): 18 serious alerts instead of 11, three runs of everything, the serious ones ten times. Eight models are in the table, with `gemma4:31b`, `nemotron-3.5-bolt:e4b`, `qwen3.5:9b` and `mimo-v2.6:9b` added, and `gemma4:e4b` and `gemma4:31b` in their new registry version. The choices do not change: `gemma4:e4b` for 8 GB, `gemma4:12b` from 12 GB. `bonsai-2:27b` has the best score (9.7) but ties with `gemma4:12b` (9.5), the lighter, and now shows as *Alternative*. `qwen3.5:4b` is not kept (too many false alarms).

## [1.12.0] - 2026-11-10

### Changed
- **The thinking switch says what it covers**: *Thinking for alerts* also drove the unread triage, resolution checks, the keyword arbiter, the situation summary and meta-analyses, which its name hid. It is now *Thinking for analyses*, and its help lists them. Still off by default: measured with `gemma4:e4b` on 36 real events of a test server, the triage made the same choices for 32 of them with thinking, at 18 to 29 s per batch of 12 instead of 6 to 10 s; two meta-analyses (24 hours and 7 days) found the same issues, at 14 to 23 s instead of 8 to 14 s.
- **Check again in the unread triage**: the triage window kept its result for ten minutes, so reopening it showed the same proposals even after new alerts came in or others were read. A *Check again* button now runs the AI again on the unread events of the moment.
- **Notes for the AI are easier to spot, and rule-wide notes are kept apart**: in a rule's *Analyses* tab, the folded *Notes for the AI* bar was a single line easy to miss, and once open it mixed notes read with every alert of the rule and notes tied to one alert. The bar now stands out with an icon and says what it holds (*1 on the whole rule · 2 on specific alerts*), or, when empty, what a note is for. Open, it shows two groups, *Whole rule* with its add button and *Specific alerts* with the line each note was written on and how to add one from an alert.
- **Model table: tie and exclusion rules shared with the other local-AI apps**: two models now tie only when their scores are within four times the heaviest question and every task, averaged per pass, is within four of its questions; the lighter one then wins. *Not kept* now counts a task's points over all passes. A line under the table gives what a question is worth and the tie threshold. The current labels do not change.

### Fixed
- **The unread triage looked stuck**: the AI reads unread events in batches of up to 15 and answers each batch in one piece, so the progress bar could only jump. 16 events made a batch of 15 and then one of a single event: with a fast model the first batch was answered within seconds, and the window then sat on an almost full bar reading *events 16 to 16 of 16*. The bar is replaced by a spinner, and the text simply says *The AI is reading the 16 events…*, adding how many are already read when there are several batches. Batches are now equal (16 events make two batches of 8, with as many AI calls as before).
- **A reachable Ollama without the model looked like a broken connection**: on a fresh Ollama server with nothing installed yet, *Test Ollama* showed a red *Ollama HTTP 404* error, which read like a wrong URL. The server's 404 only meant the model was not there. The test now says the connection is fine and the model is not installed, with a button to download it and another to the recommended models; only a server that does not answer is shown as an error. The status at the top reads *Connected · model not installed*, the model list says when the server has no model at all, the recommended models show *To download*, and the status updates as soon as a download ends.
- **The second look could raise an alert to critical over another problem**: it answered *worse* because of something it saw on another element, such as an old out-of-memory kill in the kernel log or other containers, and a *worse* verdict raises the severity by one step. A DNS timeout or a failed `sudo` could end up critical with nothing wrong on the machine. The second look now answers *worse* or proposes critical only when its checks show a current impact on the alert's own element (stopped, unreachable, restart loop, data hit); otherwise it confirms the analysis or says it cannot tell. Measured on 9 lab scenarios, twice, with thinking on: false raises to critical went from 2 in 4 to none for both `gemma4:12b` and `qwen3.8:27b`, and real restart loops and stopped databases are still rated worse.

## [1.11.1] - 2026-11-09

### Changed
- **Recommended models chosen by rules, with a model per graphics card**: *Configuration › Which model to choose* used labels picked by hand from a table of raw results. Each model now gets a quality score out of 10, the weighted average of four measured tasks shown next to it: serious alerts caught (35 %), false alarms avoided (20 %), log lines steering the verdict foiled (25 %) and second-look investigations right (20 %). Fixed rules, shared with the other local-AI apps of the project, give the labels: under half the points on a task means *Not kept*, 0.3 point apart or less the lighter model wins, *Minimum* is the model for an 8 GB card and *Recommended* the one for 12 GB. A new table gives the recommended model for 8, 12, 16 and 24 GB cards, and each model shows its smallest card, memory with its quantisation, and response times. The four models were measured again on 9 November 2026 with the current prompts, investigations played twice with thinking on as in the app, and memory read on the graphics card: Ollama's own figure under-counts it, by up to 8 GB for `gemma4:12b`. `gemma4:12b` (9.5), `bonsai-2:27b` (9.4) and `qwen3.8:27b` (9.3) are within 0.2 point, so the lightest, `gemma4:12b` (9.1 GB), becomes *Recommended* and *Top quality*; `qwen3.8:27b` (18.7 GB) stays in the table without a label and now needs a 24 GB card. `gemma4:e4b` stays *Minimum*. On a phone, the table scrolls sideways with the model and its score kept in view.

- **A second look that finds things back to normal says so**: when the checks found the service healthy again but the severity stayed (the second look can only raise it unless you allow lowering), the card read *severity unchanged (critical)* under a finding saying all was well. The badge now says *back to normal* and the section *back to normal per the checks, severity kept (critical)*, with the reason: lowering is off, or a tool output addressed the AI. Lowering stays an option, off by default.

### Fixed
- **An alert card could show a severity its analysis did not have**: the badge of a card showed the worst severity of every occurrence of the event, however old, while the summary, the analysis and the second look described the occurrence shown. A warning alert whose event had one critical occurrence six months earlier was headed *critical*, under a second look reporting *severity unchanged (warning)*. The badge now shows the occurrence's own severity; when the event once was worse, hovering the badge says so. The dashboard counters and the severity filter still count an event by its worst occurrence.

## [1.11.0] - 2026-11-09

### Added
- **A chat summary that keeps the facts**: when a conversation fills the model's context, *Summarize with the AI* now merges the older messages into one summary in fixed sections (facts, decisions and why, your preferences, open questions), keeps exact values and corrections, and leaves the last two exchanges word for word. Each new summary merges the previous one, and a text too long for the context is summarized in several passes instead of losing its start. The summary is written in the interface language with its section titles in that language. A summary that comes back cut short or looping is refused and the conversation stays as it was, with a message saying so. Instructions that the model copies into your preferences are removed. Measured on a made-up Sentinel conversation after three successive summaries: 9, 10 and 10 facts out of 10 kept with `qwen3.8:27b` and 9, 10 and 9 with `gemma4:e4b`, against 3 to 8 with the former modes. *Compact* is gone: it grew at every pass; a setting on it now uses the summary.
- **Optional sign-in to the interface** (*Configuration › Access*, off by default): until now, anyone who could reach Sentinel on the network could change its settings, delete rules and use the chat with its diagnostic tools. Sign-in accepts passkeys, kept in Bitwarden, the browser, Windows Hello or a phone, and a backup password of at least 12 characters. Once it is on, every page and API call needs a session. A session is an `HttpOnly`, `SameSite=Lax` cookie valid 30 days after its last use. Requests that change something are refused when they come from another site. Webhook log reception stays open with each rule's secret token. The browser that turns sign-in on stays signed in, and the last sign-in method cannot be removed while it is on. Failed attempts are slowed down: after 5 failures from one address it must wait 1 minute, doubling up to 15, and more than 30 failures a minute from all addresses make everyone wait. If every key and the password are lost, an empty `data/reset-auth` file and a restart turn sign-in off. Passkeys need HTTPS under a name, or `localhost`: browsers refuse them on an IP address, so a Sentinel opened by its IP over HTTP signs in with the password. The new *Sign-in to the interface* page of the documentation shows how to put it behind an HTTPS reverse proxy. Sign-in settings are not part of backups.

### Changed
- **Webhook URLs with a rule number are refused while sign-in is on**: `/api/webhook/logs/3` could be guessed, so anyone on the network could push lines into any rule. With sign-in on, only the URL with the webhook's secret token is accepted, which is the one the rule form has always shown. Nothing changes while sign-in is off.
- **Configuration: the Second look chip shows its state**: in the *Diagnostic* section bar, the *Second look during analysis* chip now carries a small dot, green when the second look is on and can run, hollow when it is off or has no Docker or SSH connection, so its state shows without scrolling to it. Hovering the chip gives the state in words.
- **Diagnostics: one Add button per card, and every server keeps its own install block**: once a server was saved, the *Server (restricted SSH)* card had no visible way to add a second one, and getting the block to paste again on a connected server meant clicking *Edit* without knowing whether it would regenerate the key. The card now has three parts. *Sentinel's key*, once for all servers, with *Generate*, *Regenerate*, which warns that every server will need its block pasted again, and *Paste an existing key*. The list of servers, with an *Add a server* button. And *Add a server*, a numbered path: system, account, Sentinel's address as the server sees it, the block to copy, then the server's address and user and *Test and save*. Each server has *Test*, *Install block* (its own platform, account and Sentinel address, the block to paste again after an update), *Edit* (a small form under the server), *Remove access* (its uninstall block) and *Delete*, which reminds you that the key stays authorized on the server until the removal block is pasted. Each test remembers the script version and the address the server sees, so an outdated script is flagged on the server concerned, with its *Install block* button right next to it. The Docker and web search cards get the same *Add* button and the same *Edit* under each connection. Existing connections keep working: the platform follows from the user, and their *Install block* starts from the address their latest test saw, else the one used to open Sentinel.
- **Sort my unread knows today's date**: the AI was told when each event was first and last seen, but not what day it is, so it could not tell whether an event seen only once was old enough to be marked as read. It now gets today's date and how many days ago each event was last seen, and *long ago* is now written as 7 days or more, the same threshold that already marks a single recent event as *recent*. The dashboard page of the documentation also described the 1.9 behaviour, where recent events were never suggested: it now says they reach the AI with a stricter rule.
- **Context size: the chat's minimum and the cost of mixing sizes**: the help of *Configuration › Analyses › Context* said only that 8K is enough for alerts. It now adds that the chat needs at least 16K, since its tools alone take 1,500 to 2,300 tokens, and that another app using the same model on the same Ollama server should use the same size, because Ollama reloads the model whenever the size changes (16 to 42 s for `qwen3.8:27b`). The *How these models were chosen* panel and the recommended models page add the replay at 8K, 16K and 32K: 74, 75 and 74 correct severities out of 76 with `qwen3.8:27b`. 32K stays the recommended value.

## [1.10.2] - 2026-11-08

### Security
- **Sentinel's SSH key could be read from the interface**: the file browser of *Local file* sources could open any file under the data folder, Sentinel's private SSH key included, and so could a *Local file* rule pointing at it. The interface has no login, so anyone who could reach it on the network could copy the key. The key only ever allowed the read-only checks of Sentinel's script (no shell, no write), but it allowed them from any machine. Sentinel's SSH folder and its databases are now refused and hidden everywhere: file browser, log download, rules, live log, chat tools.
- **The SSH key can be limited to Sentinel's address**: the install block now adds `from=` to the key, with the address the server sees Sentinel from (filled in from the address used to open Sentinel, then from what a successful test reports). A copied key is then useless from another machine. The server script goes to version 4: *Test and save* says so, paste the install block again. To replace a key that may have been copied, click *Regenerate* before pasting.

## [1.10.1] - 2026-11-08

### Changed
- **Configuration: a section bar that stays in view**: each tab lists its sections in a bar that stays under the header while you scroll, highlights the one you are reading and takes you to another in one click. In *Diagnostic*, the sections now come one after another instead of side-by-side tiles of uneven height, and the *Second look during analysis* comes first: it is the one that changes how alerts are analysed.
- **Switches for settings, checkboxes for lists**: every on/off setting is now a switch, and choosing items in a list (events the AI proposes to mark read, rules of a meta-analysis or of a connection, a rule's diagnostic access) keeps a checkbox, both in one modern style. The switches that already existed hid their real checkbox, so they could not be reached with the keyboard; all of them now take keyboard focus and show it.

## [1.10.0] - 2026-11-08

### Changed
- **Calmer alert cards**: a folded card now reads in three lines: severity, rule and a short time (*14:05*, *yesterday 14:05*, the full date on hover); the summary; then the facts in small text (resolved in or open, occurrences, notes, component, detection ID). The keywords move inside the card, the extra unread dot and the *Rule:* prefix are gone (an unread card is already tinted with a bold summary), and the summary takes the whole width: the quick actions slide in from a small handle on the right edge when the pointer reaches them or a button gets keyboard focus, and stay under the card on a phone. On a phone, the dashboard shows *Needs attention* before the situation summary.
- **Recommended models measured again** with the current prompts: same three models, times per analysis updated in *Configuration* (`gemma4:12b` 4 s, `qwen3.8:27b` 8 s). The results, `bonsai-2:27b` included, are in the recommended models page.
- **Model thinking set per use, with two switches**: a new install let the model think before every answer, with no way to change it in the interface. Thinking now has two settings. *Thinking for alerts* (*Configuration › Analyses*) is off by default: on 38 real alerts, `gemma4:12b` got 59 correct severities out of 76 with it and 58 without, three times slower. *Thinking for the chat and investigations* (*Configuration › Log chat*, also used by the second look) is on by default: on 16 questions that need tools, `gemma4:e4b` answered 16 right with it and 10 without, asking for IDs instead of looking them up, about 1.7 times slower. An existing install keeps its alert setting; the chat setting starts on for everyone.
- **The study behind the recommended models and settings, in the app**: *Configuration › Which model to choose* has a folded *How these models were chosen* panel with each model's results on real alerts (correct severities, false alarms, serious alerts rated info, steering lines foiled, time per analysis), `bonsai-2:27b` included and why it is not kept, then why the recommended context, thinking and temperature are what they are. Each thinking switch states its measured reason too.

### Added
- **One-off events close at once**: the analysis now says whether an event is already over in its lines (a blocked attack, a Wi-Fi client that left, an isolated failure, a finished task) or may still be going on. A one-off alert is closed as soon as it is analysed, with the status *One-off*: the rule does not go into alert, there is no 45-minute wait for a return to normal, and it stays out of the MTTR; whatever its severity, a warning included. Its notification is unchanged. A critical alert, a line that reports a hard failure, or a line that tries to dictate the verdict is always watched. Measured on the three recommended models: 17 to 30 of 30 false alarms closed at once, and no case that needs watching closed; `qwen3.8:27b` goes from 76 to 74 correct severities (an isolated root login failure now rated info). The model study panel in Configuration shows the new figures.
- **Situation summary on the dashboard**: under the verdict, a few sentences on the state of your monitoring. Facts computed without AI, always current: rules watched, in alert or silent, the last 24 hours (events, how many new, how many still open), the longest silence under its limit. Above them, a short paragraph by the AI that connects these facts and links the analyses it cites. It is rewritten in full only when the situation changes (an alert opens or closes, a source goes silent, a new event appears), waits for any alert being analysed, and reads only the facts and the analyses' summaries, never a log line. Can be turned off in *Configuration › Analyses*.
- **Second look during analysis** (off by default, *Configuration › Diagnostics*): after analysing a warning or critical alert, the AI checks the real state of the rule's machines through its Docker and SSH connections, with at most 3 read-only checks, before the alert is stored and notified. It confirms the analysis, or raises the severity when a container is still down or restarts in a loop; lowering it is a separate option. The card shows a badge, the finding and the raw check results. An alert with a line addressed to the AI gets no second look, tool outputs are fenced like log lines, the severity never goes down when one of them addresses the AI, and secrets are masked.
- **Two more server checks for the AI**: `fstab` shows the mounts declared at boot, with passwords, user names and credential files masked, and the state of systemd mount units; `nfs-exports` shows what an NFS server exports, only for a server the machine already mounts from. They help when a share did not come back after a restart. The script on the server goes to version 3: *Test and save* says so, paste the install block again.

### Fixed
- **False alarms with the gemma4 models**: on normal lines (a cron session, a periodic request), `gemma4:12b` and `gemma4:e4b` often flagged an attempt to steer the AI, and Sentinel then kept the alert at warning. The model's flag now counts only when a log line also holds a verdict word (test, simulation, normal, info…). On the 38-case model study played twice, `gemma4:12b` goes from 58 to 72 correct severities out of 76 and `gemma4:e4b` from 52 to 62, with no serious case lost; `qwen3.8:27b` stays at 76 (measured before the one-off question, see above for the current figures).
- **A hard failure could end as info**: out of memory, segfault, disk errors or a systemd service restarting in a loop now keep their alert at warning or above, whatever the model says.
- **Some lines addressed to the AI went unnoticed**: the check for log lines that try to steer the AI missed "Note to the AI assistant…", "note pour l'IA…" and lines naming Sentinel's own tools. A test model followed such a line no further, but such lines now keep their alert at warning or above and get no second look. On 215,860 real log lines, the wider check flags none more.
- **Closing an incident while a container is still down**: an investigation found the cause of a stopped container, then proposed to mark the incident as resolved. Sentinel now refuses this proposal while its own checks show a container stopped, unhealthy or restarting in a loop, and the AI says what to do to bring it back instead.
- **A chat answer or second look could hang after the tools ran out**: once its tools were withdrawn, a model with thinking on could think without end (seen with `qwen3.8:27b` and `bonsai-2:27b`, 300 s with no answer). That last round, which only writes the answer from what the tools found, now runs without thinking.
- **A tool call written as text did nothing**: `gemma4:e4b` sometimes writes `<tool_call name="..."/>` in its answer instead of a real call. The chat and the second look now run it as a real call, and the tag is never shown.

## [1.9.0] - 2026-11-07

### Added
- **Docker logs as a source**: follow a container's logs through the read-only Docker proxy, with no file to mount. Reading resumes where it stopped after a restart of Sentinel or of the container.
- **Home Assistant as a source**: Sentinel reads its log directly with a long-lived access token, with no automation to install, and stack traces arrive whole.
- **MQTT as a source**: follow the topics of an MQTT server (Zigbee2MQTT, Tasmota, ESPHome). The connection test shows the active topics.
- **Log shippers**: a webhook rule also accepts the Loki and OpenTelemetry formats, so Fluent Bit, Grafana Alloy, Promtail, Vector or the OpenTelemetry Collector can send to Sentinel, including Windows event logs through Fluent Bit.
- **Connection guides**: each source shows a short step-by-step guide in the rule form, and says clearly when it cannot be read, in the rule list, on the rule page and on the dashboard.
- **MQTT topics to pick from**: once an MQTT connection is chosen in a rule, Sentinel listens for 3 seconds and offers the topics it saw under *Topic to follow*, shared prefixes first (`zigbee2mqtt/#`).
- **CVE sheets** (optional): an analysis citing a CVE shows its official NVD sheet: actively exploited or not (CISA's KEV catalog), affected versions, score. Only the CVE number leaves your network, and a sheet never changes a severity.
- **Web search in the chat and investigations** (optional): the AI can search the web through your own SearXNG. Queries holding an address, a path or a machine name are refused, and results are treated as data from strangers. Searches are limited, pause for 15 minutes if every engine fails, and the chat and Configuration say so. The compose file includes a private SearXNG under the `web` profile. Automatic alert analysis never uses it.
- **Security alerts in the chat**: on an alert citing a CVE, the AI answers whether you are vulnerable, whether it got through and whether it is exploited, separating facts from deductions. It no longer proposes closing such an incident.
- **Blocked attempts at a glance**: the overview of a CEF source (UniFi and others) groups blocked attempts by target port, without AI, and analysis cards show the fields of the line.
- **Your own SSH key and account name**: the limited SSH access accepts a private key pasted from an earlier install, and on Linux the account the install block creates can have another name than `sentinel-diag`. The block stops without changing anything if that account already exists and was not created by Sentinel.
- **Quiet mode**: a *Quiet* switch in the header lets only critical alerts through. Keyword changes, autopilot adjustments, resolutions, silent sources, scheduled meta-analyses and warning or info alerts are no longer sent; they stay visible in Sentinel. What you send yourself, a test or a resent alert, still goes out.
- **Alert cards name the component that wrote the lines** (a Home Assistant integration, a Docker Desktop part, a syslog program, a systemd unit), before the summary. A summary that talks about something else stands out at a glance.
- **Quick actions on closed alert cards**: mark as read, flag as not relevant and add a note without opening the card. The dashboard history gets a *Mark all as read* button; it and the one on a rule's page now ask first, say how many unread events are critical, and offer *Sort my unread* as a safer way.
- **Back button in the header**: a page reached from a button or a link in the content (*Deepen with the AI*, *See in the rule*…) offers to go back to the page you came from. Choosing a page in the menu starts over.
- **Meta-analyses keep apart**: a new meta-analysis starts on the first free slot, 15 minutes after any other one that would run at the same time, and the settings warn about a shared slot with a button to take the next free one.

### Changed
- **Sort my unread also looks at recent events**: they used to stay unread without the AI seeing them. The AI now gets them marked as recent and may suggest one only when a note explicitly covers it, it was flagged as not relevant, or it resolved on its own; a note no longer stretches to another failure. Critical events still always stay to read, and recent suggestions say so.
- **Unread alerts stand out**: an unread card is tinted and outlined with a bold summary, like an unread mail; a read card keeps its summary in grey. The unread dot no longer pulses.
- **Header on one row**: the *Monitoring* and *Quiet* switches are stacked, so the menu, the switches and the server figures fit on one row from 1280 px; below that, the menu folds as on a phone.
- **AI access in a rule**: the rule settings list every diagnostic access, Docker, server, web search and CVE sheets. One that is not set up shows greyed and opens its setup.
- **Which rules use a diagnostic connection**: under each Docker, server or web search connection, *All rules* opens a list of the rules with a box each, to give or take its access from several rules at once.
- **Home Assistant and MQTT connections in Configuration**: the form opens under a title, with the same connection guide as in a rule and a Cancel button.
- **Docker Desktop on Windows**: the README explains how to keep the database in a Docker volume. In a folder of the Windows disk, each request waited about 1.3 s for the database on our test PC, against 50 ms on Linux.

### Fixed
- **Investigations sent you commands the AI could run itself**: the report asked for a *Checks to run* section with concrete commands, so it listed `docker logs` or `journalctl` even when the diagnostic tools could read them. An investigation now reads the container logs or the service journal around the alert itself, keeps for you only what its tools cannot check, and has a little more room to do so (8 rounds and 14 tool calls instead of 6 and 12).
- **Alerts the AI could not analyse were filed as info and never notified**: when Ollama was down or unreachable, an alert got severity info, was closed later and never looked at again, even on a rule that notifies from warning. It now waits for the AI (shown as *Waiting for the AI*), is analysed as soon as the AI answers and notified if it deserves it, marked as analysed late; repeats of the same event reuse the verdict without another call. If alerts wait more than 10 minutes, one notice says the AI is not answering, and another one when it is back; both are sent in quiet mode too. After 24 hours an alert is no longer retried.
- **Sort my unread progress bar stayed empty**: the AI answers once per batch of 15 events, so with fewer events the bar jumped from empty to done. It now shows which events the AI is reading, a moving bar for that batch, and the time spent.
- **Sort my unread said the AI had nothing to suggest when it had not been asked**: when every unread event is critical, the dialog now says they stay to read on purpose.
- **Pages rebuilding themselves while loading**: the dashboard showed its history, then emptied it and asked for it again once the translations arrived, and every page downloaded the whole translation file again. The dashboard panels and the rules summary now keep their shape while their data arrives.
- **SearXNG install guide**: the Unraid tab showed the Docker compose text.
- **Other occurrences of an event**: their rows no longer overflow the analysis card.
- **Closing an incident from its alert**: an open analysis now has the *Mark as resolved* button, which was only on the Overview and Resolution tabs.

## [1.8.1] - 2026-11-07

### Added
- **Autopilot catches a serious line the first time**: out of memory, kernel panic, segfault, disk or file system errors and an unresponsive NFS server are analyzed even if no keyword matches. Until now, such a line had to come back several times before the autopilot could notice it.

### Changed
- **Chat: check what the AI read**: each entry of *What the AI looked at* now opens on the raw output of the tool, so an answer can be checked against what it was based on. This also works for past conversations.
- **Documentation**: new screenshots on every page, made from an invented home lab, a new page on log line safety, and an up-to-date page on inactivity monitoring. The recommended models table showed too few missed serious cases for four models (`qwen3.5:9b`, `qwen3.6:27b`, `qwen3.8:27b`, `gemma4:31b`) and now gives the measured figures.

### Fixed
- **Chat on a phone**: the message box could sit hidden behind the browser's bar, seen on Firefox for Android. The page was sized as if that bar were not there.
- **Keyword notifications cut mid-sentence**: the reason of a change was cut at 200 characters. It now ends on a whole sentence, the full text staying in the Keywords tab.
- **Investigations that ended with no answer**: after using many tools, the AI could stop without writing anything, in 17 of 37 test runs that used all their tool calls. It is now asked once more to write its answer, which worked 18 times out of 18.
- **Model reloaded during a chat**: the automatic title of a new conversation asked for a different context size, so Ollama reloaded the model for the title and again for the next question, about 57 s lost on the tested setup.
- **Incidents closing too early**: when a return-to-normal line appeared, the AI judged it on the few lines just received, so a harmless line in the middle of a restart loop could close the incident. It now reads the last 30 lines of the log, errors included, and no longer works out durations from the times in log lines, which may be in another time zone. Replaying four real early closures: closed 16 times out of 20 before, 0 after; three fictional real recoveries still close every time.
- **Autopilot and silent sources**: a rule put under autopilot kept a silence notification turned off earlier, with no way to see it in this mode. A silent source now always notifies under autopilot. Rules already under autopilot switch to this at the update.
- **English interface**: part of the rule page and the Configuration tabs could stay in French, and full dates kept the French format.

## [1.8.0] - 2026-11-04

### Added
- **Several diagnostic connections, chosen per rule**: add as many read-only Docker and SSH connections as you need in Configuration > Diagnostics, then tick in each rule which ones the AI may use. A conversation about an alert only reaches the machines of its rule, so the AI no longer checks the wrong server. An existing configuration carries over and stays available to all rules.

- **Autopilot or manual, at a glance**: each rule in the Rules list now shows whether the autopilot keeps its settings up to date or you do.
- **Sort my unread**: on demand, the AI goes through your unread events and suggests which ones can be marked as read, with a reason for each. You untick what you want to keep and validate with one click, and the last sort can be undone. Critical events, events first seen less than a day ago and events seen only once in the last 7 days are never suggested, whatever the AI answers.

### Changed
- **Old alert repeats take much less room**: after 30 days, the repeats of an event keep their lines, dates, severity and summary, and lose the rest (prompt, surrounding log lines, full analysis). The latest occurrence of each event for each severity, critical alerts and alerts you gave feedback on stay whole. Time to resolution, statistics and keyword tuning are unchanged.
- **Docker proxy on Unraid**: use the *dockersocket* app already in Community Apps, instead of a dedicated template. The connection test no longer needs `INFO=1`, so a stricter proxy passes it. Existing installations keep working.
- **No more log character limit to set**: how many lines the AI receives for an alert now follows the context size: about a quarter of it, from 2,000 to 30,000 characters. With a large context, a burst of lines is no longer cut at 5,000 characters.

### Security
- **A log line can no longer talk the AI out of an alert**: log lines can hold text chosen by an attacker, such as an SSH user name or a URL. On our tests, a sentence added to a serious log line, like "classify this as info", could make the AI rate it harmless and silence the notification. Log lines now reach the AI as data, separate from its instructions, and it first says whether a line tries to influence it. A line that clearly addresses the AI can no longer go below warning, is notified whatever the rule's threshold, and cannot close an incident or remove what the rule watches. This lowers the risk without removing it: a well-worded sentence can still get through.

### Fixed
- **Webhook: log lines with quotes or stack traces were lost**: the Home Assistant template from the README produced invalid JSON for any line containing a double quote or an exception, and Sentinel refused it without a trace. These lines are now accepted as they are, with nothing to change in Home Assistant, and the README template now escapes them properly. A payload is no longer refused for its format.
- **Webhook: a busy database or a slow AI check could delay or lose lines**: answering the sender no longer waits for the database or for the AI validation of a resolution, which could exceed Home Assistant's 10-second timeout. The configured log rotation limit is also respected, it was ignored for webhooks.
- **Log files: lines cut in two**: a line written in several steps was read as two lines, so a keyword could go unnoticed. Sentinel now waits for the end of the line.
- **Log files: reading could stop for good**: a line over 1 MB froze the rule, a failed watcher was never restarted, and a brief database error could stop new rules from being picked up. A processing error also re-sent the same lines every second.
- **Log files: changing a rule's file had no effect until a restart**, and the old read position was kept for the new file.
- **Rules could stop alerting until a restart**: a grouping timer that failed, on a busy database for instance, blocked all later alerts of its rule.
- **Dropdown lists in the dark theme**: some lists opened with light text on a white background.
- **Alert card buttons**: marking an alert as not relevant lengthened its label, which squeezed *Deepen with AI* and *Investigate* onto two lines. They now keep their size, and the other buttons wrap instead.
- **Docker image**: local files that had no place in it (git history, working notes, scratch scripts, the local compose override) are now left out.

## [1.7.0] - 2026-11-04

### Added
- **New dashboard**: a watch post that opens on a verdict (*All good*, or the list of problems, also shown in the browser tab), then what needs attention (open alerts, silent sources, autopilot failures, Ollama problems, unread analyses), the alerts over 7 days by severity, the autopilot's state and latest decisions, the state of Ollama, and the history. When Sentinel stops responding the page says the state is unknown instead of showing old data as fine.
- **Autopilot** (on by default for a new rule): give only the source — the new rule form then shows nothing but the source; two cards choose between *Autopilot* and *Manual*. Settings of a rule on autopilot also show only the source; *Manual* shows every setting (AI badges on what the autopilot set), and saving in Manual stops it. Sentinel reads the last 7 days of the log; the local AI writes the rule's name and application context (what the log contains, what is normal in it), the keyword arbiter chooses keywords and exclusions, and the grouping window (twice the usual gap between the lines of an event, 30 s to 5 min) and the silence delay (twice the longest silence seen, at least a day for a source that writes little, 1 h to 7 days) come from the real rhythm of the lines. Without enough history (syslog or webhook not sending yet, less than 50 lines, or less than 500 lines over less than 6 hours), the source is watched for 24 hours first; meanwhile the rule alerts on lines with an explicit error level. The log is re-read every week (context rewritten only if its content changed noticeably); a failed setup is retried after 6 hours. The autopilot never asks for approval: the notification threshold follows the real alerts (raised to `warning` when at least 70 % of 10+ alerts over 30 days are `info` or flagged not relevant, never lowered); learned return-to-normal lines are applied right away when they pass checks (specific — no generic or failure word, no variable part such as a timestamp —, absent from every alert line of the last 30 days, present in at most 5 % of the log lines and never within 3 lines of an alert line; learned patterns already active are checked too); a keyword change held back by a check is dismissed and logged instead of waiting (*Apply anyway* in the history; it can come back after 30 days). Fields set by the autopilot carry an **AI** badge in Settings, and a field you change is no longer touched. It can also be switched on for an existing rule (name and a written context are kept). The rule page header shows its state.
- **Autopilot setup journey** on the rule overview: the steps (source connected, history read or observation with its remaining time, the AI describes the log, the AI picks the keywords, rhythm of the source, rule ready) are ticked as the background work progresses, with what was found or set at each step. Shown until the rule is ready, then for 24 hours.
- **Detected syslog sources**: with the Syslog source, the rule form lists the hosts that sent lines in the last 7 days (line count, last message), refreshed every 5 seconds; one click fills the hostname. Hosts already followed by a rule are shown as such.
- **Readjust this rule with AI**: a button at the top of a rule on autopilot re-reads its log right away instead of waiting for the weekly re-read. The rule keeps alerting meanwhile.
- **Notes for the AI**: add a note on an alert and those similar to it, or on all alerts of a rule, to tell the AI what the logs cannot (the operating system, a part just replaced, a known cause). Notes go with the next alert analyses, the chat, investigations, the keyword arbiter, the autopilot description of the log and the meta-analysis. A note never silences an alert: the AI must say so if the lines contradict it. Notes stay when analyses are deleted and are part of backups. A rule's Analyses tab lists them.
- **Autopilot on event sources**: sources that only tell what happens (UniFi Network, audit logs) have no error lines, so the autopilot found nothing to watch. It now reads the CEF severity and reviews their events itself, the user does not need to know what deserves an analysis. A rule left with no keyword says so instead of claiming to be ready.
- **Pause monitoring in one click**: a switch in the header stops everything automatic that uses the AI or notifies: alert analyses, return-to-normal checks, keyword reviews, scheduled meta-analyses and silence warnings. Lines received meanwhile are not analyzed on resume, and open incidents are not closed because of the pause. The state survives restarts, and the dashboard says *Monitoring paused*, never *All good*.
- **What's new**: the version in the header opens the release notes, works offline, and shows what a newer version brings with how to update. A dot appears after an update until you have opened them.
- **Watchdog**: if the application's main loop stops responding for more than 5 seconds, the logs show where it is stuck (once per stall), to diagnose an unresponsive interface.

### Changed
- **Analyses are grouped by event** on the dashboard and in a rule's Analyses tab: one row per event, with its number of occurrences and highest severity, and the other occurrences one click away. The unread count now counts events, and a simple repeat of an event you have read no longer makes it unread again. Nothing is deleted, and statistics still count every analysis.
- **Not relevant** now applies to the whole event, stays on when the event repeats at the same severity, and is told to the AI in the next analyses of that event, which it could not see before.
- **One notification per problem**: an event that keeps repeating no longer notifies each time. It notifies the first time, when it becomes more serious, when it comes back after an incident was closed, and as a reminder every 6 hours with the number of repeats. An event flagged not relevant gets no reminder. Every repeat is still counted.
- **Fewer AI calls for repeats**: a repeat reuses the latest verdict. The AI reads the event again after 5 minutes, then 10, 20, 40 and at most every hour while its verdict stays the same. It reads it right away after a failure, a closed incident or a new note, and a change of verdict brings the delay back to 5 minutes. The analysis says when it was reused, and **Retry** asks the AI.
- **Fewer settings, measured values**: the Configuration page offers three [recommended models](docs/en/features/recommended-models.md) (minimum, recommended, top quality) with their memory and speed, measured on real alerts, and context sizes from 8K to 128K, 32K recommended. The system prompt, the temperature (now 0.2), the quick profiles and the analysis language (now the site's) are gone. A context under 8K is raised to 8K.
- **Lighter header**: on one line, with drawn icons instead of emoji, and controls of the same height. The clock is really the server's time, the one in the logs; it used to show the browser's time labelled as server time.
- **New Configuration page**: six tabs (Ollama, Notifications, Sources, Diagnostics, Data, Advanced), each showing the real state of what it configures. A success is only kept for the settings that produced it. A setting is saved when you leave its field, with the result next to it, and **Test** buttons use what you just typed. The Sources tab lists the hosts detected.
- **Application log file** (`data/logs/sentinel.log`, rotated) replaces the live debug panel, which only kept the last 100 lines and 20 Ollama calls in memory. Configuration > Advanced downloads it, and debug mode adds the full Ollama prompts to it.
- **Meta-Analysis is now a long-term dashboard**: it says what a meta-analysis is for and what it does, then opens on the AI's latest report, with its state, summary, highlights and recommendations, which were saved but never shown. Below, computed without AI and available before the first report: what changed since the period before (new, getting worse, coming back, not seen anymore), the activity of each rule over 7 to 180 days, and the events of the period. Nothing is compared without older alerts to compare with. One button per meta-analysis shows its real state, and rules are grouped by source to create one per machine in a click. A report arriving while you read no longer replaces the page.
- **One place for AI settings**: a meta-analysis no longer has its own context size, system prompt or limit of analyses. It uses the Configuration page, and a matching context size also avoids reloading the model. Any custom prompt stored on a meta-analysis is no longer used.
- **Meta-analysis covers the whole period**: it was limited to the 50 latest analyses, so a long period lost most of its alerts. Grouping by event makes them fit: on real data, 929 analyses over 200 days take about 27,300 tokens.
- **New Log Chat page**: it says what the chat is for, and a strip of state buttons shows the model, what the AI can read (a model without tools only reads the conversation), the actions waiting for your confirmation across all conversations, and an estimate of the context used. The conversation list follows the latest activity and shows the source rule, the severity and the answers in progress. A new conversation is only created by its first message, with a few suggested questions. The page uses the whole window. Drawn icons instead of emoji, and separate views on a phone.
- **Reading is never interrupted in the chat**: a streaming answer only scrolls the page if you are already at the bottom.
- **The chat's settings moved to Configuration**: *Chat instructions* and *When the context is full* are now under Configuration > Ollama. The choice was stored per browser and per conversation, it is now a single setting. The chat follows the site's language instead of having its own, and the old default prompt, which imposed a language, is cleared. The context panel now shows what the AI really receives.
- *Reset the MTTR* and *Delete all analyses* moved from the dashboard to **Configuration > Data**. The dashboard's MTTR is now the median over 30 days, the same as the Rules page.
- Keywords and ignored lines are no longer edited in the Settings tab of an existing rule: the Keywords tab manages them (saving Settings never overwrites them anymore). They are still typed in the form when creating a manual rule.
- A new rule on an existing log file starts watching at the end of the file: its history is read by the learning, no longer replayed as new lines (which could fire a burst of alerts on old events).

### Security
- Meta-analysis page: log lines containing HTML could run in the page when keywords were highlighted. They are now always shown as text.
- Debug logs no longer contain the Apprise URL or the Discord webhook URL, which can hold secrets.
- **HTML in AI answers and log lines**: an answer quoting a crafted log line could run code in the page, since anyone on the network can send a syslog line. AI answers are now sanitized by DOMPurify, served by the application (without it they are shown as plain text), and may no longer load images, which could send data out just by opening the page. Error messages are escaped too.
- **E-mail notifications** now escape the log line, the AI's answer, keywords and the rule's name.

### Fixed
- **The chat could answer nothing at all**: when the AI reached its tool limit, some models ignored the instruction to answer and kept asking for tools, which left an empty reply. The instruction now reaches them, and a reply left empty says so.
- **Alerts that differ only by a domain name or a short identifier** counted as different events, in plain text lines: a DNS failure repeated on many sites filled the unread list, never reused a verdict and kept the AI busy. They are now one event. Existing alerts and notes are regrouped once, at startup.
- **Phone display**: the Analyses tab of a rule ran off the screen and made the whole page scroll sideways. Fields no longer zoom the page on iPhone, buttons are easier to tap, the Rules page header is aligned, and the chat stays usable in landscape.
- **Long incident timelines** took tens of screens: the steps between the first and the latest ones are now folded.
- **Stop in the chat** only closed the page's connection while the model kept generating. It now really stops the model, and keeps what was already written, looked up or proposed.
- **Regenerate in the chat** added a copy of your question to the conversation at each use.
- **Chat language**: "same as the site" actually used French. Times in the chat were shown in UTC instead of local time. The Log Chat menu entry was not highlighted on its own page.
- **Long meta-analyses**: the context budget assumed too many characters per token, and on the largest periods Ollama could have silently cut the start of the context. When everything does not fit, one talkative rule can also no longer take all the room: each rule keeps its most serious events first.
- **Events recognized in JSON logs**: a line carrying a JSON object was treated as a new event whenever one of its values changed (a domain name, an identifier, a measure). Grouping and the repeated-event guard then did nothing, and such a source could send one notification a minute. Sentinel now keeps the fields of the object and what tells events apart (type, status, level, messages), and ignores the rest. MAC addresses are ignored too, like IP addresses. Existing analyses and notes are recalculated once.
- **Meta-analysis failure**: when the AI failed, the error was saved as a report and its period was skipped for good. The failure is now shown, the period stays due, and a scheduled meta-analysis tries again at most once an hour.
- **Deleting a rule** now removes it from the meta-analyses that followed it, as its number can be reused. One left with no rule is disabled, not widened to all rules.
- **Autopilot description of a log**: it no longer judges events as normal or usual noise. The keyword arbiter then quoted that unproven judgment to ignore the same lines.
- **Syslog receiver**: every change in Configuration restarted it, even an unrelated one, which could lose packets. It now restarts only when its own settings change.
- **Notification record**: an alert was marked as notified even when sending had failed.
- **Quick profiles** in Configuration raised an error after applying the values.
- **Event recognition**: a date written `2026/11/03` (e.g. in nginx errors) made the same event look new every day, for the recurrence count and for the autopilot's repeated events.
- **Deleting a rule** now also deletes its analyses, resolution history and learning sessions. Its number can be reused: a rule created afterwards could inherit them, and a pending autopilot session prevented its setup, then applied the deleted rule's source to it.
- **Changing a rule's source**: a learning session still running on the old source is stopped, and a rule on autopilot starts learning again from the new source. A rule on autopilot whose setup could not start now retries by itself.
- **Autopilot**: an event repeating all day (e.g. Docker Desktop's `event streamer: error: EOF`, every 10 seconds) no longer sends an analysis to the AI every 30 seconds nor keeps the rule in alert: once its last 3 analyses are rated `info`, it is only analyzed again every 6 hours.
- **Dashboard**: the automatic refresh no longer closes the analysis being read, nor reloads the list from the start. The list is usable on a phone and with the keyboard (an analysis card opens with a real button, on every page).
- Markdown rendering no longer depends on a CDN: without internet access the analyses list stopped displaying altogether. The library is now served by the application, with its version pinned.
- A new rule that has not received any line yet is no longer shown as a *silent source* right away; and while the autopilot sets up a rule, the silence delay is not known yet, so no *silent source* warning is shown or sent.
- **Log history**: the learning and the autopilot read every rotated file of the period, not only `.1` (e.g. Docker Desktop's `com.docker.backend.exe.log.20261102-170332.916`; compressed archives are skipped).
- **Syslog**: UniFi devices that send lines without a date (e.g. the USW Flex 2.5G switch) were listed under the host `unknown`.
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
