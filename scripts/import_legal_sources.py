#!/usr/bin/env python3
"""Stage official texts for review; never overwrite the bot's active corpus."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'matrix_support_bot'))
from official_sources import main
if __name__=='__main__': main()
