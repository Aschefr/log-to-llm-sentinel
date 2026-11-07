# When the AI is not answering

Sentinel needs the local AI to judge an alert. If Ollama is stopped, unreachable, or the model is missing, Sentinel still records the alert, but it does not pretend to know how serious it is.

## What you see

* The alert card shows **Waiting for the AI** instead of a severity.
* Its analysis says what went wrong, for example `[Erreur Ollama] All connection attempts failed`.
* Nothing is notified yet: the severity is unknown.

## What Sentinel does

* **Every minute**, it checks whether the AI answers again.
* **As soon as it does**, the waiting alerts are analysed, a few at a time so the AI is not flooded. The analysis keeps the date of the original detection.
* **Repeats of the same event** reuse the verdict of the first one, without another call to the AI.
* **An alert that deserves a notification** under its rule's threshold is notified then, with the mention *Analysed late* and the time it came in.
* **After 24 hours** of waiting, an alert is no longer retried. *Retry* on its card still works.

## Notices

* If alerts have been waiting for **more than 10 minutes**, one notice says the AI is not answering, how many alerts wait and on which rules. It is sent once per outage.
* When every waiting alert has been analysed, a second notice says the AI is answering again.
* Sentinel is blind during an outage, so both notices go out in **quiet mode** too, like a critical alert. Alerts analysed late still follow quiet mode: only critical ones are notified.

## Good to know

* If Ollama runs on a server that Sentinel also watches, an outage of that server is exactly when the AI cannot answer. Its alerts are analysed when the server is back, and notified then if they deserve it.
* The dashboard shows the state of Ollama at any time, in its *Ollama* panel.
