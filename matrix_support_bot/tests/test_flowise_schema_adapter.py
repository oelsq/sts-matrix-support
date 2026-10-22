"""Exercise the actual compatibility patch without network/model credentials."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "flowise/patch-structured-output.cjs"
SNIPPET = "return llmNodeInstance.withStructuredOutput(structuredOutputSchema, {\n            method: 'functionCalling'\n        });"


@pytest.mark.skipif(not shutil.which("node"), reason="JavaScript runtime unavailable")
def test_patch_is_scoped_and_refuses_unknown_or_repeated_source(tmp_path):
    target = tmp_path / "utils.cjs"
    target.write_text("module.exports = function(llmNodeInstance, structuredOutputSchema) {" + SNIPPET + "};")
    subprocess.run(["node", str(PATCH), str(target)], check=True, capture_output=True)
    program = """
const patch = require(process.argv[1]);
const call = name => patch({model:name, withStructuredOutput:(_schema, options)=>options.method},{});
process.env.STS_JSON_SCHEMA_MODEL = 'local-model';
const values = [call('local-model'), call('other-model')];
delete process.env.STS_JSON_SCHEMA_MODEL;
values.push(call('local-model'));
console.log(JSON.stringify(values));
"""
    result = subprocess.check_output(["node", "-e", program, str(target)], text=True)
    assert json.loads(result) == ["jsonSchema", "functionCalling", "functionCalling"]
    assert subprocess.run(["node", str(PATCH), str(target)], capture_output=True).returncode != 0
    target.write_text("module.exports = {};")
    assert subprocess.run(["node", str(PATCH), str(target)], capture_output=True).returncode != 0
    assert target.read_text() == "module.exports = {};"
