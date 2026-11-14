/* ── Keywords tab of the rule page ─────────────────────────────────────────────
 * Keywords and exclusions with their real-world stats, the AI arbiter's changes
 * (applied, pending, history) and the learning sessions on a period.
 * Relies on rule_page.js helpers: RP, T, esc, fmtWhen, minutesSince, fmtMinutes, flash.
 */

const KW_ORIGINS = {
    manual: ['rule.kw_origin_manual', 'à la main'],
    blind_spot: ['rule.kw_origin_blind', 'angle mort'],
    noise: ['rule.kw_origin_noise', 'bruit'],
    feedback: ['rule.kw_origin_feedback', 'signalement'],
    learning: ['rule.kw_origin_learning', 'apprentissage'],
    chat: ['rule.kw_origin_chat', 'chat'],
};
const KW_HOLDS = {
    suggest_mode: ['rule.kw_hold_suggest', 'mode suggestion'],
    low_confidence: ['rule.kw_hold_confidence', 'l’IA n’est pas assez sûre'],
    too_frequent: ['rule.kw_hold_frequent', 'déclencherait très souvent'],
    last_keyword: ['rule.kw_hold_last', 'c’est le dernier mot-clé de la règle'],
    important_alerts: ['rule.kw_hold_important', 'il a déclenché des alertes importantes'],
    hides_important: ['rule.kw_hold_hides', 'masquerait des alertes importantes'],
    neutralizes_keyword: ['rule.kw_hold_neutralizes', 'neutraliserait un mot-clé'],
    manual_edit: ['rule.kw_hold_manual', 'modifié à la main'],
};
const KW_STATUS = {
    applied: ['rule.kw_status_applied', 'appliqué'],
    pending: ['rule.kw_status_pending', 'à valider'],
    rejected: ['rule.kw_status_rejected', 'refusé'],
    reverted: ['rule.kw_status_reverted', 'annulé'],
    dismissed: ['rule.kw_status_dismissed', 'écarté par l’autopilote'],
};
const KW_ACTIVE = ['pending', 'scanning', 'refining'];
const AP_FIELDS = {
    name: ['rules.label_name', 'Nom'],
    application_context: ['rf.app_context', 'Contexte applicatif'],
    anti_spam_delay: ['rf.grouping_window', 'Fenêtre de regroupement'],
    inactivity_period_hours: ['rf.notify_silent', 'Alerter si la source est muette'],
};

/** Settings defined by the autopilot in a session (name, context, rhythm). */
function apValuesHtml(sum) {
    const vals = sum.autopilot_values || {};
    const keys = Object.keys(vals);
    if (!keys.length) return '';
    const fmt = (k, v) => k === 'anti_spam_delay' ? `${v} s` : k === 'inactivity_period_hours' ? `${v} h` : v;
    return `<ul class="kw-session-changes">${keys.map(k => `<li><strong>${esc(T(...AP_FIELDS[k]))}</strong> : ${esc(fmt(k, vals[k]))}</li>`).join('')}</ul>`;
}
let kwLastSessionStatus = null;

function kwLabel(map, key) { return map[key] ? T(...map[key]) : (key || ''); }

function kwChangeText(c) {
    const kind = c.kind === 'keyword' ? T('rule.kw_keyword', 'mot-clé') : T('rule.kw_exclusion', 'exclusion');
    const signal = c.evidence && c.evidence.signal ? ` · ${T('rule.kw_signal', 'signal ponctuel')}` : '';
    return `${c.action === 'add' ? '+' : '−'} ${kind} « ${c.value} »${signal}`;
}

function kwSevBar(sev, total) {
    if (!total) return '';
    const seg = (k, cls) => sev[k] ? `<span class="${cls}" style="width:${(100 * sev[k] / total).toFixed(1)}%"></span>` : '';
    return `<span class="kw-sev" title="${esc(Object.entries(sev).map(([k, v]) => `${k} ${v}`).join(' · '))}">
        ${seg('critical', 'rp-bar-critical')}${seg('warning', 'rp-bar-warning')}${seg('info', 'rp-bar-info')}</span>`;
}

function kwEvidence(c) {
    const ev = c.evidence || {};
    const facts = [];
    if (ev.count) facts.push(T('rule.kw_ev_count', '{n} occurrence(s), ≈ {d}/jour', { n: ev.count, d: ev.per_day }));
    if (ev.alerts != null) facts.push(T('rule.kw_ev_alerts', '{n} alerte(s) sur 30 jours', { n: ev.alerts }));
    if (ev.irrelevant) facts.push(T('rule.kw_ev_irrelevant', '{n} signalée(s) pas pertinente(s)', { n: ev.irrelevant }));
    const ex = (ev.hidden_examples || ev.examples || []).slice(0, 3);
    return `${facts.length ? `<span class="rp-muted">${esc(facts.join(' · '))}</span>` : ''}
        ${ex.length ? `<details class="kw-ev"><summary>${esc(ev.hidden_examples ? T('rule.kw_ev_hidden', 'Alertes importantes qui seraient masquées') : T('rule.kw_ev_examples', 'Lignes concernées'))}</summary>
            ${ex.map(l => `<div class="mono kw-ev-line">${esc(l)}</div>`).join('')}</details>` : ''}`;
}

async function renderKeywords() {
    const panel = document.getElementById('tab-keywords');
    let d;
    try {
        d = await apiFetch(`/api/rules/${RP.id}/keywords`);
    } catch (e) {
        panel.innerHTML = `<div class="loading">${esc(T('common.error', 'Erreur'))} : ${esc(e.message)}</div>`;
        return;
    }
    if (RP.tab !== 'keywords') return;
    setKwBadge(d.pending.length);
    const s = d.session;
    // A session that just ended changed the keywords: refresh the header too
    if (s && kwLastSessionStatus && KW_ACTIVE.includes(kwLastSessionStatus) && !KW_ACTIVE.includes(s.status)) loadHeader();
    kwLastSessionStatus = s ? s.status : null;
    const open = new Set([...panel.querySelectorAll('details[open]')].map(x => x.dataset.key));
    // La page se redessine toutes les 3 s pendant une analyse ou une revue : garder la durée choisie
    const picked = { hist: document.getElementById('kw-hist-days')?.value || '168', live: document.getElementById('kw-live-hours')?.value || '6' };
    const review = d.review || {};
    const reviewing = !!review.running;
    const sessionRunning = !!(s && KW_ACTIVE.includes(s.status));
    const busyTitle = T('rule.kw_session_busy_title', 'Une analyse est déjà en cours : attends sa fin ou annule-la ci-dessous.');

    const modeHint = {
        auto: T('rule.kw_mode_auto_hint', 'L’IA locale examine la règle toutes les heures : elle ajoute les mots-clés qui manquent et écarte le bruit, après des vérifications sur tes données. Ce qui ne passe pas ces vérifications t’est proposé.'),
        suggest: T('rule.kw_mode_suggest_hint', 'L’IA examine la règle toutes les heures et te propose ses changements ; rien n’est modifié sans ta validation.'),
        off: T('rule.kw_mode_off_hint', 'Les mots-clés ne changent que lorsque tu les modifies.'),
    }[d.mode];
    const head = `
    <section class="rp-card">
        <div class="rp-card-head"><h2>${esc(T('rule.kw_tuning', 'Ajustement automatique'))}</h2>
            <span class="rp-muted">${d.tuned_at ? esc(T('rule.kw_last_review', 'dernière revue {when}', { when: fmtWhen(d.tuned_at) })) : esc(T('rule.kw_no_review', 'pas encore de revue'))}</span></div>
        <div class="rf-seg" role="radiogroup" aria-label="${esc(T('rule.kw_tuning', 'Ajustement automatique'))}">
            ${['auto', 'suggest', 'off'].map(m => `<input type="radio" name="kw-mode" id="kw-mode-${m}" value="${m}" ${d.mode === m ? 'checked' : ''} onchange="kwSetMode('${m}')">
                <label for="kw-mode-${m}">${esc(T('rf.tuning_' + m, { auto: 'Automatique', suggest: 'Suggestions', off: 'Désactivé' }[m]))}</label>`).join('')}
        </div>
        <p class="rp-muted kw-mode-hint">${esc(modeHint)}</p>
        <div class="rp-actions kw-learn-actions">
            <button class="btn btn-secondary btn-sm kw-review-btn" onclick="kwReviewNow(this)" aria-busy="${reviewing}"
                title="${esc(reviewing ? T('rule.kw_reviewing_title', 'Une revue de cette règle est en cours : le bouton se réactive quand elle est finie.') : T('rule.kw_review_now_title', 'Lance tout de suite la revue horaire de l’IA.'))}"
                ${d.mode === 'off' || reviewing ? 'disabled' : ''}>${reviewing ? `<span class="kw-spin" aria-hidden="true"></span>${esc(T('rule.kw_reviewing', 'Revue en cours…'))}` : esc(T('rule.kw_review_now', 'Revoir maintenant'))}</button>
            <span class="kw-learn-group">
                <select id="kw-hist-days" aria-label="${esc(T('rule.kw_history_period', 'Période à analyser'))}" ${sessionRunning ? 'disabled' : ''}>
                    ${[1, 3, 7, 14, 30].map(n => `<option value="${n * 24}" ${String(n * 24) === picked.hist ? 'selected' : ''}>${esc(T('rule.kw_days', '{n} derniers jours', { n }))}</option>`).join('')}
                </select>
                <button class="btn btn-secondary btn-sm" onclick="kwLearn('history', 'kw-hist-days', this)" ${sessionRunning ? 'disabled' : ''} title="${esc(sessionRunning ? busyTitle : T('rule.kw_learn_history_title', 'Relit le journal sur la période choisie et demande à l’IA quels mots-clés et exclusions changer.'))}">${esc(T('rule.kw_learn_history', 'Analyser l’historique'))}</button>
            </span>
            <span class="kw-learn-group">
                <select id="kw-live-hours" aria-label="${esc(T('rule.kw_live_period', 'Durée d’observation'))}" ${sessionRunning ? 'disabled' : ''}>
                    ${[1, 6, 24].map(n => `<option value="${n}" ${String(n) === picked.live ? 'selected' : ''}>${esc(T('rule.kw_hours', '{n} h', { n }))}</option>`).join('')}
                </select>
                <button class="btn btn-secondary btn-sm" onclick="kwLearn('live', 'kw-live-hours', this)" ${sessionRunning ? 'disabled' : ''} title="${esc(sessionRunning ? busyTitle : T('rule.kw_learn_live_title', 'Garde ce qui arrive pendant la durée choisie, puis fait la même analyse.'))}">${esc(T('rule.kw_learn_live', 'Observer'))}</button>
            </span>
        </div>
        ${kwReviewStatus(review)}
    </section>`;

    let session = '';
    if (s && (KW_ACTIVE.includes(s.status) || minutesSince(s.validated_at || s.created_at) < 24 * 60)) {
        const sum = s.summary || {};
        let body;
        if (s.status === 'pending' && s.mode === 'live' && sum.autopilot) {
            body = `<p>${esc(T('rule.ap_session_observing', 'Pas assez d’historique dans ce journal : Sentinel observe la source jusqu’à {when}, puis l’IA définit la règle. En attendant, elle alerte sur les lignes de niveau erreur explicite.', { when: fmtWhen(s.period_end) }))}</p>`;
        } else if (s.status === 'pending' && s.mode === 'live') {
            body = `<p>${esc(T('rule.kw_session_observing', 'Observation en cours jusqu’à {when}. L’analyse commencera ensuite.', { when: fmtWhen(s.period_end) }))}</p>`;
        } else if (s.status === 'scanning' || s.status === 'pending') {
            body = `<p>${esc(T('rule.kw_session_scanning', 'Lecture du journal…'))}</p><div class="rp-progress"><div style="width:${s.progress}%"></div></div>`;
        } else if (s.status === 'refining') {
            body = `<p>${esc(T('rule.kw_session_refining', 'L’IA examine {n} ligne(s) regroupées en {t} modèle(s)…', { n: sum.lines || 0, t: sum.templates || 0 }))}</p>`;
        } else if (s.status === 'validated') {
            body = `<p>${esc(T('rule.kw_session_done', '{n} ligne(s) lues, {b} angle(s) mort(s), {k} mot(s)-clé(s) examiné(s) : {c} changement(s).',
                { n: sum.lines || 0, b: sum.blind_spots || 0, k: sum.keywords_reviewed || 0, c: s.changes.length }))}</p>
                ${apValuesHtml(sum)}
                ${s.changes.length ? `<ul class="kw-session-changes">${s.changes.map(c => `<li><span class="mono">${esc(kwChangeText(c))}</span>
                    <span class="kw-status kw-status--${c.status}">${esc(kwLabel(KW_STATUS, c.status))}</span> <span class="rp-muted">${esc(c.reason)}</span></li>`).join('')}</ul>` : ''}`;
        } else if (s.status === 'reverted') {
            body = `<p class="rp-muted">${esc(T('rule.kw_session_reverted', 'Les changements de cet apprentissage ont été annulés.'))}</p>`;
        } else {
            body = `<p class="rp-ko">${esc(s.error_message || T('common.error', 'Erreur'))}</p>`;
        }
        const period = `${fmtWhen(s.period_start)} → ${fmtWhen(s.period_end)}`;
        session = `
        <section class="rp-card">
            <div class="rp-card-head"><h2>${esc(sum.refresh ? T('rule.ap_session_refresh', 'Relecture par le pilote automatique')
                : sum.autopilot ? T('rule.ap_session_title', 'Mise en place par le pilote automatique')
                : s.mode === 'live' ? T('rule.kw_session_live', 'Observation') : T('rule.kw_session_history', 'Analyse de l’historique'))}</h2>
                <span class="rp-muted">${esc(period)}</span></div>
            ${body}
            <div class="rp-actions">
                ${KW_ACTIVE.includes(s.status) ? `<button class="btn btn-secondary btn-sm" onclick="kwSessionAction(${s.id}, 'cancel', this)" title="${esc(T('rule.kw_session_cancel_title', 'Arrête cette analyse. Rien n’est modifié.'))}">${esc(T('common.cancel', 'Annuler'))}</button>` : ''}
                ${s.status === 'validated' && s.changes.some(c => c.status === 'applied') ? `<button class="btn btn-secondary btn-sm" onclick="kwSessionAction(${s.id}, 'revert', this)" title="${esc(T('rule.kw_session_revert_title', 'Défait tous les changements de cette analyse.'))}">${esc(T('rule.kw_session_revert', 'Annuler ces changements'))}</button>` : ''}
                ${['validated', 'reverted'].includes(s.status) ? `<a class="btn btn-secondary btn-sm" href="/api/keyword-learning/${s.id}/log" title="${esc(T('rule.kw_session_log_title', 'Télécharge les échanges avec l’IA.'))}">${esc(T('rule.kw_session_log', 'Télécharger le journal de l’IA'))}</a>` : ''}
            </div>
        </section>`;
    }

    const pending = d.pending.length ? `
    <section class="rp-card">
        <div class="rp-card-head"><h2>${esc(T('rule.kw_pending', 'À valider'))}</h2>
            <span class="rp-muted">${esc(T('rule.kw_pending_hint', 'Changements proposés par l’IA que les vérifications ne permettent pas d’appliquer seuls.'))}</span></div>
        ${d.pending.map(c => `<div class="kw-change">
            <div class="kw-change-main"><strong class="mono">${esc(kwChangeText(c))}</strong>
                <span class="rp-muted">${esc(c.reason)}</span>
                <span class="kw-hold">${esc(T('rule.kw_hold_prefix', 'Non appliqué : {why}', { why: kwLabel(KW_HOLDS, c.hold_reason) }))}</span>
                ${kwEvidence(c)}</div>
            <span class="rp-actions">
                <button class="btn btn-primary btn-sm" onclick="kwDecide(${c.id}, 'accept', this)" title="${esc(T('rule.kw_accept_title', 'Applique ce changement à la règle.'))}">${esc(T('rule.accept', 'Accepter'))}</button>
                <button class="btn btn-secondary btn-sm" onclick="kwDecide(${c.id}, 'reject', this)" title="${esc(T('rule.kw_reject_title', 'N’applique pas ce changement.'))}">${esc(T('rule.reject', 'Refuser'))}</button>
            </span></div>`).join('')}
    </section>` : '';

    const kwRows = d.keywords.map(k => `<tr>
        <td><span class="mono">${esc(k.value)}</span>
            ${k.signal ? `<span class="kw-signal" title="${esc(T('rule.kw_signal_badge_title', 'Ses lignes sont gardées en info sur le tableau de bord, closes aussitôt, sans notification ni surveillance du retour à la normale.'))}">${esc(T('rule.kw_signal', 'signal ponctuel'))}</span>` : ''}
            ${k.neutralized_by ? `<div class="rp-ko kw-warn">${esc(T('rule.kw_neutralized', 'Ne peut jamais se déclencher : l’exclusion « {x} » l’emporte.', { x: k.neutralized_by }))}</div>` : ''}</td>
        <td><strong>${k.alerts_30d}</strong> ${kwSevBar(k.severities, k.alerts_30d)}
            ${k.irrelevant ? `<span class="rp-muted">${esc(T('rule.kw_irrelevant_n', '{n} pas pertinente(s)', { n: k.irrelevant }))}</span>` : ''}</td>
        <td>${k.lines_per_day == null ? '—' : k.lines_per_day}</td>
        <td>${k.last_alert_at ? esc(fmtWhen(k.last_alert_at)) : '—'}</td>
        <td>${esc(kwLabel(KW_ORIGINS, k.origin))}</td>
        <td class="kw-row-actions"><button class="btn btn-secondary btn-sm" data-v="${esc(k.value)}" onclick="kwSetSignal(this.dataset.v, ${k.signal ? 'false' : 'true'}, this)"
                title="${esc(k.signal ? T('rule.kw_to_alert_title', 'Ses lignes redeviennent des alertes : analysées, notifiées selon le seuil de la règle, et surveillées jusqu’au retour à la normale.')
                                      : T('rule.kw_to_signal_title', 'Ses lignes ne sont plus des alertes : gardées en info sur le tableau de bord, closes aussitôt, sans notification. Pour un événement à tracer qui ne demande rien (attaque bloquée, incident passager d’un client).'))}">${esc(k.signal ? T('rule.kw_to_alert', 'En faire une alerte') : T('rule.kw_to_signal', 'Signal ponctuel'))}</button>
            <button class="btn btn-secondary btn-sm" data-v="${esc(k.value)}" onclick="kwEditList('keyword', 'remove', this.dataset.v, this)" title="${esc(T('rule.kw_remove_keyword_title', 'Retire ce mot-clé tout de suite.'))}">${esc(T('rule.kw_remove', 'Retirer'))}</button></td>
    </tr>`).join('');
    const keywords = `
    <section class="rp-card rp-table-card">
        <div class="rp-card-head"><h2>${esc(T('rf.keywords', 'Mots-clés déclencheurs'))}</h2>
            <span class="rp-muted">${d.lines_per_day != null ? esc(T('rule.kw_lines_day', '≈ {n} lignes reçues par jour', { n: d.lines_per_day })) : ''}</span></div>
        ${d.keywords.length ? `<div class="rp-scroll"><table class="rp-table">
            <thead><tr><th>${esc(T('rule.kw_keyword_col', 'Mot-clé'))}</th><th>${esc(T('rule.alerts_30d', 'Alertes · 30 jours'))}</th>
                <th>${esc(T('rule.kw_lines_col', 'Lignes / jour'))}</th><th>${esc(T('rule.last', 'Dernier'))}</th><th>${esc(T('rule.kw_origin', 'Origine'))}</th><th></th></tr></thead>
            <tbody>${kwRows}</tbody></table></div>`
            : `<p class="rp-muted">${esc(RP.rule && RP.rule.autopilot && RP.rule.autopilot.enabled && RP.rule.autopilot.status !== 'active'
                ? T('rule.ap_kw_none', 'Pas encore de mot-clé : le pilote automatique les choisira. En attendant, la règle alerte sur les lignes de niveau erreur explicite.')
                : T('rule.kw_none', 'Aucun mot-clé : la règle ne déclenche rien. Lance une analyse de l’historique ou ajoute-en un.'))}</p>`}
        <form class="kw-add" onsubmit="kwAdd(event, 'keyword')" title="${esc(T('rule.kw_add_keyword_title', 'Une ligne qui contient ce texte déclenche une analyse.'))}">
            <input type="text" id="kw-add-keyword" placeholder="${esc(T('rule.kw_add_keyword', 'Ajouter un mot-clé'))}" aria-label="${esc(T('rule.kw_add_keyword', 'Ajouter un mot-clé'))}">
            <button class="btn btn-secondary btn-sm" type="submit">${esc(T('rule.kw_add', 'Ajouter'))}</button>
        </form>
    </section>`;

    const exclusions = `
    <section class="rp-card rp-table-card">
        <div class="rp-card-head"><h2>${esc(T('rf.exclusions', 'Lignes ignorées'))}</h2>
            <span class="rp-muted">${esc(T('rule.kw_exclusions_hint', 'Une ligne contenant l’un de ces termes ne déclenche rien, même si un mot-clé correspond.'))}</span></div>
        ${d.exclusions.length ? `<div class="rp-scroll"><table class="rp-table">
            <thead><tr><th>${esc(T('rule.kw_exclusion_col', 'Exclusion'))}</th><th>${esc(T('rule.kw_ignored_col', 'Lignes ignorées / jour'))}</th><th>${esc(T('rule.kw_origin', 'Origine'))}</th><th></th></tr></thead>
            <tbody>${d.exclusions.map(x => `<tr><td class="mono">${esc(x.value)}</td><td>${x.lines_per_day == null ? '—' : x.lines_per_day}</td><td>${esc(kwLabel(KW_ORIGINS, x.origin))}</td>
                <td><button class="btn btn-secondary btn-sm" data-v="${esc(x.value)}" onclick="kwEditList('exclusion', 'remove', this.dataset.v, this)" title="${esc(T('rule.kw_remove_exclusion_title', 'Retire cette exclusion.'))}">${esc(T('rule.kw_remove', 'Retirer'))}</button></td></tr>`).join('')}</tbody>
        </table></div>` : `<p class="rp-muted">${esc(T('rule.kw_no_exclusion', 'Aucune exclusion.'))}</p>`}
        <form class="kw-add" onsubmit="kwAdd(event, 'exclusion')" title="${esc(T('rule.kw_add_exclusion_title', 'Une ligne qui contient ce texte ne déclenche plus rien.'))}">
            <input type="text" id="kw-add-exclusion" placeholder="${esc(T('rule.kw_add_exclusion', 'Ajouter une exclusion'))}" aria-label="${esc(T('rule.kw_add_exclusion', 'Ajouter une exclusion'))}">
            <button class="btn btn-secondary btn-sm" type="submit">${esc(T('rule.kw_add', 'Ajouter'))}</button>
        </form>
    </section>`;

    const watching = d.watching.length ? `
    <section class="rp-card rp-table-card">
        <div class="rp-card-head"><h2>${esc(T('rule.kw_watching', 'Angles morts en observation'))}</h2>
            <span class="rp-muted">${esc(T('rule.kw_watching_hint', 'Lignes d’erreur qu’aucun mot-clé n’attrape. L’IA les examine à partir de 3 occurrences.'))}</span></div>
        <div class="rp-scroll"><table class="rp-table">
            <thead><tr><th>${esc(T('rule.event', 'Événement'))}</th><th>${esc(T('rule.count', 'Nombre'))}</th><th>${esc(T('rule.last', 'Dernier'))}</th></tr></thead>
            <tbody>${d.watching.map(w => `<tr><td class="mono rp-sig" title="${esc(w.example)}">${esc(w.signature.replace(/^<TS>\s*/, ''))}</td>
                <td>× ${w.count}</td><td>${esc(fmtWhen(w.last_seen))}</td></tr>`).join('')}</tbody>
        </table></div>
    </section>` : '';

    const history = d.history.length ? `
    <section class="rp-card rp-table-card">
        <details data-key="history" ${open.has('history') ? 'open' : ''}>
            <summary class="rp-card-head"><h2>${esc(T('rule.kw_history', 'Historique des changements'))}</h2>
                <span class="rp-muted">${esc(T('rule.kw_history_n', '{n} changement(s)', { n: d.history.length }))}</span></summary>
            <div class="rp-scroll"><table class="rp-table">
                <thead><tr><th>${esc(T('rule.date', 'Date'))}</th><th>${esc(T('rule.kw_change', 'Changement'))}</th><th>${esc(T('rule.kw_origin', 'Origine'))}</th>
                    <th>${esc(T('rule.kw_reason', 'Raison'))}</th><th>${esc(T('rule.kw_status', 'Statut'))}</th><th></th></tr></thead>
                <tbody>${d.history.map(c => `<tr>
                    <td>${esc(fmtWhen(c.decided_at || c.created_at))}</td>
                    <td class="mono">${esc(kwChangeText(c))}</td>
                    <td>${esc(kwLabel(KW_ORIGINS, c.origin))}</td>
                    <td class="kw-reason">${esc(c.reason)}${c.hold_reason === 'manual_edit' || c.status === 'dismissed' ? ` <span class="rp-muted">(${esc(kwLabel(KW_HOLDS, c.hold_reason))})</span>` : ''}</td>
                    <td><span class="kw-status kw-status--${c.status}">${esc(kwLabel(KW_STATUS, c.status))}</span></td>
                    <td>${c.status === 'applied' ? `<button class="btn btn-secondary btn-sm" onclick="kwDecide(${c.id}, 'revert', this)" title="${esc(T('rule.kw_undo_title', 'Défait ce changement sur la règle.'))}">${esc(T('rule.kw_undo', 'Annuler'))}</button>`
                        : c.status === 'dismissed' ? `<button class="btn btn-secondary btn-sm" title="${esc(T('rule.kw_apply_anyway_title', 'L’autopilote l’a écarté par prudence ({why}).', { why: kwLabel(KW_HOLDS, c.hold_reason) }))}" onclick="kwDecide(${c.id}, 'accept', this)">${esc(T('rule.kw_apply_anyway', 'Appliquer quand même'))}</button>` : ''}</td>
                </tr>`).join('')}</tbody>
            </table></div>
        </details>
    </section>` : '';

    panel.innerHTML = head + session + pending + keywords + exclusions + watching + history;
    if (RP.kwPrefill) {
        const input = document.getElementById('kw-add-exclusion');
        input.value = RP.kwPrefill;
        RP.kwPrefill = null;
        input.focus();
        input.select();
    }
    // Faster refresh while a session or a review runs
    clearTabTimers();
    RP.timers.push(setInterval(renderKeywords, sessionRunning || reviewing ? 3000 : 30000));
}

function setKwBadge(n) {
    const b = document.getElementById('rp-kw-badge');
    if (!b) return;
    b.textContent = n || '';
    b.classList.toggle('hidden', !n);
    b.title = n ? T('rule.kw_pending_badge', '{n} changement(s) à valider', { n }) : '';
}

async function kwSetMode(mode) {
    try {
        await apiFetch(`/api/rules/${RP.id}`, { method: 'PUT', body: { keyword_tuning_mode: mode } });
        renderKeywords();
    } catch (e) { alert(e.message); }
}

/** Under the buttons: the review that runs, then the result of the last one asked from this page (15 min). */
function kwReviewStatus(review) {
    if (review.running) {
        return `<p class="kw-review-status" role="status"><span class="kw-spin" aria-hidden="true"></span>${esc(T('rule.kw_reviewing_hint', 'L’IA examine la règle. Cela peut prendre de quelques secondes à quelques minutes ; tu peux quitter la page.'))}</p>`;
    }
    if (!review.finished_at || minutesSince(review.finished_at) > 15) return '';
    const when = fmtWhen(review.finished_at);
    if (review.failed) {
        return `<p class="kw-review-status rp-ko" role="status">${esc(T('rule.kw_review_failed', 'Revue de {when} interrompue : l’IA n’a pas répondu. Le détail est dans le journal de l’application.', { when }))}</p>`;
    }
    return `<p class="kw-review-status rp-muted" role="status">${esc(review.changes
        ? T('rule.kw_review_done', 'Revue de {when} terminée : {n} changement(s), visibles ci-dessous.', { when, n: review.changes })
        : T('rule.kw_review_nothing', 'Revue de {when} terminée : rien à changer.', { when }))}</p>`;
}

async function kwReviewNow(btn) {
    btn.disabled = true;
    try {
        await apiFetch(`/api/rules/${RP.id}/keywords/review`, { method: 'POST' });
    } catch (e) { alert(e.message); }
    renderKeywords();  // the button stays disabled while the review runs
}

async function kwLearn(mode, selectId, btn) {
    btn.disabled = true;
    const hours = parseInt(document.getElementById(selectId).value);
    try {
        await apiFetch('/api/keyword-learning/start', { method: 'POST', body: { rule_id: RP.id, mode, hours } });
        await loadHeader();
        renderKeywords();
    } catch (e) { alert(e.message); btn.disabled = false; }
}

async function kwSessionAction(id, action, btn) {
    btn.disabled = true;
    try {
        if (action === 'cancel') await apiFetch(`/api/keyword-learning/${id}`, { method: 'DELETE' });
        else await apiFetch(`/api/keyword-learning/${id}/revert`, { method: 'POST' });
        loadHeader();
        renderKeywords();
    } catch (e) { alert(e.message); btn.disabled = false; }
}

async function kwDecide(changeId, action, btn) {
    btn.disabled = true;
    try {
        await apiFetch(`/api/rules/${RP.id}/keyword-changes/${changeId}/${action}`, { method: 'POST' });
        loadHeader();
        renderKeywords();
    } catch (e) { alert(e.message); btn.disabled = false; }
}

/** Manual edit of the keyword or exclusion list (the arbiter will not undo it). */
async function kwEditList(kind, action, value, btn) {
    if (btn) btn.disabled = true;
    try {
        const rule = await apiFetch(`/api/rules/${RP.id}`);
        const field = kind === 'keyword' ? 'keywords' : 'excluded_patterns';
        let list = rule[field] || [];
        if (action === 'add') {
            if (list.some(x => x.toLowerCase() === value.toLowerCase())) return renderKeywords();
            list = list.concat([value]);
        } else {
            list = list.filter(x => x !== value);
        }
        await apiFetch(`/api/rules/${RP.id}`, { method: 'PUT', body: { [field]: list } });
        loadHeader();
        renderKeywords();
    } catch (e) { alert(e.message); if (btn) btn.disabled = false; }
}

async function kwSetSignal(value, signal, btn) {
    if (btn) btn.disabled = true;
    try {
        await apiFetch(`/api/rules/${RP.id}/keywords/signal`, { method: 'POST', body: { value, signal } });
        renderKeywords();
    } catch (e) { alert(e.message); if (btn) btn.disabled = false; }
}

function kwAdd(event, kind) {
    event.preventDefault();
    const input = document.getElementById(kind === 'keyword' ? 'kw-add-keyword' : 'kw-add-exclusion');
    const value = input.value.trim();
    if (value) kwEditList(kind, 'add', value);
}
