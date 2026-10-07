"""Safe Matrix rich text with compact citations and a complete text fallback."""
import html
import re
from urllib.parse import urlsplit

CARD = re.compile(r'^• \[([^\]]+)\] (.*?) \(проверен ([^;]+); редакция/версия: (.*?); источник: (.*?)\)$')

def safe_link(url, label):
    u = urlsplit(url)
    if u.scheme not in {'http', 'https'} or not u.netloc:
        return html.escape(label)
    return '<a href="' + html.escape(url, quote=True) + '">' + html.escape(label) + '</a>'

def inline(text, numbers):
    # Escape first; the only tags come from our own restricted renderer.
    text = html.escape(text)
    text = re.sub(r'\[([^\[\]\n]+)\]\(([^\s)]+)\)',
                  lambda m: safe_link(html.unescape(m[2]), html.unescape(m[1])), text)
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*([^*\n]+)\*\*', r'<strong>\1</strong>', text)
    for identifier, number in numbers.items():
        text = text.replace('[' + html.escape(identifier) + ']', '<sup>[' + str(number) + ']</sup>')
    return text

def blocks(text, numbers):
    result, paragraph, items, kind = [], [], [], None
    def flush():
        if paragraph:
            result.append('<p>' + '<br>'.join(paragraph) + '</p>'); paragraph.clear()
        if items:
            result.append('<' + kind + '>' + ''.join('<li>' + x + '</li>' for x in items) + '</' + kind + '>'); items.clear()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            # Blank lines separate paragraphs, but do not restart a list.
            if paragraph: flush()
            continue
        match = re.match(r'^(?:(\d+)[.)]|[-*•])\s+(.+)$', line)
        if match:
            wanted = 'ol' if match[1] else 'ul'
            if paragraph or (items and wanted != kind): flush()
            kind = wanted; items.append(inline(match[2], numbers)); continue
        if items and raw[:1].isspace():
            items[-1] += '<br>' + inline(line, numbers)
            continue
        if items: flush()
        heading = re.match(r'^#{1,6}\s+(.+)$', line)
        if heading:
            flush(); result.append('<p><strong>' + inline(heading[1], numbers) + '</strong></p>')
        else: paragraph.append(inline(line, numbers))
    flush()
    return ''.join(result)

def plain(text, numbers):
    for identifier, number in numbers.items():
        text = text.replace('[' + identifier + ']', '[' + str(number) + ']')
    text = re.sub(r'\*\*([^*\n]+)\*\*', r'\1', text)
    text = re.sub(r'`([^`\n]+)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.M)
    # Plain-text clients should see the same numbering as the HTML <ol>.
    lines, number = [], 0
    for raw in text.splitlines():
        match = re.match(r'^\s*\d+[.)]\s+(.+)$', raw)
        if match:
            number += 1; lines.append(str(number) + '. ' + match[1])
        else:
            if raw.strip() and not raw[:1].isspace(): number = 0
            lines.append(raw)
    return '\n'.join(lines)

def matrix_content(text):
    lines = text.splitlines(); cards = []; remaining = []; i = 0
    while i < len(lines):
        match = CARD.match(lines[i].strip())
        if match:
            identifier, title, checked, version, source = match.groups()
            page = re.search(r', стр\. (\d+)$', title)
            if page: title = title[:page.start()]
            quote = ''
            if i + 1 < len(lines) and lines[i+1].strip().startswith('Цитата из документа:'):
                i += 1; quote = lines[i].strip().split(':', 1)[1].strip()
            cards.append({'id':identifier,'title':title,'page':page[1] if page else '', 'checked':checked,'version':version,'source':source,'quote':quote})
        elif lines[i].strip() != 'Локальные материалы для проверки:':
            remaining.append(lines[i])
        i += 1
    numbers = {card['id']:n for n,card in enumerate(cards,1)}
    core = '\n'.join(remaining).strip()
    # Keep operational attribution unobtrusive and shorten the repetitive handoff footer.
    core = re.sub(r'Ответ подготовил (ЮрАгент|ФинАгент) в Multica\.', r'Подготовлено: \1 · Multica.', core)
    core = re.sub(r'Если ответ не помог, напишите !человек[^\n]*', 'Нужен сотрудник? Напишите !человек, !юрдеп или !финдеп.', core)
    core = re.sub(r'^\*\*(Финансовый агент|Юридический агент)\*\*\n', r'**\1**\n\n', core)
    body = plain(core, numbers); rich = blocks(core, numbers)
    if cards:
        rich += '<p><strong>Источники</strong></p><ol>'
        fallback = ['\n\nИсточники']
        details = []
        for n, c in enumerate(cards,1):
            loc = ' — стр. ' + c['page'] if c['page'] else ''
            rich += '<li>' + safe_link(c['source'],c['title']) + html.escape(loc) + '</li>'
            meta = 'Проверено: ' + c['checked'] + '. Редакция/версия: ' + c['version'] + '. Фрагмент: ' + c['id'] + '.'
            details.append('<p><strong>[' + str(n) + '] ' + html.escape(c['title'] + loc) + '</strong><br>' + html.escape(meta) + '</p><blockquote>' + html.escape(c['quote']) + '</blockquote>')
            fallback.append(str(n) + '. ' + c['title'] + loc + '\n' + c['source'] + '\n' + meta + '\nЦитата из документа: ' + c['quote'])
        rich += '</ol><details><summary>Цитаты и сведения о документах</summary>' + ''.join(details) + '</details>'
        body += '\n\n'.join(fallback)
    return {'msgtype':'m.text','body':body,'format':'org.matrix.custom.html','formatted_body':rich}
