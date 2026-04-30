import os

filepath = os.path.abspath('static/js/monitor.js')
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update initial render of last-received timestamp to include relative time span
old_render = '''<span><span style="opacity: 0.7;">${window.t ? window.t('monitor.last_received') : 'Latest received line'}</span> <strong id="last-received-${rule.id}">${rule.last_line_received_at ? formatDate(rule.last_line_received_at) : '\u2014'}</strong></span>'''

new_render = '''<span><span style="opacity: 0.7;">${window.t ? window.t('monitor.last_received') : 'Latest received line'}</span> <strong id="last-received-${rule.id}">${rule.last_line_received_at ? formatDate(rule.last_line_received_at) : '\u2014'}</strong> <span id="last-received-ago-${rule.id}" style="opacity: 0.5; font-size: 0.8em;">${rule.last_line_received_at ? formatRelativeTime(rule.last_line_received_at) : ''}</span></span>'''

if old_render in content:
    content = content.replace(old_render, new_render, 1)
    print('1. Initial render updated')
else:
    print('1. WARNING: initial render pattern not found!')

# 2. Update the polling update for last-received to also update the ago span
old_update = "if (lastReceivedEl) lastReceivedEl.textContent = buf.last_line_received_at ? formatDate(buf.last_line_received_at) : '\u2014';"

new_update = """if (lastReceivedEl) lastReceivedEl.textContent = buf.last_line_received_at ? formatDate(buf.last_line_received_at) : '\u2014';
        const lastReceivedAgoEl = document.getElementById(`last-received-ago-${ruleId}`);
        if (lastReceivedAgoEl) lastReceivedAgoEl.textContent = buf.last_line_received_at ? formatRelativeTime(buf.last_line_received_at) : '';"""

if old_update in content:
    content = content.replace(old_update, new_update, 1)
    print('2. Polling update updated')
else:
    print('2. WARNING: polling update pattern not found!')

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print('Done')
