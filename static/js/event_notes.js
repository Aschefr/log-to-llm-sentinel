/* Notes for the AI: on an event's card, and the list of all notes in a rule's Analyses tab. */

const NOTE_MAX_CHARS = 500;

function noteT(key, fallback) {
    return (window.t ? window.t(key) : null) || fallback;
}

function noteScopeLabel(scope) {
    return {
        event: noteT('common.note_scope_event', 'Cet événement'),
        related: noteT('common.note_scope_related', 'Événement lié'),
        source: noteT('common.note_scope_source', 'Toute la source'),
    }[scope] || scope;
}

function noteErrorText(error) {
    const known = {
        note_empty: noteT('common.note_err_empty', 'Écris d’abord ta note.'),
        note_too_long: noteT('common.note_err_too_long', 'Cette note dépasse 500 caractères.'),
        notes_limit: noteT('common.note_err_limit', 'Cette règle a atteint 30 notes : supprimes-en une d’abord.'),
    };
    return known[error.message] || error.message;
}

function noteItemHtml(note, opts = {}) {
    const line = opts.showLine && note.line && note.scope !== 'source'
        ? `<code class="event-note-line">${escapeHtml(note.line)}</code>` : '';
    return `<li class="event-note" data-note="${note.id}">
        <div class="event-note-meta">
            <span class="event-note-scope">${escapeHtml(opts.showLine && note.scope === 'event' ? noteT('common.note_scope_an_event', 'Un événement') : noteScopeLabel(note.scope))}</span>
            <span>${note.updated_at ? escapeHtml(formatDate(note.updated_at)) : ''}</span>
            <span class="event-note-actions">
                <button type="button" class="btn-link" onclick="editNote(this)">${escapeHtml(noteT('common.edit', 'Modifier'))}</button>
                <button type="button" class="btn-link" onclick="deleteNote(this)">${escapeHtml(noteT('common.delete', 'Supprimer'))}</button>
            </span>
        </div>
        ${line}
        <p class="event-note-text">${escapeHtml(note.text)}</p>
    </li>`;
}

/** Notes block of an event's card. */
function eventNotesHtml(ev, analysisId) {
    const notes = ev.notes || [];
    return `<section class="event-notes${notes.length ? '' : ' is-empty'}" data-rule="${ev.rule_id}" data-analysis="${analysisId}">
        <h4 class="event-notes-title">${escapeHtml(noteT('common.notes_title', 'Notes pour l’IA'))}</h4>
        <ul class="event-notes-list">${notes.map(n => noteItemHtml(n)).join('')}</ul>
    </section>`;
}

function noteFormHtml({ text = '', withScope = false, hint = true }) {
    const id = `note-${Math.random().toString(36).slice(2, 9)}`;
    const scope = withScope ? `<fieldset class="event-note-scopes">
            <legend>${escapeHtml(noteT('common.note_scope_label', 'S’applique à'))}</legend>
            <label><input type="radio" name="${id}-scope" value="event" checked> ${escapeHtml(noteT('common.note_scope_event', 'Cet événement'))}</label>
            <label><input type="radio" name="${id}-scope" value="source"> ${escapeHtml(noteT('common.note_scope_source', 'Toute la source'))}</label>
        </fieldset>` : '';
    return `<form class="event-note-form" onsubmit="event.preventDefault(); submitNoteForm(this)">
        <label class="event-note-label" for="${id}">${escapeHtml(noteT('common.note_label', 'Ce que l’IA devrait savoir'))}</label>
        <textarea id="${id}" rows="3" maxlength="${NOTE_MAX_CHARS}" oninput="updateNoteCounter(this)"
            placeholder="${escapeHtml(noteT('common.note_placeholder', 'Ex. : ce serveur tourne sous Unraid, sans systemd.'))}">${escapeHtml(text)}</textarea>
        <div class="event-note-form-row">
            <span class="event-note-counter" aria-live="polite">${text.length}/${NOTE_MAX_CHARS}</span>
            ${scope}
        </div>
        ${hint ? `<p class="form-hint">${escapeHtml(noteT('common.note_hint', 'Elle informe l’IA sans jamais faire taire une alerte. Prise en compte dès la prochaine analyse ; « Ré-essayer » l’applique à celle-ci.'))}</p>` : ''}
        <p class="event-note-error hidden" role="alert"></p>
        <div class="event-note-form-actions">
            <button type="submit" class="btn btn-primary btn-sm">${escapeHtml(noteT('common.save', 'Enregistrer'))}</button>
            <button type="button" class="btn btn-secondary btn-sm" onclick="closeNoteForm(this.closest('form'))">${escapeHtml(noteT('common.cancel', 'Annuler'))}</button>
        </div>
    </form>`;
}

function updateNoteCounter(textarea) {
    textarea.closest('form').querySelector('.event-note-counter').textContent = `${textarea.value.length}/${NOTE_MAX_CHARS}`;
}

function noteFormFrom(html) {
    const template = document.createElement('template');
    template.innerHTML = html.trim();
    return template.content.firstElementChild;
}

/** Opens the form to add a note from a card. */
function openNoteForm(btn) {
    const section = btn.closest('.analysis-card').querySelector('.event-notes');
    const open = section.querySelector(':scope > .event-note-form');
    if (open) { open.querySelector('textarea').focus(); return; }
    section.classList.remove('is-empty');
    const form = noteFormFrom(noteFormHtml({ withScope: true }));
    form._opener = btn;
    section.append(form);
    form.querySelector('textarea').focus();
}

function closeNoteForm(form) {
    const item = form.closest('.event-note');
    const section = form.closest('.event-notes, .rule-notes');
    const opener = form._opener;
    form.remove();
    if (item) {
        item.querySelector('.event-note-text').classList.remove('hidden');
        item.querySelector('.event-note-actions').classList.remove('hidden');
    }
    if (section && section.classList.contains('event-notes') && !section.querySelector('.event-note')) section.classList.add('is-empty');
    if (opener && opener.isConnected) opener.focus();
}

async function submitNoteForm(form) {
    const textarea = form.querySelector('textarea');
    const error = form.querySelector('.event-note-error');
    const submit = form.querySelector('[type="submit"]');
    const item = form.closest('.event-note');
    const section = form.closest('.event-notes, .rule-notes');
    error.classList.add('hidden');
    submit.disabled = true;
    try {
        if (item) {
            await apiFetch(`/api/monitor/notes/${item.dataset.note}`, { method: 'PUT', body: JSON.stringify({ text: textarea.value }) });
        } else {
            const scope = form.querySelector('input[type="radio"]:checked');
            const onEvent = scope && scope.value === 'event';
            await apiFetch(`/api/monitor/rules/${section.dataset.rule}/notes`, {
                method: 'POST',
                body: JSON.stringify({ text: textarea.value, analysis_id: onEvent ? Number(section.dataset.analysis) : null }),
            });
        }
        await refreshNotes(section);
    } catch (e) {
        error.textContent = noteErrorText(e);
        error.classList.remove('hidden');
        submit.disabled = false;
    }
}

function editNote(btn) {
    const item = btn.closest('.event-note');
    if (item.querySelector('.event-note-form')) return;
    const paragraph = item.querySelector('.event-note-text');
    const form = noteFormFrom(noteFormHtml({ text: paragraph.textContent, hint: false }));
    form._opener = btn;
    paragraph.classList.add('hidden');
    item.querySelector('.event-note-actions').classList.add('hidden');
    item.append(form);
    const textarea = form.querySelector('textarea');
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
}

function deleteNote(btn) {
    const item = btn.closest('.event-note');
    const section = item.closest('.event-notes, .rule-notes');
    showInlineConfirm(btn, noteT('common.note_confirm_delete', 'Supprimer cette note ?'), async () => {
        try {
            await apiFetch(`/api/monitor/notes/${item.dataset.note}`, { method: 'DELETE' });
            await refreshNotes(section);
        } catch (e) {
            alert(`${noteT('common.error', 'Erreur')} : ${e.message}`);
        }
    });
}

/** Reloads a notes block after a change. */
async function refreshNotes(section) {
    if (section.classList.contains('rule-notes')) {
        await loadRuleNotes(section);
    } else {
        const res = await apiFetch(`/api/monitor/analyses/${section.dataset.analysis}/notes`);
        section.querySelector('.event-notes-list').innerHTML = res.notes.map(n => noteItemHtml(n)).join('');
        section.querySelector(':scope > .event-note-form')?.remove();
        section.classList.toggle('is-empty', !res.notes.length);
        updateCardNoteCount(section.closest('.analysis-card'), res.notes.length);
    }
    document.dispatchEvent(new CustomEvent('notes-changed'));
}

function updateCardNoteCount(card, count) {
    if (!card) return;
    const badge = card.querySelector('.event-note-count');
    if (badge) {
        badge.classList.toggle('hidden', !count);
        badge.querySelector('span:last-child').textContent = count;
    }
}

/* All the notes of a rule, in its Analyses tab. */

async function loadRuleNotes(section) {
    const res = await apiFetch(`/api/monitor/rules/${section.dataset.rule}/notes`);
    section.querySelector('.rule-notes-count').textContent = res.notes.length;
    section.querySelector('.event-notes-list').innerHTML = res.notes.map(n => noteItemHtml(n, { showLine: true })).join('');
    section.querySelector('.rule-notes-empty').classList.toggle('hidden', res.notes.length > 0);
    const add = section.querySelector('.rule-notes-add');
    add.classList.toggle('hidden', res.notes.length >= res.max_notes);
    section.querySelector(':scope .rule-notes-body > .event-note-form')?.remove();
}

function openRuleNoteForm(btn) {
    const body = btn.closest('.rule-notes-body');
    const open = body.querySelector(':scope > .event-note-form');
    if (open) { open.querySelector('textarea').focus(); return; }
    const form = noteFormFrom(noteFormHtml({ hint: true }));
    form._opener = btn;
    body.append(form);
    form.querySelector('textarea').focus();
}
