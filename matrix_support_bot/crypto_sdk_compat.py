#!/usr/bin/env python3
"""Pinned nio 0.26 wire compatibility: SAS and compact UTF-8 Megolm JSON.

The vodozemac cryptographic primitives are unchanged. Unexpected SDK sources
fail closed. Track upstream matrix-nio #570 / #572 before the next upgrade.
"""
import hashlib
import importlib.metadata
import importlib.util
from pathlib import Path
import re

EXPECTED = '64315edb3cda348e40fb1a518a36b465b2241d8d31a07e979ace5c6d73b03cf9'
LEGACY_PATCHED = '1632988fd53ab5a07f2a1f7457b362ee3508518257a957cff29a91f4090310e1'
PATCHED = '1ba298585373886473e440c474e8bdc8251e20db40e2767480021058c2f99ea4'

def patch_sas():
    if importlib.metadata.version('matrix-nio')!='0.26.0':raise RuntimeError('Review SAS compatibility for the new SDK version')
    path=Path(importlib.util.find_spec('nio').origin).parent/'crypto/sas.py'
    source=path.read_text();digest=hashlib.sha256(source.encode()).hexdigest()
    if digest==PATCHED:return
    if digest==LEGACY_PATCHED:
        value=source
    elif digest==EXPECTED:
        value=source.replace('from hashlib import sha256','from hashlib import sha256\nimport base64').replace('_mac_normal = "hkdf-hmac-sha256"','_mac_normal = "hkdf-hmac-sha256.v2"')
        pattern=r'(\s*(?:obj\.commitment|calculated_commitment)\s*=\s*)sha256\((.*?)\)\.hexdigest\(\)'
        value,count=re.subn(pattern,lambda m:m[1]+'base64.b64encode(sha256('+m[2]+').digest()).decode().rstrip("=")',value,flags=re.S)
        value+='\nSTS_SAS_WIRE_COMPAT = "matrix-spec-v2"\n'
        if count!=2:raise RuntimeError('SAS patch integrity check failed')
    else:raise RuntimeError('Unexpected nio SAS source; patch not applied')
    old='        generated_bytes = self.established_sas.bytes(extra_info).emoji_indices\n        number = "".join([format(x, "08b") for x in generated_bytes])\n        return [\n            self.emoji[int(x, 2)]\n            for x in map("".join, list(self._grouper(number[:42], 6)))\n        ]'
    new='        # vodozemac returns seven 6-bit indices, not six raw SAS bytes.\n        return [self.emoji[index] for index in self.established_sas.bytes(extra_info).emoji_indices]'
    if value.count(old)!=1:raise RuntimeError('Unexpected emoji mapping')
    value=value.replace(old,new)
    if hashlib.sha256(value.encode()).hexdigest()!=PATCHED:raise RuntimeError('SAS patch integrity check failed')
    compile(value,str(path),'exec');path.write_text(value)
    for old in path.parent.glob('__pycache__/sas.*.pyc'):old.unlink()


# nio 0.26 serializes Megolm plaintext with ensure_ascii=True. Cyrillic text
# plus its HTML copy can exceed Matrix's event limit after base64 encryption.
# UTF-8 JSON has identical decoded content and uses the same crypto primitive.
MEGOLM_EXPECTED = '1d64c2b52f5f69c2c2b251924d66d077473203f0067b294f648ae2cece428e85'
MEGOLM_PATCHED = 'edcb8698ea7d51a9b8002d8001598ae34828962c4627ee835deb19af7f42487b'
MEGOLM_OLD = 'ciphertext = session.encrypt(Api.to_json(plaintext_dict))'
MEGOLM_NEW = 'ciphertext = session.encrypt(json.dumps(plaintext_dict, ensure_ascii=False, separators=(",", ":")))'


def patch_megolm_file(path):
    source=path.read_text();digest=hashlib.sha256(source.encode()).hexdigest()
    if digest==MEGOLM_PATCHED:return
    if digest!=MEGOLM_EXPECTED or source.count(MEGOLM_OLD)!=1:
        raise RuntimeError('Unexpected nio Megolm source; UTF-8 patch not applied')
    value=source.replace(MEGOLM_OLD,MEGOLM_NEW)
    if hashlib.sha256(value.encode()).hexdigest()!=MEGOLM_PATCHED:
        raise RuntimeError('Megolm UTF-8 patch integrity check failed')
    compile(value,str(path),'exec');path.write_text(value)
    for old in path.parent.glob('__pycache__/olm_machine.*.pyc'):old.unlink()


def patch():
    patch_sas()
    patch_megolm_file(Path(importlib.util.find_spec('nio').origin).parent/'crypto/olm_machine.py')

if __name__=='__main__':
    patch();print('Pinned Matrix SAS and UTF-8 Megolm compatibility verified')
