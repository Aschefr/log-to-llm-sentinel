# Sign-in to the interface

Sentinel's interface has no sign-in by default: anyone who can reach it on the network can change settings, delete rules and analyses, and use the chat with its diagnostic tools. If other people or devices share that network, turn sign-in on in **Configuration › Access**.

Sign-in is optional and off by default. An existing install keeps working as before until you turn it on.

## What it protects

Once on, every page and every API call needs a session, including the chat, file browsing, backups and FastAPI's `/docs`. A page opened without a session goes to the sign-in page, then comes back to the page you asked for.

These stay open:

* the sign-in page and its calls, the static files (scripts, styles, translations) and the list of languages;
* **receiving logs by webhook**: `POST /api/webhook/logs/<token>`, including the Loki and OpenTelemetry paths. The machines that send logs have no session, so the webhook's secret token is what protects these URLs.

When sign-in is on, a webhook URL with the rule's **number** instead of its token (`/api/webhook/logs/3`) is refused with `webhook_token_required`. A number can be guessed. The URL shown in the rule form always uses the token.

Reading a webhook's received lines (`/api/webhook/tail/...`) needs a session like everything else.

## Sign-in methods

* **Passkeys**: kept in Bitwarden, the browser's password manager, Windows Hello or your phone. You can save several, each with a name, and remove them one by one.
* **Backup password**: at least 12 characters, stored as a scrypt hash. It works everywhere, including over plain HTTP.

You turn sign-in on after adding at least one of them. The browser that turns it on stays signed in, so it cannot lock you out. While sign-in is on, the last remaining method cannot be removed.

Turning sign-in off keeps your passkeys and password for next time and closes every session.

## Passkeys need HTTPS and a name

Browsers allow passkeys only in a secure context, and never on an IP address:

| How you open Sentinel | Passkey | Password |
|---|---|---|
| `http://192.168.1.10:10911` (IP over HTTP) | No | Yes |
| `https://192.168.1.10` (IP over HTTPS) | No: browsers refuse IP addresses | Yes |
| `http://sentinel.home.lan:10911` (name over HTTP) | No: HTTPS needed | Yes |
| `https://sentinel.home.lan` (name over HTTPS) | Yes | Yes |
| `http://localhost:10911` (on Sentinel's own machine) | Yes | Yes |

A passkey is tied to the name it was created on. A key created on `localhost` does not work on `sentinel.home.lan`, so create it on the address you normally use. The Access tab says when the current address cannot use passkeys. On the sign-in page, the password is then the only option.

Some password managers, Bitwarden among them, may also refuse `localhost`. The browser's own manager or Windows Hello then takes over.

### HTTPS with a reverse proxy

To use passkeys from other machines, put Sentinel behind a reverse proxy that serves HTTPS under a name, with a certificate your browsers trust. Two examples follow.

Caddy, with a name your local DNS resolves to the proxy:

```
sentinel.home.lan {
    tls internal
    reverse_proxy 192.168.1.10:10911
}
```

`tls internal` uses Caddy's own authority: install its root certificate on your devices, or use a real domain and let Caddy get a public certificate.

Nginx Proxy Manager: add a *Proxy Host* for `sentinel.home.lan` pointing to Sentinel's address and port 10911, and pick or request a certificate in the *SSL* tab.

Sentinel accepts the proxy's `X-Forwarded-Host` and `X-Forwarded-Proto` headers. The session cookie is marked `Secure` when the browser uses HTTPS. Webhook senders can keep posting to Sentinel's own address, or go through the proxy.

## Sessions

A session is a random token in an `HttpOnly`, `SameSite=Lax` cookie. Only its hash is stored. It stays valid for 30 days after its last use. **Sign out other sessions** closes every session but the one you are using. The button at the right of the header signs you out.

Requests that change something must come from Sentinel's own pages. A request sent by another site, or by another subdomain of yours, is refused with `cross_site_refused`, even if your browser holds a session.

## Too many attempts

After 5 failed sign-ins from one address, that address must wait 1 minute, then 2, 4… up to 15 minutes. Across all addresses, more than 30 failures in a minute make everyone wait until the minute is over. A successful sign-in resets the count for its address. Each failure is written to Sentinel's log with the address it came from.

Behind a reverse proxy, every attempt comes from the proxy's address, so the per-address limit applies to everyone at once.

## Lost every key and the password

Create an empty file named `reset-auth` in Sentinel's data folder (`./data/reset-auth` next to the compose file), then restart the container:

```
touch data/reset-auth
docker compose restart sentinel
```

At startup, Sentinel turns sign-in off and erases its passkeys, password and sessions. It then deletes the file and writes what it did to its log. Anyone with access to the data folder already has the database, so this gives nothing more away.

## Backups

Sign-in settings are not part of the configuration export. Restoring a backup never turns sign-in on or off, and never brings back an old password.
