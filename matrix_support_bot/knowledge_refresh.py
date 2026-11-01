"""Bounded official-source checks with immutable artifacts and a durable schedule.

No document is activated and no review date is advanced by an HTTP check.
"""
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from evaluation_core import private_json
from knowledge_updates import check_source, download, inventory
from knowledge_catalog import check_catalog

DEFAULT_POLICY = {'schema_version': 1, 'batch_size': 20, 'timeout_seconds': 10,
                  'legislation_interval_days': 7, 'tariff_interval_days': 1,
                  'failure_retry_days': 1, 'priority_documents': []}
SUCCESS = {'snapshot_unchanged', 'page_unchanged', 'candidate_changed',
           'manual_page_comparison_required'}


def policy_values(value):
    result = dict(DEFAULT_POLICY, **value)
    for name, low, high in [('batch_size', 1, 30), ('timeout_seconds', 1, 30),
                           ('legislation_interval_days', 1, 30),
                           ('tariff_interval_days', 1, 7), ('failure_retry_days', 1, 7)]:
        if type(result[name]) is not int or not low <= result[name] <= high:
            raise ValueError('Invalid refresh policy: ' + name)
    if result['schema_version'] != 1:
        raise ValueError('Unknown refresh policy schema')
    priorities = result['priority_documents']
    if not isinstance(priorities, list) or len(priorities) > 30 or any(not isinstance(v, str) or not re.fullmatch(r'[a-z][a-z0-9-]{1,79}', v) for v in priorities):
        raise ValueError('Invalid priority documents')
    return result


def parsed_time(value):
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def select_due(items, latest, policy, now):
    due = []
    for item in items:
        if not re.fullmatch(r'[a-z][a-z0-9-]{1,79}', item['id']):
            raise ValueError('Invalid source identifier')
        prior = latest.get(item['id'], {})
        checked = parsed_time(prior.get('checked_at'))
        interval = policy['tariff_interval_days' if item.get('bank') else 'legislation_interval_days']
        if prior.get('status') == 'check_unavailable':
            interval = policy['failure_retry_days']
        # A new active entry must be checked again even if the previous edition was checked recently.
        if checked and checked <= now and prior.get('active_sha256') == item['sha256']:
            if now - checked < timedelta(days=interval):
                continue
        due.append((checked or datetime.min.replace(tzinfo=timezone.utc), 0 if item['id'] in policy.get('priority_documents', []) else 1, item['id'], item))
    return [v[3] for v in sorted(due)[:policy['batch_size']]], len(due)


def archive_artifact(run, archive, receipt):
    name = receipt.get('artifact_file')
    if not name:
        return None
    source = (run / name).resolve()
    if not source.is_relative_to(run.resolve()) or not source.is_file():
        raise ValueError('Invalid staged artifact path')
    data = source.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if sha != receipt.get('artifact_sha256'):
        raise ValueError('Staged artifact hash mismatch')
    destination = archive / (sha + source.suffix)
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != sha:
            raise ValueError('Immutable archive hash mismatch')
    else:
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    # Keep each receipt usable for promotion without duplicating unchanged bytes.
    source.unlink()
    os.link(destination, source)
    return str(Path('artifacts') / destination.name)


def refresh(root, state, *, policy=None, now=None, fetch=None, selected_ids=None):
    root, state = Path(root).resolve(), Path(state).resolve()
    if state == root or state.is_relative_to(root):
        raise ValueError('Refresh state must be outside the active corpus')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Timezone-aware refresh time required')
    now = now.astimezone(timezone.utc)
    policy = policy_values(policy or {})
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / 'refresh.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'already_running', 'automatic_activation': False}
        audit = inventory(root, today=now.date())
        if any(not row['integrity'] for row in audit['documents']):
            private_json(state / 'inventory.json', audit)
            raise ValueError('Active corpus integrity failure; refresh aborted')
        data = json.loads((root / 'manifest.json').read_text())
        state_path = state / 'latest.json'
        latest = json.loads(state_path.read_text()).get('documents', {}) if state_path.exists() else {}
        if selected_ids is not None:
            requested = set(selected_ids)
            if not requested or len(requested) > policy['batch_size'] or requested - {d['id'] for d in data['documents']}:
                raise ValueError('Unknown or excessive manually selected source IDs')
            selected, due = select_due([d for d in data['documents'] if d['id'] in requested], {}, policy, now)
        else:
            selected, due = select_due(data['documents'], latest, policy, now)
        run_id = now.strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
        run = state / 'runs' / run_id
        run.mkdir(parents=True, mode=0o700)
        archive = state / 'artifacts'
        archive.mkdir(mode=0o700, exist_ok=True)
        private_json(run / 'active-manifest.json', data)
        rows = []
        catalog_cache = {}
        for item in selected:
            receipt = check_source(item, run, fetch=fetch or
                                   (lambda url: download(url, timeout=policy['timeout_seconds'])))
            receipt.update(check_catalog(item, archive, fetch=fetch or (lambda url: download(url, timeout=policy['timeout_seconds'])),
                                         previous=latest.get(item['id'], {}), cache=catalog_cache))
            receipt['checked_at'] = now.isoformat()
            receipt['archive_file'] = archive_artifact(run, archive, receipt)
            receipt['receipt_file'] = str(Path('runs') / run_id / (item['id'] + '.check.json'))
            private_json(run / (item['id'] + '.check.json'), receipt)
            rows.append(receipt)
            latest[item['id']] = {k: receipt.get(k) for k in
                ['checked_at', 'status', 'active_sha256', 'artifact_sha256', 'archive_file', 'receipt_file', 'error_type', 'http_status', 'catalog_status', 'catalog_sha256', 'catalog_archive_file', 'catalog_error_type', 'last_successful_catalog_sha256']}
            # Save after each source: a process interruption does not lose completed checks.
            private_json(state_path, {'schema_version': 1, 'documents': latest})
        counts = {status: sum(r['status'] == status for r in rows) for status in sorted({r['status'] for r in rows})}
        result = {'schema_version': 1, 'run_id': run_id, 'as_of': now.isoformat(),
                  'status': 'complete' if all(r['status'] in SUCCESS and r.get('catalog_status') != 'catalog_unavailable' for r in rows) else 'checks_unavailable',
                  'documents': len(data['documents']), 'selected': len(rows), 'due_before': due,
                  'due_remaining': max(0, due - len(rows)), 'counts': counts,
                  'inventory': audit['counts'], 'catalog_downloads': len(catalog_cache),
                  'catalog_status_counts': {s: sum(r.get('catalog_status') == s for r in rows) for s in sorted({r['catalog_status'] for r in rows if r.get('catalog_status')})},
                  'automatic_activation': False,
                  'review_dates_changed': False, 'legal_currency_certified': False,
                  'latest_status_counts': {status: sum(r.get('status') == status for r in latest.values()) for status in sorted({r.get('status', 'unknown') for r in latest.values()})},
                  'never_checked': sum(d['id'] not in latest for d in data['documents']),
                  'policy': policy}
        private_json(run / 'summary.json', result)
        private_json(state / 'summary.json', result)
        private_json(state / 'inventory.json', audit)
        return result
