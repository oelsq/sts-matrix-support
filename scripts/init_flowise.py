#!/usr/bin/env python3
"""Generate independent local Flowise database passwords; never reads existing secrets."""
import os
import secrets
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1] / 'runtime' / 'flowise'
    root.mkdir(parents=True, exist_ok=True)
    paths = [root / 'database.env', root / 'server.env']
    if any(p.exists() for p in paths):
        if not all(p.exists() for p in paths):
            raise RuntimeError('Partial Flowise configuration: restore the missing file; passwords were not changed')
        print('Existing Flowise database settings preserved.')
        return
    password = secrets.token_urlsafe(36)
    for path, content in zip(paths, ['POSTGRES_USER=flowise\nPOSTGRES_DB=flowise\nPOSTGRES_PASSWORD=' + password + '\n',
                                    'DATABASE_PASSWORD=' + password + '\n']):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as file:
            file.write(content)
    print('Flowise database settings created in runtime/flowise (not tracked by Git).')


if __name__ == '__main__':
    main()
