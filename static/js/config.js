document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    setupForm();
    setupTests();
    setupOllamaModelSelect();
});

async function loadConfig() {
    try {
        const config = await apiFetch('/api/config');
        document.getElementById('smtp-host').value = config.smtp_host || '';
        document.getElementById('smtp-port').value = config.smtp_port || 587;
        document.getElementById('smtp-user').value = config.smtp_user || '';
        document.getElementById('smtp-password').value = '';
        document.getElementById('smtp-recipient').value = config.smtp_recipient || '';
        // ssl_mode: priorité au nouveau champ, fallback legacy
        const sslMode = config.smtp_ssl_mode || (config.smtp_tls ? 'starttls' : 'none');
        document.getElementById('smtp-ssl-mode').value = sslMode;
        document.getElementById('ollama-url').value = config.ollama_url || 'http://host.docker.internal:11434';
        // Model select is populated async; keep desired value to apply later.
        window.__desiredOllamaModel = config.ollama_model || 'llama3';
        document.getElementById('system-prompt').value = config.system_prompt || '';
        document.getElementById('notification-method').value = config.notification_method || 'smtp';
        document.getElementById('apprise-url').value = config.apprise_url || '';
        const debugEl = document.getElementById('debug-mode');
        if (debugEl) debugEl.checked = config.debug_mode === true;
    } catch (error) {
        console.error('Erreur chargement config:', error);
    }
}

async function setupOllamaModelSelect() {
    const select = document.getElementById('ollama-model');
    const customGroup = document.getElementById('ollama-model-custom-group');
    const customInput = document.getElementById('ollama-model-custom');
    if (!select || !customGroup || !customInput) return;

    function setCustomVisible(on) {
        customGroup.classList.toggle('hidden', !on);
    }

    select.addEventListener('change', () => {
        const v = select.value;
        if (v === '__custom__') {
            setCustomVisible(true);
            customInput.focus();
        } else {
            setCustomVisible(false);
        }
    });

    // Populate list
    try {
        const res = await apiFetch('/api/config/ollama/models');
        const models = (res && res.models) ? res.models : [];

        const desired = window.__desiredOllamaModel || 'llama3';
        const hasDesired = models.includes(desired);

        const options = [];
        if (models.length === 0) {
            options.push(`<option value="__custom__">Autre…</option>`);
        } else {
            options.push(...models.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`));
            options.push(`<option value="__custom__">Autre…</option>`);
        }
        select.innerHTML = options.join('');

        if (hasDesired) {
            select.value = desired;
            setCustomVisible(false);
        } else {
            select.value = '__custom__';
            customInput.value = desired;
            setCustomVisible(true);
        }
    } catch (e) {
        // Fallback avec message d'erreur visible
        const errMsg = e && e.message ? e.message : 'Ollama injoignable';
        select.innerHTML = `<option value="__custom__">Autre… (${escapeHtml(errMsg)})</option>`;
        select.value = '__custom__';
        customInput.value = window.__desiredOllamaModel || 'llama3';
        setCustomVisible(true);
    }
}

function setupForm() {
    const form = document.getElementById('config-form');
    const messageEl = document.getElementById('config-message');

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        await saveConfig(messageEl);
    });
}

function setupTests() {
    const btnOllama = document.getElementById('test-ollama-btn');
    const btnSmtp = document.getElementById('test-smtp-btn');
    const btnApprise = document.getElementById('test-apprise-btn');

    const msgOllama = document.getElementById('ollama-test-message');
    const msgSmtp = document.getElementById('smtp-test-message');
    const msgApprise = document.getElementById('apprise-test-message');

    if (btnOllama && msgOllama) {
        btnOllama.addEventListener('click', async () => {
            await runTest('/api/config/test/ollama', msgOllama, btnOllama);
        });
    }
    if (btnSmtp && msgSmtp) {
        btnSmtp.addEventListener('click', async () => {
            await runTest('/api/config/test/smtp', msgSmtp, btnSmtp);
        });
    }
    if (btnApprise && msgApprise) {
        btnApprise.addEventListener('click', async () => {
            await runTest('/api/config/test/apprise', msgApprise, btnApprise);
        });
    }
}

async function runTest(url, messageEl, buttonEl) {
    const oldText = buttonEl.textContent;
    buttonEl.disabled = true;
    buttonEl.textContent = 'Test en cours...';
    try {
        const res = await apiFetch(url, { method: 'POST' });
        const detail = (res && res.detail) ? res.detail : 'OK';
        showMessage(messageEl, detail, 'success');
    } catch (error) {
        console.error('Erreur test:', error);
        showMessage(messageEl, 'Erreur: ' + (error.message || 'inconnue'), 'error');
    } finally {
        buttonEl.disabled = false;
        buttonEl.textContent = oldText;
    }
}

async function saveConfig(messageEl) {
    const modelSelect = document.getElementById('ollama-model');
    const modelCustom = document.getElementById('ollama-model-custom');
    const chosenModel = (modelSelect && modelSelect.value === '__custom__')
        ? (modelCustom ? modelCustom.value : '')
        : (modelSelect ? modelSelect.value : '');

    const data = {
        smtp_host: document.getElementById('smtp-host').value,
        smtp_port: parseInt(document.getElementById('smtp-port').value) || 587,
        smtp_user: document.getElementById('smtp-user').value,
        smtp_password: document.getElementById('smtp-password').value,
        smtp_recipient: document.getElementById('smtp-recipient').value,
        smtp_ssl_mode: document.getElementById('smtp-ssl-mode').value,
        smtp_tls: document.getElementById('smtp-ssl-mode').value === 'starttls',  // compat legacy
        ollama_url: document.getElementById('ollama-url').value,
        ollama_model: chosenModel,
        system_prompt: document.getElementById('system-prompt').value,
        notification_method: document.getElementById('notification-method').value,
        apprise_url: document.getElementById('apprise-url').value,
        debug_mode: document.getElementById('debug-mode') ? document.getElementById('debug-mode').checked : false,
    };

    try {
        await apiFetch('/api/config', {
            method: 'PUT',
            body: data,
        });
        showMessage(messageEl, 'Configuration sauvegardée avec succès', 'success');
    } catch (error) {
        console.error('Erreur sauvegarde config:', error);
        showMessage(messageEl, 'Erreur: ' + error.message, 'error');
    }
}
