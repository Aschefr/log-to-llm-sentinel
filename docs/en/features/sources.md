# Log sources

A rule reads one source. You choose it in the **Source** section of the rule form, which also shows a short connection guide for each one.

| Source | Who connects | Best for |
| --- | --- | --- |
| Local file | Sentinel reads a mounted file | Log files on the same machine |
| Webhook | The sender calls Sentinel | Scripts, and log shippers |
| Syslog | The sender calls Sentinel (UDP 10514) | Network gear, NAS, servers |
| Docker | Sentinel connects to a read-only proxy | Containers, with nothing to mount |
| Home Assistant | Sentinel connects with a token | Home Assistant warnings and errors |
| MQTT | Sentinel connects to the server | Zigbee2MQTT, Tasmota, ESPHome |

When Sentinel connects by itself, it reconnects on its own after any outage. If a source cannot be read, the reason appears in the rule list, on the rule page and on the dashboard, without waiting for the silence alert.

## Docker

Sentinel follows a container's logs through the read-only Docker proxy used by [diagnostics](diagnostics.md). The Docker socket is never mounted in Sentinel.

Reading starts when the rule is saved, and resumes where it stopped after a restart of Sentinel or of the container. A stopped container shows as *waiting*, not as a failure. If the container is removed and recreated with the same name, Sentinel follows the new one.

Use one rule per container.

## Home Assistant

1. In Home Assistant, open your profile, **Security** tab, **Long-lived access tokens**, **Create token**. The account must be an administrator, because the log is reserved to administrators.
2. In the rule form, choose **Home Assistant**, enter the address and the token, then click **Test**.

There is nothing to install in Home Assistant, and no change to `configuration.yaml`. Sentinel reads the system log every 10 seconds: warnings and errors, with their stack traces. Home Assistant does not publish other levels through this channel.

The lines have the same form as the webhook automation from the README, so keywords and notes keep working if you switch a rule from the webhook to this source. You can then remove the automation.

## MQTT

Enter the server address, and credentials if it asks for them. **Test** connects and listens for 3 seconds, then shows the active topics: click one to use it.

* `+` replaces one level and `#` replaces everything after it: `zigbee2mqtt/#`, `tele/+/LWT`.
* Each line received starts with its topic.
* Retained messages are ignored: they are old states, not events.
* Avoid `#` alone, which is often very chatty.

Encrypted connections check the server certificate against the system's authorities: a self-signed certificate is refused.

## Log shippers

A webhook rule also accepts the formats of log shippers, at two addresses built from its URL:

* **Loki push API**: `<webhook URL>/loki/api/v1/push`, in JSON or Protobuf with Snappy. Used by Fluent Bit, Grafana Alloy, Promtail and Vector.
* **OpenTelemetry logs (OTLP/HTTP)**: `<webhook URL>/v1/logs`, in JSON or Protobuf, with or without gzip. Used by the OpenTelemetry Collector and by applications that speak OTLP.

The rule form gives ready-made configurations under **Send from a log shipper**, including **Windows event logs** through Fluent Bit.

A line is prefixed with the name of its source when the sender gives one (`container`, `service_name`, `job`, `host`…). The time shown is the time of reception, not the time carried by the message.

## Connections

Home Assistant and MQTT connections are in **Configuration**, **Sources** tab, and you can also create one from the rule form. A connection serves every rule that needs it, and cannot be deleted while a rule uses it. A saved token or password is never displayed again: leave the field empty to keep it.

Docker connections are in **Configuration**, **Diagnostic** tab.

Tokens and passwords are stored in Sentinel's database, like its other settings, and are not included in the configuration export. After importing a configuration on another instance, create the connections again.
