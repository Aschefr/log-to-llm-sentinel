import os

filepath = os.path.abspath('static/js/common.js')
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

new_fn = '''
/**
 * Returns a relative time string like "Il y a 02:34" (or "2:34 ago" in EN).
 * @param {string} dateString - ISO date string from DB
 * @returns {string} relative time HTML or empty string
 */
function formatRelativeTime(dateString) {
    if (!dateString) return '';
    let utcString = dateString;
    if (!dateString.endsWith('Z') && !dateString.includes('+')) {
        utcString += 'Z';
    }
    const date = new Date(utcString);
    const diffMs = Date.now() - date.getTime();
    if (diffMs < 0) return '';

    const totalSec = Math.floor(diffMs / 1000);
    const hours = Math.floor(totalSec / 3600);
    const mins = Math.floor((totalSec % 3600) / 60);
    const secs = totalSec % 60;

    const pad = n => String(n).padStart(2, '0');
    const timeStr = hours > 0
        ? `${pad(hours)}:${pad(mins)}:${pad(secs)}`
        : `${pad(mins)}:${pad(secs)}`;

    const prefix = window.t ? window.t('common.time_ago') : 'Il y a';
    return `${prefix} ${timeStr}`;
}
'''

# Insert after the closing brace of formatDate
marker = "    });\n}\n\nfunction escapeHtml"
replacement = "    });\n}\n" + new_fn + "\nfunction escapeHtml"
content = content.replace(marker, replacement, 1)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print('Done - formatRelativeTime added to common.js')
