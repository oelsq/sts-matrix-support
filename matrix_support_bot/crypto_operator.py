"""Local, manual E2EE operator actions. No tokens or keys are displayed."""
import argparse
import json
import os
import stat
from pathlib import Path
from matrix_crypto import private_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',required=True,help='Bot crypto directory, e.g. /app/state/crypto')
    commands=parser.add_subparsers(dest='action',required=True)
    commands.add_parser('status')
    sub=commands.add_parser('request');sub.add_argument('user_id');sub.add_argument('device_id')
    for name in ['match','reject']:
        sub=commands.add_parser(name);sub.add_argument('transaction_id')
    sub=commands.add_parser('invite');sub.add_argument('user_id');sub.add_argument('--domain',choices=['legal','finance','both'],default='both')
    sub=commands.add_parser('fingerprint');sub.add_argument('user_id');sub.add_argument('device_id');sub.add_argument('public_key')
    sub.add_argument('--compared-on-device',action='store_true',required=True,
                     help='Use only after the person compared this public key with their actual Element session')
    args=parser.parse_args();root=Path(args.state)
    if args.action=='status':
        for name in ['identity.json','verification-status.json','room-status.json']:
            path=root/name
            if path.exists():
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
                try:
                    info=os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077 or info.st_size>262144:raise ValueError('Invalid status file')
                    print(name+':\n'+os.read(fd,262144).decode())
                finally:os.close(fd)
        return
    if args.action=='fingerprint':
        from matrix_verification import public_fingerprint
        if not args.user_id.startswith('@') or ':' not in args.user_id:parser.error('Complete Matrix user ID required')
        try:key=public_fingerprint(args.public_key)
        except ValueError:parser.error('Use the public Ed25519 fingerprint, not a recovery key')
        actions=[{'user_id':args.user_id,'device_id':args.device_id,'public_fingerprint':key}]
        path=root/'verification-fingerprints.json'
    elif args.action=='request':
        if not args.user_id.startswith('@') or not 1<=len(args.device_id)<=256:parser.error('Complete user and device IDs required')
        actions=[{'user_id':args.user_id,'device_id':args.device_id}];path=root/'verification-requests.json'
    elif args.action=='invite':
        if not args.user_id.startswith('@') or ':' not in args.user_id:parser.error('Use a complete Matrix user ID')
        actions=[{'user_id':args.user_id,'domain':d} for d in (['legal','finance'] if args.domain=='both' else [args.domain])]
        path=root/'room-actions.json'
    else:
        if not 1<=len(args.transaction_id)<=256:parser.error('Invalid transaction ID')
        actions=[{'transaction_id':args.transaction_id,'decision':args.action}];path=root/'verification-actions.json'
    if path.exists():parser.error('Previous operator action is still pending')
    private_json(path,actions)
    print('Operator action queued. Match is valid only after comparing all seven emoji in Element.')


if __name__=='__main__':main()
