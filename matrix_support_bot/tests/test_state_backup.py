import fcntl
from pathlib import Path
import pytest
from state_cipher import StateCipher,StateEncryptionError
from state_backup import backup,restore,verify


def test_encrypted_backup_restores_identity_and_rejects_tampering(tmp_path):
    source=tmp_path/'state';source.mkdir();(source/'crypto').mkdir()
    (source/'matrix_inbox.sqlite3').write_bytes(b'synthetic-private-session')
    (source/'crypto/native.db').write_bytes(b'synthetic-private-crypto-key')
    cipher=StateCipher(b'K'*32);archive=tmp_path/'backups/state.aes'
    receipt=backup(source,archive,cipher)
    assert receipt['authenticated'] and receipt['files']==2
    assert b'synthetic-private' not in archive.read_bytes() and archive.stat().st_mode&0o777==0o600
    target=tmp_path/'restored';restore(archive,target,cipher)
    assert (target/'crypto/native.db').read_bytes()==b'synthetic-private-crypto-key'
    assert (target/'crypto/native.db').stat().st_mode&0o777==0o600
    with pytest.raises(ValueError):restore(archive,target,cipher)
    with pytest.raises(StateEncryptionError):verify(archive,StateCipher(b'W'*32))
    raw=bytearray(archive.read_bytes());raw[-20]^=1;archive.write_bytes(raw)
    with pytest.raises(StateEncryptionError):restore(archive,tmp_path/'bad',cipher)
    assert not (tmp_path/'bad').exists()


def test_consistent_backup_refuses_live_inbox_and_symlinks(tmp_path):
    source=tmp_path/'state';source.mkdir();cipher=StateCipher(b'K'*32)
    with (source/'matrix_inbox.sqlite3.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError):backup(source,tmp_path/'backup',cipher)
    (source/'linked').symlink_to(tmp_path/'outside')
    with pytest.raises(ValueError):backup(source,tmp_path/'backup',cipher)
