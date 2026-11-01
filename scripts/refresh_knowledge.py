#!/usr/bin/env python3
"""Run a bounded refresh batch; usable by cron or an explicitly connected operator."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'matrix_support_bot'))
sys.path.insert(0, str(ROOT))
from knowledge_refresh import refresh


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--knowledge', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--policy', type=Path)
    parser.add_argument('--documents', help='Comma-separated IDs for a bounded manual recheck, ignoring their last attempt time')
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text()) if args.policy else {}
    import signal
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(SystemExit('Refresh job deadline exceeded')))
    signal.alarm(900)
    result = refresh(args.knowledge, args.state, policy=policy, selected_ids=args.documents.split(',') if args.documents else None)
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result['status'] == 'checks_unavailable' else 0


if __name__ == '__main__':
    raise SystemExit(main())
