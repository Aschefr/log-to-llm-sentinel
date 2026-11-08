/* Configuration › Accès : connexion facultative à l'interface (clés d'accès, mot de passe de secours, sessions). */
let authState = null;

function authFormatDate(iso) {
    if (!iso) return '';
    const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
    return d.toLocaleDateString(document.documentElement.lang || 'fr', { day: 'numeric', month: 'short', year: 'numeric' });
}

function authPasskeyReason() {
    const reason = window.sentinelPasskey.unavailableReason();
    if (reason === 'ip') return cfgT('auth.context_ip', 'Ouvert par une adresse IP ({host}) : les navigateurs refusent les clés d’accès sur une adresse IP, même en HTTPS. Utilise le mot de passe, ou ouvre Sentinel par un nom en HTTPS derrière un reverse proxy (voir la documentation).', { host: location.hostname });
    if (reason === 'https') return cfgT('auth.context_https', 'Ouvert en HTTP ({host}) : les clés d’accès demandent HTTPS, ou localhost sur la machine de Sentinel. Utilise le mot de passe, ou un reverse proxy HTTPS (voir la documentation).', { host: location.hostname });
    if (reason === 'browser') return cfgT('auth.context_browser', 'Ce navigateur ne gère pas les clés d’accès.');
    return null;
}

function authRenderStatus() {
    const s = authState;
    if (!s) return;
    const keys = s.passkeys.length;
    const parts = [];
    if (keys) parts.push(cfgT(keys > 1 ? 'auth.status_keys' : 'auth.status_key', '{n} clé(s)', { n: keys }));
    if (s.password_set) parts.push(cfgT('auth.status_password', 'mot de passe'));
    if (s.enabled) setStatusItem('access', 'ok', [cfgT('auth.status_on', 'Active'), ...parts].join(' · '));
    else setStatusItem('access', 'idle', cfgT('auth.status_off', 'Désactivée'));
}

function authRender() {
    const s = authState;
    if (!s) return;
    const methods = s.passkeys.length + (s.password_set ? 1 : 0);
    authRenderStatus();

    const hint = document.getElementById('auth-state-hint');
    if (s.enabled) hint.textContent = cfgT('auth.state_on', 'Active : il faut une clé d’accès ou le mot de passe pour ouvrir Sentinel.');
    else if (methods) hint.textContent = cfgT('auth.state_ready', 'Désactivée. Un moyen de connexion est prêt : tu peux l’activer.');
    else hint.textContent = cfgT('auth.state_off', 'Désactivée. Ajoute d’abord une clé d’accès ou un mot de passe ci-dessous.');
    const enableBtn = document.getElementById('auth-enable-btn');
    enableBtn.classList.toggle('hidden', s.enabled);
    enableBtn.disabled = methods === 0;
    document.getElementById('auth-disable-btn').classList.toggle('hidden', !s.enabled);

    const reason = authPasskeyReason();
    const context = document.getElementById('auth-passkey-context');
    context.textContent = reason || '';
    context.classList.toggle('hidden', !reason);
    document.getElementById('auth-passkey-add').disabled = Boolean(reason);
    document.getElementById('auth-passkey-name').disabled = Boolean(reason);

    const lastMethod = s.enabled && methods <= 1;
    const list = document.getElementById('auth-passkeys');
    list.innerHTML = s.passkeys.length ? s.passkeys.map(p => {
        const facts = [p.rp_id, cfgT('auth.passkey_created', 'créée le {date}', { date: authFormatDate(p.created_at) })];
        if (p.last_used_at) facts.push(cfgT('auth.passkey_used', 'utilisée le {date}', { date: authFormatDate(p.last_used_at) }));
        const here = p.rp_id === location.hostname;
        return `<li class="auth-item">
            <span class="auth-item-text"><strong>${escapeHtml(p.name)}</strong>${here ? '' : ` <span class="auth-other">${escapeHtml(cfgT('auth.passkey_other_host', 'autre adresse'))}</span>`}
                <span class="form-hint">${escapeHtml(facts.join(' · '))}</span></span>
            <button type="button" class="btn btn-secondary btn-sm" data-remove-passkey="${p.id}" ${lastMethod ? 'disabled' : ''}
                title="${escapeHtml(lastMethod ? cfgT('auth.last_method', 'Dernier moyen de connexion : désactive d’abord la connexion, ou ajoute-en un autre.') : '')}">${escapeHtml(cfgT('auth.remove', 'Supprimer'))}</button>
        </li>`;
    }).join('') : `<li class="form-hint">${escapeHtml(cfgT('auth.passkeys_empty', 'Aucune clé d’accès.'))}</li>`;

    document.getElementById('auth-password-state').textContent = s.password_set
        ? cfgT('auth.password_set', 'Un mot de passe est enregistré : en saisir un nouveau le remplace.')
        : cfgT('auth.password_unset', 'Aucun mot de passe.');
    const remove = document.getElementById('auth-password-remove');
    remove.classList.toggle('hidden', !s.password_set);
    remove.disabled = lastMethod;
    remove.title = lastMethod ? cfgT('auth.last_method', 'Dernier moyen de connexion : désactive d’abord la connexion, ou ajoute-en un autre.') : '';

    document.getElementById('auth-sessions-state').textContent = s.enabled
        ? cfgT('auth.sessions_count', '{n} session(s) ouverte(s), chacune valable 30 jours après sa dernière utilisation.', { n: s.sessions })
        : cfgT('auth.sessions_off', 'Aucune session tant que la connexion est désactivée.');
    document.getElementById('auth-close-others').classList.toggle('hidden', !s.enabled);
    document.getElementById('auth-logout').classList.toggle('hidden', !s.current_session);
}

async function authLoad() {
    try {
        authState = await apiFetch('/api/auth/status');
        authRender();
    } catch (e) {
        setStatusItem('access', 'idle', cfgT('config.status_unknown', 'État inconnu'));
    }
}

async function authAction(resultId, fn, okText) {
    cfgResult(resultId, null, '');
    try {
        await fn();
        await authLoad();
        if (okText) cfgResult(resultId, true, okText);
        return true;
    } catch (e) {
        cfgResult(resultId, false, e.message);
        return false;
    }
}

async function authAddPasskey() {
    const nameInput = document.getElementById('auth-passkey-name');
    const btn = document.getElementById('auth-passkey-add');
    btn.disabled = true;
    await authAction('auth-passkey-result', async () => {
        const { challenge_id, options } = await apiFetch('/api/auth/passkeys/options', { method: 'POST' });
        let credential;
        try {
            credential = await window.sentinelPasskey.create(options);
        } catch (e) {
            if (e.name === 'NotAllowedError') throw new Error(cfgT('auth.passkey_cancelled', 'Clé d’accès annulée ou refusée.'));
            if (e.name === 'InvalidStateError') throw new Error(cfgT('api_errors.passkey_exists', 'Cette clé d’accès est déjà enregistrée.'));
            throw e;
        }
        await apiFetch('/api/auth/passkeys', { method: 'POST', body: { challenge_id, name: nameInput.value, credential } });
        nameInput.value = '';
    }, cfgT('auth.passkey_added', 'Clé d’accès enregistrée.'));
    btn.disabled = Boolean(authPasskeyReason());
}

function setupAuthConfig() {
    if (!document.getElementById('tab-access')) return;
    document.getElementById('auth-enable-btn').addEventListener('click', () => authAction('auth-state-result',
        () => apiFetch('/api/auth/enable', { method: 'POST' }),
        cfgT('auth.enabled_ok', 'Connexion activée. Ce navigateur reste connecté ; les autres devront se connecter.')));
    document.getElementById('auth-disable-btn').addEventListener('click', () => {
        if (!confirm(cfgT('auth.disable_confirm', 'Désactiver la connexion ? Toute personne qui atteint Sentinel sur le réseau pourra de nouveau tout faire. Les clés et le mot de passe restent enregistrés.'))) return;
        authAction('auth-state-result', () => apiFetch('/api/auth/disable', { method: 'POST' }), cfgT('auth.disabled_ok', 'Connexion désactivée.'));
    });
    document.getElementById('auth-passkey-add').addEventListener('click', authAddPasskey);
    document.getElementById('auth-passkeys').addEventListener('click', event => {
        const btn = event.target.closest('[data-remove-passkey]');
        if (!btn) return;
        const key = authState.passkeys.find(p => String(p.id) === btn.dataset.removePasskey);
        if (!confirm(cfgT('auth.passkey_remove_confirm', 'Supprimer la clé d’accès « {name} » ? Elle ne pourra plus ouvrir Sentinel ; pense à l’effacer aussi de ton gestionnaire.', { name: key ? key.name : '' }))) return;
        authAction('auth-passkey-result', () => apiFetch(`/api/auth/passkeys/${btn.dataset.removePasskey}`, { method: 'DELETE' }),
            cfgT('auth.passkey_removed', 'Clé d’accès supprimée.'));
    });
    document.getElementById('auth-password-form').addEventListener('submit', event => {
        event.preventDefault();
        const pwd = document.getElementById('auth-password-new');
        const confirmInput = document.getElementById('auth-password-confirm');
        if (pwd.value.length < 12) { cfgResult('auth-password-result', false, cfgT('api_errors.auth_password_short', 'Le mot de passe doit faire au moins 12 caractères.')); pwd.focus(); return; }
        if (pwd.value !== confirmInput.value) { cfgResult('auth-password-result', false, cfgT('auth.password_mismatch', 'Les deux saisies diffèrent.')); confirmInput.focus(); return; }
        authAction('auth-password-result', async () => {
            await apiFetch('/api/auth/password', { method: 'POST', body: { password: pwd.value } });
            pwd.value = '';
            confirmInput.value = '';
        }, cfgT('auth.password_saved', 'Mot de passe enregistré.'));
    });
    document.getElementById('auth-password-remove').addEventListener('click', () => {
        if (!confirm(cfgT('auth.password_remove_confirm', 'Supprimer le mot de passe ? Seules les clés d’accès permettront de se connecter.'))) return;
        authAction('auth-password-result', () => apiFetch('/api/auth/password', { method: 'DELETE' }), cfgT('auth.password_removed', 'Mot de passe supprimé.'));
    });
    document.getElementById('auth-close-others').addEventListener('click', () => authAction('auth-sessions-result',
        () => apiFetch('/api/auth/sessions/close-others', { method: 'POST' }), cfgT('auth.closed_others', 'Les autres sessions sont fermées.')));
    document.getElementById('auth-logout').addEventListener('click', () => sentinelLogout());
    authLoad();
    window.i18n?.onLanguageChange(authRender);
}

document.addEventListener('DOMContentLoaded', setupAuthConfig);
