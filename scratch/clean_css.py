import re

with open('static/css/index.css', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove all the source-card/source-selector/source-icon/source-text blocks
content = re.sub(r'\n\.source-selector \{[^}]+\}', '', content)
content = re.sub(r'\n\.source-card \{[^}]+\}', '', content)
content = re.sub(r'\n\.source-card:hover \{[^}]+\}', '', content)
content = re.sub(r'\n\.source-card\.active \{[^}]+\}', '', content)
content = re.sub(r'\n\.source-icon \{[^}]+\}', '', content)
content = re.sub(r'\n\.source-text \{[^}]+\}', '', content)
content = re.sub(r'\n\.source-text strong \{[^}]+\}', '', content)
content = re.sub(r'\n\.source-text span \{[^}]+\}', '', content)

with open('static/css/index.css', 'w', encoding='utf-8') as f:
    f.write(content)

print('Cleaned source-card CSS')
