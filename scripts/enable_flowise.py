#!/usr/bin/env python3
"""Enable existing specialist rooms in Flowise, preserving their IDs and histories."""
import argparse
import copy
import json
import os
from pathlib import Path
import uuid


def valid_id(value):
    try:return str(uuid.UUID(value)) == value
    except (ValueError,TypeError,AttributeError):return False


def room_id(value):
    return isinstance(value,str) and value.startswith('!') and ':' in value and not any(c.isspace() for c in value)


def primary_routes(main,setup,current):
    if not isinstance(main,dict) or set(main.values()) != {'legal','finance'} or any(not room_id(r) for r in main):
        raise ValueError('Both legal and finance primary rooms required')
    pairs={d:(setup.get('flows',{}).get(d),setup.get('review_flows',{}).get(d)) for d in ('legal','finance')}
    if any(not valid_id(i) for pair in pairs.values() for i in pair) or len({i for pair in pairs.values() for i in pair})!=4:
        raise ValueError('Four distinct configured draft/review flows required')
    if not isinstance(current,dict):raise ValueError('Invalid current routes')
    for r,entry in current.items():
        if not room_id(r) or not isinstance(entry,dict) or entry.get('domain') not in pairs or not valid_id(entry.get('flow_id')):
            raise ValueError('Invalid existing Flowise route')
        if 'review_flow_id' in entry and not valid_id(entry['review_flow_id']):raise ValueError('Invalid existing review flow')
        if r in main and entry['domain']!=main[r]:raise ValueError('Primary room domain conflict')
    result=copy.deepcopy(current)
    for room,domain in main.items():
        draft,review=pairs[domain]
        result[room]={'domain':domain,'flow_id':draft,'review_flow_id':review}
    return result


def write_json(path,value,*,exclusive=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    if exclusive and path.exists():raise ValueError('Backup already exists; choose another path')
    temporary=path.with_name(path.name+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as file:json.dump(value,file,ensure_ascii=False,indent=2)
    os.replace(temporary,path)


def enable(routes_path,main_path,setup_path,backup_path):
    before=json.loads(routes_path.read_text())
    main=json.loads(main_path.read_text());setup=json.loads(setup_path.read_text())
    after=primary_routes(main,setup,before)
    # Do not overwrite operator edits made while this command was preparing its result.
    if json.loads(routes_path.read_text())!=before or json.loads(main_path.read_text())!=main or json.loads(setup_path.read_text())!=setup:
        raise ValueError('Configuration changed while preparing routes')
    if after==before:return False
    write_json(backup_path,{'version':1,'before':before,'after':after},exclusive=True)
    write_json(routes_path,after)
    return True


def rollback(routes_path,backup_path):
    backup=json.loads(backup_path.read_text())
    if backup.get('version')!=1:raise ValueError('Unknown backup version')
    current=json.loads(routes_path.read_text())
    if current==backup['before']:return False
    if current!=backup['after']:raise ValueError('Routes changed independently; rollback refused')
    write_json(routes_path,backup['before']);return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',default='/app/state')
    parser.add_argument('--main-routes',help='Defaults to state-dir/room_routes.json')
    parser.add_argument('--backup-file',help='Required for apply/rollback; keep outside Git')
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--apply',action='store_true');mode.add_argument('--rollback',action='store_true')
    args=parser.parse_args();state=Path(args.state_dir);routes=state/'flowise_rooms.json'
    if (args.apply or args.rollback) and not args.backup_file:parser.error('--backup-file is required')
    if args.rollback:
        changed=rollback(routes,Path(args.backup_file));print('Routes rolled back' if changed else 'Already rolled back');return
    main_path=Path(args.main_routes) if args.main_routes else state/'room_routes.json'
    primary=json.loads(main_path.read_text());setup=state/'flowise_setup.json'
    after=primary_routes(primary,json.loads(setup.read_text()),json.loads(routes.read_text()))
    if args.apply:
        changed=enable(routes,main_path,setup,Path(args.backup_file));print('Primary Flowise rooms enabled' if changed else 'Already enabled')
    else:print('Preview only; primary rooms:',len(primary),'; total Flowise rooms:',len(after))
    print('Restart only bot after applying. No Matrix rooms or messages are created.')

if __name__=='__main__':main()
