// Compatibility adapter for the pinned Flowise 3.1.4 image. No credentials.
const fs = require('fs');
const file = process.argv[2] || '/usr/local/lib/node_modules/flowise/node_modules/flowise-components/dist/src/utils.js';
const before = fs.readFileSync(file,'utf8');
const needle = "return llmNodeInstance.withStructuredOutput(structuredOutputSchema, {\n            method: 'functionCalling'\n        });";
if (before.split(needle).length!==2) throw Error('Unknown Flowise structured-output implementation; patch refused');
const replacement = "return llmNodeInstance.withStructuredOutput(structuredOutputSchema, {\n            method: (process.env.STS_JSON_SCHEMA_MODEL && (llmNodeInstance.model || llmNodeInstance.modelName) === process.env.STS_JSON_SCHEMA_MODEL) ? 'jsonSchema' : 'functionCalling'\n        });";
const tmp=file+'.sts-patch-tmp';
fs.writeFileSync(tmp,before.replace(needle,replacement),{flag:'wx',mode:0o644});
fs.renameSync(tmp,file);
console.log('Scoped JSON-schema compatibility adapter installed');
