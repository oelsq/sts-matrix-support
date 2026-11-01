#!/usr/bin/env python3
"""Install one project-scoped cron entry, preserving unrelated jobs verbatim."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

BEGIN = '# BEGIN sts-matrix-support knowledge refresh'
END = '# END sts-matrix-support knowledge refresh'


def cron_text(existing, runner, log):
    if existing.count(BEGIN) != existing.count(END) or existing.count(BEGIN) > 1:
        raise ValueError('Ambiguous managed cron block; inspect before changing')
    if BEGIN in existing:
        start, stop = existing.index(BEGIN), existing.index(END) + len(END)
        if start and existing[start - 1] != '\n':
            raise ValueError('Invalid managed block boundary')
        existing = existing[:start] + existing[stop:].lstrip('\n')
    # Cron interprets % even inside shell quotes. Newlines can create extra jobs.
    if any(c in str(runner) + str(log) for c in '%\n\r'):
        raise ValueError('Invalid cron path')
    block = BEGIN + '\n17 */6 * * * ' + shlex.quote(str(runner)) + ' > ' + shlex.quote(str(log)) + ' 2>&1\n' + END + '\n'
    return existing.rstrip('\n') + ('\n' if existing else '') + block


def runner_text(root, bot):
    if not bot or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in bot):
        raise ValueError('Invalid bot container name')
    q = shlex.quote
    state = root / 'maintenance' / 'knowledge-refresh'
    return '\n'.join(['#!/bin/sh', 'set -eu', 'umask 077',
        'exec 9>' + q(str(state / 'launcher.lock')), 'flock -n 9 || exit 0',
        'image=$(docker inspect --format \'{{.Image}}\' ' + q(bot) + ')',
        'exec docker run --rm --pull never --network bridge --cpus 1 --memory 384m --pids-limit 128 --user "$(id -u):$(id -g)" ' +
        '-v ' + q(str(root / 'project' / 'offline_knowledge') + ':/knowledge:ro') + ' ' +
        '-v ' + q(str(state) + ':/refresh') + ' ' +
        '-v ' + q(str(root / 'project' / 'config' / 'knowledge-refresh-policy.json') + ':/refresh-policy.json:ro') + ' ' +
        '"$image" python /app/tools/refresh_knowledge.py --knowledge /knowledge --state /refresh --policy /refresh-policy.json', ''])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--bot', default='sts-team-demo-bot-1')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    if not (root / 'project' / 'offline_knowledge' / 'manifest.json').is_file():
        parser.error('Expected deployed project under root/project')
    old = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    if old.returncode and 'no crontab' not in old.stderr.lower():
        raise RuntimeError('Cannot read crontab')
    state = root / 'maintenance' / 'knowledge-refresh'
    runner = root / 'maintenance' / 'run-knowledge-refresh.sh'
    updated = cron_text(old.stdout, runner, state / 'latest-run.log')
    script = runner_text(root, args.bot)
    if args.apply:
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        runner.write_text(script)
        runner.chmod(0o700)
        subprocess.run(['crontab', '-'], input=updated, text=True, check=True)
        installed = subprocess.check_output(['crontab', '-l'], text=True)
        if installed != updated:
            raise RuntimeError('Installed schedule verification failed')
    print(json.dumps({'installed': args.apply, 'schedule': '17 */6 * * *',
        'timezone': 'server cron timezone', 'runner': str(runner),
        'unrelated_jobs_preserved': True, 'automatic_activation': False}))


if __name__ == '__main__':
    main()
