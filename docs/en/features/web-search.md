# Web Search and CVE Sheets

Logs do not say everything. When an alert cites a vulnerability, Sentinel can show its official sheet, and the AI of the chat can search the web for what is still missing. Both are **off by default** and set up in *Configuration > Diagnostics*.

| | CVE sheets | Web search |
|---|---|---|
| Used by | Analysis cards, the chat and investigations | The chat and investigations only |
| Source | NVD, the official database, with CISA's KEV catalog | Your own SearXNG instance |
| What leaves your network | The CVE number | A short query, filtered |
| Reliability | High: official, structured data | Lower: short excerpts of pages written by anyone |

Automatic alert analysis never uses them, and neither ever changes a severity: a log line can hold text chosen by an attacker, including a CVE number.

---

## 1. CVE sheets

Tick **Show CVE sheets**. An analysis citing a CVE then shows, when opened:

* whether it is **actively exploited** (in CISA's KEV catalog, and since when);
* the **affected versions**, so you can compare with yours;
* the CVSS score and the description, with a link to the source.

A medium score does not mean low risk: a flaw scored 5.3 can be actively exploited and lead to code execution as part of a chain. Look at exploitation first.

Sheets are kept for 7 days. If NVD cannot be reached, the older sheet is shown, or the card says so.

---

## 2. Web search

The AI gets a `web_search` tool, with the same per-rule access as the other [diagnostic connections](diagnostics.md).

### With Docker Compose
Sentinel's `docker-compose.yml` already holds the service, with no published port:

```bash
docker compose --profile web up -d
```

Then enter `http://searxng:8080` in *Configuration > Diagnostics > Web search* and click **Test and save**.

### On Unraid
1. In the **Apps** tab, install **SearXNG**.
2. In its `settings.yml`, usually under `/mnt/user/appdata/searxng/`, add `json` under `search: formats:`, next to `html`. Without it, SearXNG answers 403.
3. Restart SearXNG.
4. Enter its address in Sentinel: `http://SERVER-IP:8080`, or `http://searxng:8080` if it is on the same Docker network.

### Search engines
Many engines block home addresses with a CAPTCHA or a rate limit. **Test and save** shows which ones really answer from your connection. When engines fail and nothing comes back, the AI is told that nothing can be concluded, which is different from "no results".

### What protects you
* **Query filter**: a query is refused if it holds an IP or MAC address, an e-mail, a machine path, a token, an internal domain or the name of one of your machines, so a log line cannot use the AI to send them out.
* **Results are data**: the AI gets short excerpts, presented as text from strangers. It is told not to obey them and that they never justify a proposed action on their own.
* **What the AI looked at**: each query and its results stay readable under the answer.

This lowers the risk without removing it, and a query is still sent to public engines.

### Limits
Excerpts are often not enough to diagnose an unusual behaviour: on a measured question about a service restarting by design, the AI concluded differently from one run to the next. Treat it as a lead, and check the sources it cites.

---

## 3. Questions on a security alert

In a conversation about an alert citing a CVE, the AI is asked to answer explicitly, separating facts from deductions:

1. **Am I vulnerable?** The version found on the machine against the fixed one, or "unknown" with how to check.
2. **Did it get through?** The source address searched in your other logs. Blocked at the network does not prove there was no impact, and no trace is not proof.
3. **Is it actively exploited?**

It will not propose closing such an incident, since closing fixes nothing and only you know whether the machine is up to date, nor an exclusion that would hide the next attempts. You can still do both yourself.

---

## 4. Blocked attempts

For a source in CEF format (UniFi and others), the rule's **Overview** tab groups blocked attempts by target port, without any AI: flows, number of sources and most seen signature. One alert says little; many sources aiming at the same port show what is exposed.

Analysis cards of such lines also show the fields of the line (source, destination, port, action, signature), read directly from it.
