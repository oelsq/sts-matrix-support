from message_format import matrix_content

def test_verified_source_compact_view_preserves_quote_and_metadata():
    text='**Финансовый агент**\nКомиссия 0 тенге [bank:p1:5:1].\n\nЛокальные материалы для проверки:\n• [bank:p1:5:1] Тарифы Halyk, стр. 1 (проверен 2026-10-01; редакция/версия: 15.09.2026; источник: https://example.org/tariff.pdf)\n  Цитата из документа: «Оплата коммунальных услуг 0 тенге»\n\nОтвет подготовил ФинАгент в Multica.'
    c=matrix_content(text)
    assert c['format']=='org.matrix.custom.html'
    assert '<strong>Финансовый агент</strong>' in c['formatted_body']
    assert '<sup>[1]</sup>' in c['formatted_body']
    assert '<details><summary>Цитаты и сведения о документах</summary>' in c['formatted_body']
    for value in ['Оплата коммунальных услуг 0 тенге','2026-10-01','15.09.2026','bank:p1:5:1']:
        assert value in c['body'] and value in c['formatted_body']
    assert '**' not in c['body']

def test_untrusted_html_and_dangerous_links_cannot_become_active():
    text='**Ответ**\n<script>alert(1)</script>\n• [x] Документ (проверен сегодня; редакция/версия: нет; источник: javascript:alert(1))\nЦитата из документа: «<img src=x onerror=alert(1)>»'
    rich=matrix_content(text)['formatted_body']
    assert '<script>' not in rich and '<img' not in rich
    assert 'href="javascript:' not in rich and 'onerror=' not in rich.replace('&lt;img src=x onerror=alert(1)&gt;','')
    assert '&lt;script&gt;' in rich

def test_lists_and_paragraphs_plain_fallback_and_unknown_markers():
    c=matrix_content('**Что делать**\n\n1. Проверьте договор.\n2. Напишите сотруднику.\n\nНеизвестный источник [unknown].')
    assert '<ol><li>' in c['formatted_body'] and '</li><li>' in c['formatted_body']
    assert '[unknown]' in c['formatted_body']
    assert '1. Проверьте договор.' in c['body']
    assert '<details>' not in c['formatted_body']


def test_blank_lines_keep_numbered_steps_in_one_list():
    from html.parser import HTMLParser
    class Lists(HTMLParser):
        def __init__(self):
            super().__init__(); self.lists = []; self.current = None
        def handle_starttag(self, tag, attrs):
            if tag == 'ol': self.current = []; self.lists.append(self.current)
            if tag == 'li': self.current.append('')
        def handle_data(self, data):
            if self.current is not None and self.current: self.current[-1] += data
        def handle_endtag(self, tag):
            if tag == 'ol': self.current = None
    content = matrix_content('1. Первый шаг.\n\n1. Второй шаг.\n\n1. Третий шаг.')
    parser = Lists(); parser.feed(content['formatted_body'])
    assert parser.lists == [['Первый шаг.', 'Второй шаг.', 'Третий шаг.']]

def test_paragraph_still_separates_distinct_lists():
    rich = matrix_content('1. Первый шаг.\n\n2. Второй шаг.\n\nОтдельная часть.\n\n1. Другой шаг.')['formatted_body']
    assert rich.count('<ol>') == 2
    assert '</ol><p>Отдельная часть.</p><ol>' in rich
