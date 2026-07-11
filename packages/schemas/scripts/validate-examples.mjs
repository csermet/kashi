// Validates every fixture in examples/ against the v1 schema, plus the two
// structural rules the schema itself cannot express cleanly:
//   - sync=word  → at least ONE line has a non-empty `words` array (a line
//     whose word timings were rejected by server-side QA omits the field;
//     the overlay renders such lines as plain text)
//   - sync=line  → no line carries a `words` field at all
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const schema = JSON.parse(readFileSync(join(root, 'processed-track.v1.schema.json'), 'utf8'));

const ajv = new Ajv2020.default({ strict: true, allErrors: true });
addFormats.default(ajv);
const validate = ajv.compile(schema);

let failed = false;
for (const file of readdirSync(join(root, 'examples')).filter((f) => f.endsWith('.json'))) {
  const doc = JSON.parse(readFileSync(join(root, 'examples', file), 'utf8'));
  const errors = [];

  if (!validate(doc)) {
    errors.push(...validate.errors.map((e) => `${e.instancePath || '/'} ${e.message}`));
  }
  for (const [i, line] of (doc.lines ?? []).entries()) {
    if (doc.sync === 'line' && 'words' in line) {
      errors.push(`/lines/${i}: sync=line forbids a words field`);
    }
  }
  if (doc.sync === 'word' && !(doc.lines ?? []).some((l) => Array.isArray(l.words) && l.words.length > 0)) {
    errors.push('/lines: sync=word requires at least one line with non-empty words');
  }

  if (errors.length > 0) {
    failed = true;
    console.error(`FAIL ${file}\n  ${errors.join('\n  ')}`);
  } else {
    console.log(`ok   ${file}`);
  }
}
process.exit(failed ? 1 : 0);
