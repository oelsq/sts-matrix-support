import hashlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from knowledge_refresh import refresh, select_due, DEFAULT_POLICY, policy_values

NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


def corpus(tmp_path, **extra):
    root = tmp_path / 'knowledge'
    root.mkdir()
    artifact = root / 'law.pdf'
    artifact.write_bytes(b'%PDF-original')
    item = dict(id='test-law', file='law.pdf', title='Test law', source='https://www.gov.kz/test',
                sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(), reviewed_on='2026-09-01', **extra)
    (root / 'manifest.json').write_text(json.dumps({'documents': [item]}))
    return root, item


def test_refresh_preserves_active_edition_and_review_date(tmp_path):
    root, item = corpus(tmp_path)
    original = (root / 'manifest.json').read_bytes()
    state = tmp_path / 'refresh'
    r = refresh(root, state, now=NOW, fetch=lambda _: b'%PDF-new-edition')
    assert r['counts'] == {'candidate_changed': 1}
    assert r['inventory']['review_due'] == 1
    assert not r['automatic_activation'] and not r['review_dates_changed']
    assert (root / 'manifest.json').read_bytes() == original
    assert (root / item['file']).read_bytes() == b'%PDF-original'
    assert len(list((state / 'artifacts').iterdir())) == 1
    assert r['due_remaining'] == 0


def test_retains_all_editions_and_receipts(tmp_path):
    root, _ = corpus(tmp_path)
    state = tmp_path / 'refresh'
    r1 = refresh(root, state, now=NOW, fetch=lambda _: b'%PDF-edition-one')
    r2 = refresh(root, state, now=NOW + timedelta(days=8), fetch=lambda _: b'%PDF-edition-two')
    assert len(list((state / 'artifacts').iterdir())) == 2
    assert len(list((state / 'runs').glob('*/test-law.check.json'))) == 2
    assert r1['run_id'] != r2['run_id']


def test_unchanged_reuses_content_archive_but_retains_checks(tmp_path):
    root, _ = corpus(tmp_path)
    state = tmp_path / 'refresh'
    for offset in [0, 8]:
        refresh(root, state, now=NOW + timedelta(days=offset), fetch=lambda _: b'%PDF-original')
    assert len(list((state / 'artifacts').iterdir())) == 1
    assert len(list((state / 'runs').glob('*/summary.json'))) == 2


def test_unavailable_is_not_unchanged_or_reviewed(tmp_path):
    root, _ = corpus(tmp_path)
    def unavailable(_):
        raise TimeoutError('offline')
    state = tmp_path / 'refresh'
    result = refresh(root, state, now=NOW, fetch=unavailable)
    assert result['status'] == 'checks_unavailable'
    assert result['counts'] == {'check_unavailable': 1}
    assert not list((state / 'artifacts').iterdir())
    assert refresh(root, state, now=NOW + timedelta(hours=6), fetch=unavailable)['selected'] == 0
    assert refresh(root, state, now=NOW + timedelta(days=1), fetch=unavailable)['selected'] == 1


def test_html_comparison_never_certifies_a_new_legal_edition(tmp_path):
    root, _ = corpus(tmp_path)
    result = refresh(root, tmp_path / 'refresh', now=NOW, fetch=lambda _: b'<html>widget</html>')
    assert result['counts'] == {'manual_page_comparison_required': 1}
    assert result['legal_currency_certified'] is False


def test_daily_tariffs_and_weekly_law_checks():
    items = [dict(id='law-a', sha256='a', source='https://www.gov.kz/a'),
             dict(id='bank-a', sha256='b', bank='test', source='https://halykbank.kz/a')]
    prior = {d['id']: dict(checked_at=(NOW-timedelta(days=2)).isoformat(), active_sha256=d['sha256'], status='snapshot_unchanged') for d in items}
    selected, count = select_due(items, prior, DEFAULT_POLICY, NOW)
    assert [d['id'] for d in selected] == ['bank-a'] and count == 1
    prior['law-a']['active_sha256'] = 'old'
    assert len(select_due(items, prior, DEFAULT_POLICY, NOW)[0]) == 2


def test_oldest_attempts_not_id_order_receive_priority():
    items = [dict(id=f'law-{i}', sha256='a', source='https://www.gov.kz/a') for i in range(3)]
    prior = {'law-0': dict(checked_at=(NOW-timedelta(days=10)).isoformat(), status='check_unavailable', active_sha256='a')}
    selected, count = select_due(items, prior, dict(DEFAULT_POLICY, batch_size=1), NOW)
    assert selected[0]['id'] == 'law-1' and count == 3


def test_tampered_corpus_stops_before_network(tmp_path):
    root, _ = corpus(tmp_path)
    (root/'law.pdf').write_bytes(b'changed')
    with pytest.raises(ValueError, match='integrity'):
        refresh(root, tmp_path/'refresh', now=NOW, fetch=lambda _: pytest.fail('must not fetch'))


def test_cannot_write_update_state_in_active_corpus(tmp_path):
    root, _ = corpus(tmp_path)
    with pytest.raises(ValueError, match='outside'):
        refresh(root, root/'updates', now=NOW)


@pytest.mark.parametrize('policy', [{'batch_size':0}, {'timeout_seconds':31}, {'batch_size':True}, {'tariff_interval_days':8}])
def test_policy_bounds(policy):
    with pytest.raises(ValueError):
        policy_values(policy)


def test_concurrent_job_skips_without_partial_run(tmp_path):
    import fcntl
    root, _ = corpus(tmp_path)
    state = tmp_path/'refresh'
    state.mkdir()
    with (state/'refresh.lock').open('w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        assert refresh(root, state, now=NOW)['status'] == 'already_running'
    assert not (state/'runs').exists()


def cron_module():
    script = Path(__file__).resolve().parents[2]/'scripts/install_knowledge_refresh.py'
    spec = importlib.util.spec_from_file_location('cron_install', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cron_install_is_idempotent_preserves_other_jobs():
    m = cron_module()
    original = 'MAILTO=ops@example.test\n30 4 * * * /srv/other-job\n'
    once = m.cron_text(original, Path('/srv/sts/run.sh'), Path('/srv/sts/log'))
    twice = m.cron_text(once, Path('/srv/sts/run.sh'), Path('/srv/sts/log'))
    assert once == twice and once.startswith(original)
    assert once.count(m.BEGIN) == 1
    with pytest.raises(ValueError):
        m.cron_text(m.BEGIN, Path('/srv/sts/run.sh'), Path('/srv/sts/log'))
    with pytest.raises(ValueError):
        m.cron_text('', Path('/srv/sts/%run.sh'), Path('/srv/sts/log'))


def test_scheduler_does_not_mount_credentials_or_use_admin_apis():
    text = cron_module().runner_text(Path('/srv/sts'), 'sts-bot')
    assert '--user' in text and '--memory 384m' in text and '--cpus 1' in text
    assert 'secrets' not in text and 'token' not in text and 'admin/v1' not in text
    assert ':/knowledge:ro' in text and '--pull never' in text


def test_single_source_checker_never_overwrites_a_previous_snapshot(tmp_path):
    from knowledge_updates import check_source
    item=dict(id='law-a', source='https://www.gov.kz/a', sha256='old')
    receipt=check_source(item, tmp_path, fetch=lambda _: b'%PDF-first')
    original=(tmp_path/receipt['artifact_file']).read_bytes()
    with pytest.raises(ValueError,match='fresh staging'):
        check_source(item, tmp_path, fetch=lambda _: pytest.fail('previous check must be preserved'))
    assert (tmp_path/receipt['artifact_file']).read_bytes() == original


def test_initial_critical_sources_get_priority_without_starving_unchecked():
    items=[dict(id=x,sha256='a',source='https://www.gov.kz/a') for x in ['aaa-law','tax']]
    p=dict(DEFAULT_POLICY,batch_size=1,priority_documents=['tax'])
    assert select_due(items,{},p,NOW)[0][0]['id']=='tax'
    latest={'tax':dict(checked_at=(NOW-timedelta(days=8)).isoformat(),active_sha256='a',status='check_unavailable')}
    assert select_due(items,latest,p,NOW)[0][0]['id']=='aaa-law'


def test_unapproved_source_is_a_reported_failure_not_a_global_schedule_crash(tmp_path):
    root,item=corpus(tmp_path)
    item['source']='https://evil.example/not-official'
    (root/'manifest.json').write_text(json.dumps({'documents':[item]}))
    result=refresh(root,tmp_path/'refresh',now=NOW,fetch=lambda _:pytest.fail('blocked destination must not be fetched'))
    assert result['counts']=={'check_unavailable':1}


def test_archived_artifacts_use_hard_links_for_repeated_identical_downloads(tmp_path):
    root,_=corpus(tmp_path)
    state=tmp_path/'refresh'
    for d in [0,8]:refresh(root,state,now=NOW+timedelta(days=d),fetch=lambda _:b'%PDF-original')
    archive=next((state/'artifacts').iterdir())
    snapshots=list((state/'runs').glob('*/test-law.pdf'))
    assert len(snapshots)==2 and all(p.stat().st_ino==archive.stat().st_ino for p in snapshots)


@pytest.mark.parametrize('url', ['https://halykbank.kz/Тариф 2026.pdf', 'https://halykbank.kz/a%20b.pdf?язык=русский'])
def test_official_urls_encode_non_ascii_and_spaces_without_double_encoding(url):
    from knowledge_updates import request_url,validate_url
    result=request_url(url)
    assert result.isascii() and ' ' not in result and '%2520' not in result
    assert validate_url(result)==result
    assert '%D0' in result or '%D1' in result


def test_manual_recheck_respects_whitelist_and_retains_previous_attempt(tmp_path):
    root,_=corpus(tmp_path)
    state=tmp_path/'refresh'
    refresh(root,state,now=NOW,fetch=lambda _:b'%PDF-original')
    result=refresh(root,state,now=NOW,selected_ids=['test-law'],fetch=lambda _:b'%PDF-new')
    assert result['selected']==1 and len(list((state/'runs').glob('*/summary.json')))==2
    with pytest.raises(ValueError,match='Unknown'):
        refresh(root,state,now=NOW,selected_ids=['missing'],fetch=lambda _:pytest.fail('must not fetch'))


def test_catalog_observes_new_urls_without_activating_or_calling_them_new_terms(tmp_path):
    from knowledge_catalog import check_catalog
    item=dict(source='https://halykbank.kz/old.pdf',catalog_url='https://halykbank.kz/tariffs')
    first=check_catalog(item,tmp_path,fetch=lambda _:b'<a href="/old.pdf">Old</a>')
    assert first['catalog_status']=='catalog_baseline_requires_review'
    assert first['source_pdf_link_observed'] is True
    changed=check_catalog(item,tmp_path,previous=first,fetch=lambda _:b'<a href="/new.pdf">New</a>')
    assert changed['catalog_status']=='catalog_changed_review_required'
    assert changed['source_pdf_link_observed'] is False
    assert len(list(tmp_path.glob('*.html')))==2
    unchanged=check_catalog(item,tmp_path,previous=changed,fetch=lambda _:b'<a href="/new.pdf">New</a>')
    assert unchanged['catalog_status']=='catalog_page_unchanged'


def test_shared_catalog_download_is_cached_only_within_one_batch(tmp_path):
    from knowledge_catalog import check_catalog
    calls=[]
    def fetch(url):calls.append(url);return b'<a href="/a.pdf">A</a>'
    cache={}
    for filename in ['a','b']:
        check_catalog(dict(source='https://halykbank.kz/'+filename+'.pdf',catalog_url='https://halykbank.kz/tariffs'),tmp_path,fetch=fetch,cache=cache)
    assert calls==['https://halykbank.kz/tariffs']


def test_catalog_download_failure_remains_visible(tmp_path):
    from knowledge_catalog import check_catalog
    def fail(_):raise TimeoutError()
    record=check_catalog(dict(source='https://halykbank.kz/a.pdf',catalog_url='https://halykbank.kz/tariffs'),tmp_path,fetch=fail)
    assert record['catalog_status']=='catalog_unavailable' and 'catalog_sha256' not in record


def test_catalog_cannot_fetch_unapproved_urls(tmp_path):
    from knowledge_catalog import check_catalog
    result=check_catalog(dict(source='https://halykbank.kz/a.pdf',catalog_url='https://evil.example/'),tmp_path,fetch=lambda _:pytest.fail('must not fetch'))
    assert result['catalog_status']=='catalog_unavailable'


def test_catalog_comparison_survives_an_intermediate_network_failure(tmp_path):
    from knowledge_catalog import check_catalog
    item=dict(source='https://halykbank.kz/a.pdf',catalog_url='https://halykbank.kz/tariffs')
    first=check_catalog(item,tmp_path,fetch=lambda _:b'<a href="/a.pdf">A</a>')
    def fail(_):raise TimeoutError()
    failed=check_catalog(item,tmp_path,previous=first,fetch=fail)
    changed=check_catalog(item,tmp_path,previous=failed,fetch=lambda _:b'<a href="/b.pdf">B</a>')
    assert changed['catalog_status']=='catalog_changed_review_required'
    assert changed['previous_catalog_sha256']==first['catalog_sha256']
