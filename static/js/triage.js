// ─── Tri des non lues par l'IA ────────────────────────
// The AI proposes, the user validates, the last triage can be undone.
// Closing the dialog leaves a running triage alive: reopening picks it up.
// ──────────────────────────────────────────────────────

const TRIAGE = { job: null, timer: null, ruleId: null, unchecked: new Set() };

function trT(key, fallback, vars) {
    let s = window.t ? window.t(`triage.${key}`) : '';
    if (!s || s === `triage.${key}`) s = fallback;
    if (vars) Object.entries(vars).forEach(([k, v]) => { s = s.split(`{${k}}`).join(v); });
    return s;
}

function trCount(n, oneKey, oneText, manyKey, manyText) {
    return n === 1 ? trT(oneKey, oneText, { n }) : trT(manyKey, manyText, { n });
}

function trDialog() {
    let dialog = document.getElementById('triage-dialog');
    if (dialog) return dialog;
    dialog = document.createElement('dialog');
    dialog.className = 'triage';
    dialog.id = 'triage-dialog';
    dialog.setAttribute('aria-labelledby', 'triage-title');
    dialog.innerHTML = `<div class="triage-inner">
        <header class="triage-head">
            <h2 id="triage-title">${escapeHtml(trT('title', 'Trier mes non lues'))}</h2>
            <button type="button" class="theme-btn" id="triage-close" aria-label="${escapeHtml(trT('close', 'Fermer'))}" title="${escapeHtml(trT('close', 'Fermer'))}">
                <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M3 3l10 10M13 3L3 13"/></svg>
            </button>
        </header>
        <div class="triage-body" id="triage-body" aria-live="polite"></div>
        <footer class="triage-foot" id="triage-foot"></footer>
    </div>`;
    document.body.appendChild(dialog);
    dialog.querySelector('#triage-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => clearTimeout(TRIAGE.timer));
    return dialog;
}

async function openTriage(ruleId = null) {
    TRIAGE.ruleId = ruleId;
    TRIAGE.unchecked = new Set();
    trDialog().showModal();
    trRender();
    try {
        TRIAGE.job = await apiFetch(`/api/triage/start${ruleId ? `?rule_id=${ruleId}` : ''}`, { method: 'POST' });
    } catch (e) {
        TRIAGE.job = { status: 'failed', error: 'internal', total: 0, done: 0, proposals: [], kept: [] };
    }
    trRender();
    trPoll();
}

function trPoll() {
    clearTimeout(TRIAGE.timer);
    if (TRIAGE.job?.status !== 'running' || !trDialog().open) return;
    TRIAGE.timer = setTimeout(async () => {
        try {
            TRIAGE.job = await apiFetch(`/api/triage/jobs/${TRIAGE.job.id}`);
            trRender();
        } catch (e) {
            TRIAGE.job = { status: 'failed', error: 'expired', total: 0, done: 0, proposals: [], kept: [] };
            trRender();
        }
        trPoll();
    }, 1500);
}

/** The AI reads the events by batches and answers once per batch: what is read is filled, the batch being read moves.
 *  Built once, then updated in place, so the animation does not restart at each poll. */
function trRenderRunning(body, foot, job) {
    const total = job?.total || 0, done = job?.done || 0;
    if (body.dataset.running !== '1') {
        body.dataset.running = '1';
        body.innerHTML = `<p class="triage-lead" id="triage-run-text"></p>
            <div class="triage-bar" id="triage-bar" role="progressbar" aria-valuemin="0">
                <span class="triage-bar-done"></span><span class="triage-bar-current"></span>
            </div>
            <p class="form-hint" id="triage-run-time"></p>
            <p class="form-hint">${escapeHtml(trT('running_hint', 'Tu peux fermer cette fenêtre : le tri continue, et tu retrouveras le résultat en revenant ici.'))}</p>`;
        foot.innerHTML = `<button type="button" class="btn btn-secondary btn-sm" onclick="trCancel()">${escapeHtml(trT('stop', 'Arrêter le tri'))}</button>`;
    }
    foot.querySelector('button')?.classList.toggle('hidden', !job);
    const bar = document.getElementById('triage-bar');
    const to = total ? Math.min(total, done + (job.batch || total)) : 0;
    bar.querySelector('.triage-bar-done').style.width = total ? `${done / total * 100}%` : '0';
    const current = bar.querySelector('.triage-bar-current');
    // Le lot en cours reste visible même petit : une largeur minimale, sans sortir de la barre
    current.style.left = total ? `min(${done / total * 100}%, calc(100% - 2.5rem))` : '0';
    current.style.width = total ? `max(${(to - done) / total * 100}%, 2.5rem)` : '100%';
    const text = !total ? trT('running', 'Préparation du tri…')
        : to === done + 1 ? trT('running_one', 'L’IA lit l’événement {from} sur {total}…', { from: done + 1, total })
        : trT('running_batch', 'L’IA lit les événements {from} à {to} sur {total}…', { from: done + 1, to, total });
    document.getElementById('triage-run-text').textContent = text;
    bar.setAttribute('aria-valuemax', String(total || 1));
    bar.setAttribute('aria-valuenow', String(done));
    bar.setAttribute('aria-valuetext', text);
    // Temps écoulé, à l'heure du serveur : la réponse d'un lot prend souvent une à deux minutes
    const skew = typeof HEADER !== 'undefined' && HEADER.serverSkew ? HEADER.serverSkew : 0;
    const seconds = job?.at ? Math.max(0, Math.round((Date.now() + skew - Date.parse(job.at)) / 1000)) : 0;
    document.getElementById('triage-run-time').textContent = job?.at
        ? trT('running_since', 'Depuis {t}. Une réponse de l’IA prend souvent une à deux minutes.', { t: `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` }) : '';
}

function trKey(item) {
    return `${item.rule_id}|${item.key}`;
}

function trRender() {
    const body = document.getElementById('triage-body'), foot = document.getElementById('triage-foot');
    const job = TRIAGE.job;
    if (!job || job.status === 'running') {
        trRenderRunning(body, foot, job);
        return;
    }
    body.dataset.running = '';
    if (job.status === 'failed' || job.status === 'cancelled') {
        const text = job.status === 'cancelled' ? trT('cancelled', 'Tri arrêté. Rien n’a changé.')
            : job.error === 'ai_unavailable' ? trT('failed_ai', 'L’IA n’a pas répondu. Rien n’a changé : vérifie Ollama, puis relance le tri.')
            : job.error === 'expired' ? trT('failed_expired', 'Ce tri n’est plus disponible. Relance-le.')
            : trT('failed', 'Le tri a échoué. Rien n’a changé.');
        body.innerHTML = `<p class="triage-lead">${escapeHtml(text)}</p>`;
        foot.innerHTML = `<button type="button" class="btn btn-primary btn-sm" onclick="openTriage(TRIAGE.ruleId)">${escapeHtml(trT('retry', 'Relancer le tri'))}</button>`;
        return;
    }
    trRenderResult(body, foot, job);
}

function trRenderResult(body, foot, job) {
    const proposals = job.proposals, kept = job.kept;
    if (!job.unread) {
        body.innerHTML = `<p class="triage-lead">${escapeHtml(trT('nothing', 'Rien à trier : tu n’as aucun événement non lu.'))}</p>`;
        foot.innerHTML = `<button type="button" class="btn btn-secondary btn-sm" onclick="document.getElementById('triage-dialog').close()">${escapeHtml(trT('close', 'Fermer'))}</button>`;
        return;
    }
    // Without proposal, say whether the AI looked at anything: the guards may have kept everything before asking it
    const lead = proposals.length
        ? trCount(proposals.length, 'lead_one', 'L’IA propose de marquer 1 événement comme lu. Décoche ce que tu veux garder.', 'lead_many', 'L’IA propose de marquer {n} événements comme lus. Décoche ce que tu veux garder.')
        : job.total
            ? trCount(job.total, 'lead_none_one', 'L’IA a regardé 1 événement et préfère que tu le lises.', 'lead_none_many', 'L’IA a regardé {n} événements et n’en propose aucun : elle préfère que tu les lises.')
            : trCount(job.unread, 'lead_none_guarded_one', 'L’IA n’a rien eu à regarder : ton événement non lu est critique, il reste à lire d’office.', 'lead_none_guarded_many', 'L’IA n’a rien eu à regarder : tes {n} événements non lus sont critiques, ils restent à lire d’office.');
    const more = job.not_reviewed ? `<p class="form-hint">${escapeHtml(trT('not_reviewed', '{n} autres événements n’ont pas été regardés : relance le tri après celui-ci.', { n: job.not_reviewed }))}</p>` : '';
    body.innerHTML = `<p class="triage-lead">${escapeHtml(lead)}</p>
        ${proposals.length ? `<ul class="triage-list">${proposals.map(trProposalHtml).join('')}</ul>` : ''}
        ${more}
        ${kept.length ? `<details class="triage-kept" ${proposals.length ? '' : 'open'}>
            <summary>${escapeHtml(trCount(kept.length, 'kept_one', '1 événement reste à lire', 'kept_many', '{n} événements restent à lire'))}</summary>
            <ul class="triage-list">${kept.map(trKeptHtml).join('')}</ul>
        </details>` : ''}
        <p class="form-hint triage-note">${escapeHtml(trT('note', 'Marquer comme lu ne ferme aucun incident et ne supprime rien. Une répétition plus grave le remettra en non lu.'))}</p>`;
    body.querySelectorAll('input[data-key]').forEach(box => box.addEventListener('change', () => {
        if (box.checked) TRIAGE.unchecked.delete(box.dataset.key);
        else TRIAGE.unchecked.add(box.dataset.key);
        trRenderFoot(foot, job);
    }));
    trRenderFoot(foot, job);
}

function trRenderFoot(foot, job) {
    const n = job.proposals.filter(p => !TRIAGE.unchecked.has(trKey(p))).length;
    const close = `<button type="button" class="btn btn-secondary btn-sm" onclick="document.getElementById('triage-dialog').close()">${escapeHtml(job.proposals.length ? trT('later', 'Pas maintenant') : trT('close', 'Fermer'))}</button>`;
    foot.innerHTML = close + (job.proposals.length ? `<button type="button" class="btn btn-primary btn-sm" id="triage-apply" ${n ? '' : 'disabled'} onclick="trApply()">${escapeHtml(trCount(n, 'apply_one', 'Marquer 1 événement comme lu', 'apply_many', 'Marquer {n} événements comme lus'))}</button>` : '');
}

function trMeta(item) {
    const when = new Date(item.last_at).toLocaleDateString([], { day: '2-digit', month: '2-digit' });
    // Récent : l'IA n'avait pas d'historique pour juger, la personne le sait en validant
    return [item.rule_name, trCount(item.count, 'seen_one', 'vu 1 fois', 'seen_many', 'vu {n} fois'), trT('last_seen', 'dernière fois le {d}', { d: when }),
        ...(item.recent ? [trT('recent', 'récent, sans historique')] : [])].join(' · ');
}

function trEventHtml(item) {
    return `<span class="triage-event">
        <span class="severity-badge ${escapeHtml(item.severity)}">${escapeHtml(item.severity)}</span>
        <a href="/rules/${item.rule_id}?analysis=${item.analysis_id}#analyses" class="triage-summary">${escapeHtml(item.summary)}</a>
    </span>
    <span class="triage-meta">${escapeHtml(trMeta(item))}</span>`;
}

function trProposalHtml(item) {
    const key = trKey(item);
    return `<li class="triage-item">
        <input type="checkbox" data-key="${escapeHtml(key)}" ${TRIAGE.unchecked.has(key) ? '' : 'checked'} aria-label="${escapeHtml(item.summary)}">
        <div class="triage-text">${trEventHtml(item)}
            <span class="triage-reason">${escapeHtml(item.reason)}</span>
        </div>
    </li>`;
}

function trKeptHtml(item) {
    const reason = { critical: trT('kept_critical', 'Critique : un critique se lit toujours.'),
        no_answer: trT('kept_no_answer', 'L’IA n’a pas répondu pour celui-ci.') }[item.kind] || item.reason || trT('kept_ai', 'L’IA préfère que tu le regardes.');
    return `<li class="triage-item triage-item--kept">
        <div class="triage-text">${trEventHtml(item)}
            <span class="triage-reason">${escapeHtml(reason)}</span>
        </div>
    </li>`;
}

async function trCancel() {
    if (!TRIAGE.job?.id) return;
    await apiFetch(`/api/triage/jobs/${TRIAGE.job.id}`, { method: 'DELETE' }).catch(() => {});
    TRIAGE.job = { ...TRIAGE.job, status: 'cancelled' };
    clearTimeout(TRIAGE.timer);
    trRender();
}

async function trApply() {
    const job = TRIAGE.job;
    const keys = job.proposals.filter(p => !TRIAGE.unchecked.has(trKey(p))).map(p => ({ rule_id: p.rule_id, key: p.key }));
    const button = document.getElementById('triage-apply');
    if (button) button.disabled = true;
    try {
        await apiFetch('/api/triage/apply', { method: 'POST', body: { job_id: job.id, keys } });
    } catch (e) {
        document.getElementById('triage-body').insertAdjacentHTML('beforeend', `<p class="triage-error" role="alert">${escapeHtml(trT('failed_expired', 'Ce tri n’est plus disponible. Relance-le.'))}</p>`);
        return;
    }
    document.getElementById('triage-dialog').close();
    TRIAGE.job = null;
    document.dispatchEvent(new CustomEvent('events-changed'));
    trRefreshLast();
}

// The last triage stays visible and undoable even when nothing is left to read.
async function trRefreshLast() {
    const el = document.getElementById('triage-last');
    if (!el) return;
    let last = null;
    try {
        last = (await apiFetch('/api/triage/last')).last;
    } catch (e) {
        return;
    }
    if (!last) {
        el.innerHTML = '';
        return;
    }
    const when = new Date(last.at).toLocaleString([], { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    el.innerHTML = `<p class="triage-last">${escapeHtml(trCount(last.events, 'last_one', 'Dernier tri, le {d} : 1 événement marqué comme lu.', 'last_many', 'Dernier tri, le {d} : {n} événements marqués comme lus.').split('{d}').join(when))}
        <button type="button" class="triage-undo" onclick="trUndo()">${escapeHtml(trT('undo', 'Annuler ce tri'))}</button></p>`;
}

async function trUndo() {
    await apiFetch('/api/triage/undo', { method: 'POST' });
    document.dispatchEvent(new CustomEvent('events-changed'));
    trRefreshLast();
}

document.addEventListener('DOMContentLoaded', trRefreshLast);
