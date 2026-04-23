document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    setupForm();
});

async function loadConfig() {
    try {
        const config = await apiFetch('/api/config');
        document.getElementById('smtp-host').value = config.smtp_host || '';
        document.getElementById('smtp-port').value = config.smtp_port || 587;
        document.getElementById('smtp-user').value = config.smtp_user || '';
        document.getElementById('smtp-password').value = '';
        document.getElementById('smtp-tls').checked = config.smtp_tls !== false;
        document.getElementById('ollama-url').value = config.ollama_url || 'http://host.docker.internal:11434';
        document.getElementById('ollama-model').value = config.ollama_model || 'llama3';
        document.getElementById('system-prompt').value = config.system_prompt || '';
        document.getElementById('notification-method').value = config.notification_method || 'smtp';
        document.getElementById('apprise-url').value = config.apprise_url || '';
    } catch (error) {
        console.error('Erreur chargement config:', error);
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

async function saveConfig(messageEl) {
    const data = {
        smtp_host: document.getElementById('smtp-host').value,
        smtp_port: parseInt(document.getElementById('smtp-port').value) || 587,
        smtp_user: document.getElementById('smtp-user').value,
        smtp_password: document.getElementById('smtp-password').value,
        smtp_tls: document.getElementById('smtp-tls').checked,
        ollama_url: document.getElementById('ollama-url').value,
        ollama_model: document.getElementById('ollama-model').value,
        system_prompt: document.getElementById('system-prompt').value,
        notification_method: document.getElementById('notification-method').value,
        apprise_url: document.getElementById('apprise-url').value,
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
