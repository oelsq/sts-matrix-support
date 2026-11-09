"""Authenticated encryption of sensitive bot fields; keys stay outside SQLite."""
import base64
import hashlib
import hmac
import os
import stat
from pathlib import Path
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

PREFIX = 'stsenc:v1:'


class StateEncryptionError(ValueError):
    pass


def private_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise StateEncryptionError('Private directory must not be a symlink')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def read_private_secret(path):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise StateEncryptionError('Secret must be a regular owner-only file')
        value = os.read(fd, 4097)
        if len(value) > 4096:
            raise StateEncryptionError('Oversized secret file')
        return value.strip()
    finally:
        os.close(fd)


class StateCipher:
    def __init__(self, key):
        if not isinstance(key, bytes) or len(key) != 32:
            raise StateEncryptionError('State encryption requires a 32-byte key')
        self._master = key
        self._key = hmac.digest(key, b'sts-state-fields/v1', 'sha256')
        self.key_id = hashlib.sha256(self._key).hexdigest()[:24]

    @classmethod
    def from_env(cls, env):
        path = env.get('STATE_ENCRYPTION_KEY_FILE')
        if not path:
            if env.get('MATRIX_E2EE_ENABLED') == '1':
                raise StateEncryptionError('E2EE requires encrypted bot state')
            return None
        try:
            key = base64.b64decode(read_private_secret(path), altchars=b'-_', validate=True)
        except (ValueError, OSError) as exc:
            raise StateEncryptionError('State key unavailable or invalid') from exc
        return cls(key)

    def routing_token(self, value):
        return 'sts-private-'+hmac.digest(self._master,b'sts-private-room/v1\0'+value.encode(),'sha256').hex()[:32]

    def crypto_pickle_key(self):
        # Separate domain from application fields and encrypted backups.
        return base64.urlsafe_b64encode(hmac.digest(self._master, b'sts-nio-pickle/v1', 'sha256')).decode()

    def seal(self, text, purpose):
        if not isinstance(text, str):
            raise StateEncryptionError('State field must be text')
        if text == '':
            return ''  # erased payload/tombstone; contains no secret text
        nonce = get_random_bytes(12)
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
        cipher.update(purpose.encode())
        ciphertext, tag = cipher.encrypt_and_digest(text.encode())
        return PREFIX + base64.urlsafe_b64encode(nonce + tag + ciphertext).decode()

    def open(self, value, purpose):
        if value == '':
            return ''
        if not isinstance(value, str) or not value.startswith(PREFIX):
            raise StateEncryptionError('Unprotected state field in encrypted database')
        try:
            raw = base64.b64decode(value[len(PREFIX):], altchars=b'-_', validate=True)
            if len(raw) < 28:
                raise ValueError('short envelope')
            cipher = AES.new(self._key, AES.MODE_GCM, nonce=raw[:12])
            cipher.update(purpose.encode())
            return cipher.decrypt_and_verify(raw[28:], raw[12:28]).decode()
        except (ValueError, UnicodeError) as exc:
            raise StateEncryptionError('State authentication failed') from exc
