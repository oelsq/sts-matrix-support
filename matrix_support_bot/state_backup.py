"""Authenticated streaming backups of bot state, including its Matrix identity.

Backup requires the inbox owner to be stopped. Restore only into an empty
private directory; existing state is never overwritten by this command.
"""
import argparse
import fcntl
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import tarfile
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from state_cipher import StateCipher,StateEncryptionError,private_directory

MAGIC=b'STS-BACKUP-v1\n'
CHUNK=1024*1024


def backup_key(cipher):
    return hmac.digest(cipher._master,b'sts-state-backup/v1','sha256')


class EncryptWriter:
    def __init__(self,output,cipher):self.output,self.cipher=output,cipher
    def write(self,value):
        self.output.write(self.cipher.encrypt(value));return len(value)
    def flush(self):self.output.flush()


class DecryptReader(io.RawIOBase):
    def __init__(self,path,key):
        self.file=Path(path).open('rb')
        if self.file.read(len(MAGIC))!=MAGIC:raise StateEncryptionError('Invalid encrypted backup')
        nonce=self.file.read(12)
        self.remaining=Path(path).stat().st_size-len(MAGIC)-12-16
        if len(nonce)!=12 or self.remaining<0:raise StateEncryptionError('Truncated encrypted backup')
        self.cipher=AES.new(key,AES.MODE_GCM,nonce=nonce);self.cipher.update(MAGIC)
    def readable(self):return True
    def read(self,size=-1):
        amount=self.remaining if size<0 else min(size,self.remaining)
        if not amount:return b''
        value=self.file.read(amount)
        if len(value)!=amount:raise StateEncryptionError('Truncated encrypted backup')
        self.remaining-=len(value);return self.cipher.decrypt(value)
    def verify(self):
        while self.remaining:self.read(min(CHUNK,self.remaining))
        try:self.cipher.verify(self.file.read(16))
        except ValueError:raise StateEncryptionError('Backup authentication failed') from None
    def close(self):self.file.close();super().close()


def verify(path,cipher):
    reader=DecryptReader(path,backup_key(cipher))
    try:reader.verify()
    finally:reader.close()
    with Path(path).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    return {'authenticated':True,'sha256':digest,'key_id':cipher.key_id}


def backup(state,destination,cipher):
    state=Path(state).resolve();destination=Path(destination).resolve()
    if destination==state or state in destination.parents:raise ValueError('Backup must be outside state')
    if destination.exists():raise FileExistsError('Preserve the existing encrypted backup')
    private_directory(destination.parent)
    lock=open(state/'matrix_inbox.sqlite3.lock','a')
    try:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise RuntimeError('Stop the owning bot before taking a consistent backup') from None
        files=sorted(state.rglob('*'))
        if any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in files):raise ValueError('Unsafe state entry')
        temp=destination.with_suffix(destination.suffix+'.partial')
        fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        try:
            with os.fdopen(fd,'wb') as output:
                nonce=get_random_bytes(12);output.write(MAGIC+nonce)
                engine=AES.new(backup_key(cipher),AES.MODE_GCM,nonce=nonce);engine.update(MAGIC)
                writer=EncryptWriter(output,engine)
                with tarfile.open(fileobj=writer,mode='w|gz') as archive:
                    for path in files:
                        if path.is_file() and not path.name.endswith('.lock'):
                            archive.add(path,arcname=str(path.relative_to(state)),recursive=False)
                output.write(engine.digest());output.flush();os.fsync(output.fileno())
            receipt=verify(temp,cipher)
            os.replace(temp,destination)
            receipt.update(files=sum(p.is_file() and not p.name.endswith('.lock') for p in files))
            return receipt
        finally:temp.unlink(missing_ok=True)
    finally:lock.close()


def restore(source,target,cipher):
    # Authentication completes before any archived content is written.
    receipt=verify(source,cipher)
    target=Path(target)
    if target.is_symlink() or (target.exists() and any(target.iterdir())):raise ValueError('Restore target must be empty')
    target=private_directory(target).resolve()
    reader=DecryptReader(source,backup_key(cipher))
    try:
        with tarfile.open(fileobj=reader,mode='r|gz') as archive:
            for entry in archive:
                path=target/entry.name
                if not path.resolve().is_relative_to(target) or not entry.isfile():raise ValueError('Unsafe backup member')
                private_directory(path.parent)
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
                with os.fdopen(fd,'wb') as output,archive.extractfile(entry) as content:
                    while value:=content.read(CHUNK):output.write(value)
    finally:reader.close()
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['backup','verify','restore']);parser.add_argument('--key-file',required=True)
    parser.add_argument('--archive',required=True);parser.add_argument('--state')
    args=parser.parse_args();cipher=StateCipher.from_env({'STATE_ENCRYPTION_KEY_FILE':args.key_file})
    if args.action=='verify':result=verify(args.archive,cipher)
    else:
        if not args.state:parser.error('--state is required')
        result=backup(args.state,args.archive,cipher) if args.action=='backup' else restore(args.archive,args.state,cipher)
    print(json.dumps(result))

if __name__=='__main__':main()
