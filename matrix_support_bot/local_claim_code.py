"""Pure JavaScript reference implementation for local validator parity tests."""
from pathlib import Path
ROOT=Path(__file__).with_name("agents")/"quality"

def unit_code():
    return (ROOT/'claim_gate.js').read_text().split('const answer = $flow.state.draft;')[0]
