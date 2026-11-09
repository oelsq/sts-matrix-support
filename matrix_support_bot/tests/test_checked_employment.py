from checked_employment import employment_comparison

TEXT=('Отличительным признаком трудового договора от иных видов договоров является наличие в нем одного из следующих условий: '
      '1) выполнения работником работы по определенной квалификации, специальности, профессии или должности; '
      '2) выполнения обязательств лично с подчинением трудовому распорядку; '
      '3) получения работником заработной платы за труд.')
SOURCE={'id':'labor:synthetic','document_id':'labor','domains':['legal','finance'],'section':'Статья 27. Отличие трудового договора от иных видов договоров','text':TEXT,'source':'https://adilet.zan.kz/rus/docs/K1500000414','title':'Трудовой кодекс','reviewed_on':'2026-11-01','version':'2026-01-01','page':1}

def test_comparison_preserves_one_of_conditions_not_all_and_uses_exact_source():
    answer=employment_comparison('legal','Чем трудовой договор отличается от договора услуг?',[SOURCE])
    assert answer and 'одного из перечисленных' in answer and 'трудовому распорядку' in answer
    assert 'одновременно' not in answer and 'https://adilet.zan.kz/rus/docs/K1500000414' in answer


def test_comparison_requires_the_correct_article_and_all_three_source_conditions():
    for source in [dict(SOURCE,section='Статья 26.'),dict(SOURCE,document_id='unrelated'),dict(SOURCE,text=TEXT.replace('одного из следующих условий','всех условий')),dict(SOURCE,text=TEXT.replace('заработной платы за труд','оплаты'))]:
        assert employment_comparison('legal','Чем трудовой договор отличается от договора услуг?',[source]) is None


def test_individual_legal_classification_and_document_are_not_replaced_by_definition():
    for question in ['Могу ли я признать договор услуг трудовым через суд?','Сравни трудовой договор и ГПХ и напиши заявление','Меня увольняют по договору услуг, могу ли взыскать зарплату?']:
        assert employment_comparison('legal',question,[SOURCE]) is None
