#!/usr/bin/env python3
"""Apply the reviewed SDK patch used by the bot image."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'matrix_support_bot'))
from crypto_sdk_compat import patch
if __name__=='__main__':
    patch();print('Pinned Matrix SAS compatibility verified')
