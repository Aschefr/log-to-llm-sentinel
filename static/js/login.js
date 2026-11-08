/* Page de connexion : clé d'accès d'abord, mot de passe en secours. */
(function () {
    'use strict';

    function tr(key, fallback, vars) {
        let text = window.t ? window.t(key) : key;
        if (!text || text === key) text = fallback;
        Object.entries(vars || {}).forEach(([k, v]) => { text = text.replace(`{${k}}`, v); });
        return text;
    }

    function result(ok, text) {
        const el = document.getElementById('login-result');
        el.textContent = text;
        el.classList.remove('rf-ok', 'rf-ko');
        if (ok !== null) el.classList.add(ok ? 'rf-ok' : 'rf-ko');
    }

    function errorText(body) {
        const code = body && body.detail;
        if (code === 'too_many_attempts') {
            const minutes = Math.max(1, Math.ceil((body.retry_after || 60) / 60));
            return tr('auth.too_many', 'Trop d’essais : réessaie dans {n} min.', { n: minutes });
        }
        if (code === 'login_failed') return tr('auth.login_failed', 'Connexion refusée.');
        const key = 'api_errors.' + code;
        const text = window.t ? window.t(key) : key;
        return text && text !== key ? text : tr('auth.login_error', 'La connexion a échoué.');
    }

    async function post(url, body) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw Object.assign(new Error('auth'), { body: data });
        return data;
    }

    function done() {
        result(true, tr('auth.login_ok', 'Connecté.'));
        location.replace(window.SENTINEL_NEXT || '/');
    }

    async function withPasskey() {
        const btn = document.getElementById('login-passkey-btn');
        btn.disabled = true;
        result(null, '');
        try {
            const { challenge_id, options } = await post('/api/auth/login/options');
            const credential = await window.sentinelPasskey.get(options);
            await post('/api/auth/login/passkey', { challenge_id, credential });
            done();
        } catch (e) {
            if (e.body) result(false, errorText(e.body));
            else if (e.name === 'NotAllowedError') result(false, tr('auth.passkey_cancelled', 'Clé d’accès annulée ou refusée.'));
            else result(false, tr('auth.login_error', 'La connexion a échoué.') + (e.message ? ` (${e.message})` : ''));
        } finally {
            btn.disabled = false;
        }
    }

    async function withPassword(event) {
        event.preventDefault();
        const input = document.getElementById('login-password-input');
        const btn = document.getElementById('login-password-btn');
        if (!input.value) { input.focus(); return; }
        btn.disabled = true;
        result(null, '');
        try {
            await post('/api/auth/login/password', { password: input.value });
            done();
        } catch (e) {
            result(false, e.body ? errorText(e.body) : tr('auth.login_error', 'La connexion a échoué.'));
            input.select();
        } finally {
            btn.disabled = false;
        }
    }

    function unavailableText(reason) {
        if (reason === 'ip') return tr('auth.passkey_ip', 'Les clés d’accès demandent un nom (pas une adresse IP) et HTTPS : utilise le mot de passe, ou ouvre Sentinel par son nom en HTTPS.');
        if (reason === 'https') return tr('auth.passkey_https', 'Les clés d’accès demandent HTTPS (ou localhost) : utilise le mot de passe, ou ouvre Sentinel en HTTPS.');
        return tr('auth.passkey_browser', 'Ce navigateur ne gère pas les clés d’accès.');
    }

    async function init() {
        let methods = { password: true, passkey: false };
        try {
            methods = await (await fetch('/api/auth/methods')).json();
        } catch (_) { /* on propose au moins le mot de passe */ }
        const reason = window.sentinelPasskey.unavailableReason();
        const passkeyBlock = document.getElementById('login-passkey');
        const passwordForm = document.getElementById('login-password');
        passkeyBlock.classList.toggle('hidden', !methods.passkey);
        passwordForm.classList.toggle('hidden', !methods.password);
        if (methods.passkey && reason) {
            // Impossible ici : le mot de passe devient le seul bouton, la raison reste écrite
            document.getElementById('login-passkey-btn').classList.add('hidden');
            const hint = document.getElementById('login-passkey-unavailable');
            hint.textContent = unavailableText(reason);
            hint.classList.remove('hidden');
        }
        document.getElementById('login-passkey-btn').addEventListener('click', withPasskey);
        passwordForm.addEventListener('submit', withPassword);
        if (methods.password && (!methods.passkey || reason)) document.getElementById('login-password-input').focus();
        else if (methods.passkey && !reason) document.getElementById('login-passkey-btn').focus();
        window.i18n?.onLanguageChange(() => {
            if (methods.passkey && reason) document.getElementById('login-passkey-unavailable').textContent = unavailableText(reason);
        });
    }

    document.addEventListener('DOMContentLoaded', init);
})();
