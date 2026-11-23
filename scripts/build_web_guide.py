"""Render the bundled beginner guide from our limited Markdown source (no runtime dependency)."""
from html import escape
from pathlib import Path
import re

root = Path(__file__).resolve().parent.parent
source = (root/'docs/WEB_BEGINNER.md').read_text()

def inline(text):
    text = escape(text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\[([^]]+)\]\(([^)]+)\)',r'<a href="\2">\1</a>',text)
    return text.replace('href="INPUTS.md"','href="https://github.com/cmjlabs/epitope-screening/blob/main/docs/INPUTS.md"')

parts=[]
list_type=None
table=False
for line in source.splitlines()+['']:
    numbered=re.match(r'^\d+\. (.+)',line)
    bullet=line.startswith('- ')
    current='ol' if numbered else 'ul' if bullet else None
    if list_type and current!=list_type:
        parts.append('</'+list_type+'>'); list_type=None
    if table and not line.startswith('|'):
        parts.append('</tbody></table></div>');table=False
    if not line:
        continue
    if current:
        if not list_type: parts.append('<'+current+'>');list_type=current
        parts.append('<li>'+inline(numbered[1] if numbered else line[2:])+'</li>')
    elif line.startswith('|'):
        if re.match(r'^\|[\s|:-]+$',line): continue
        cells=[inline(cell.strip()) for cell in line.strip('|').split('|')]
        if not table:
            parts.append('<div class="table-scroll"><table><thead><tr>'+''.join('<th>'+c+'</th>' for c in cells)+'</tr></thead><tbody>');table=True
        else: parts.append('<tr>'+''.join('<td>'+c+'</td>' for c in cells)+'</tr>')
    elif line.startswith('## '):parts.append('<h2>'+inline(line[3:])+'</h2>')
    elif line.startswith('# '):parts.append('<h1>'+inline(line[2:])+'</h1>')
    else:parts.append('<p>'+inline(line)+'</p>')
output='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>表位筛选 · 完整新手说明</title><link rel="stylesheet" href="style.css"></head><body><main><article class="card guide-content">'''+''.join(parts)+'</article></main></body></html>'
(root/'epitope_screening/web/guide.html').write_text(output)
