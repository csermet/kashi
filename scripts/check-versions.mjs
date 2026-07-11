// Version-lockstep guard. The bindings below were previously enforced only by
// comments ("Bump IN LOCKSTEP") — this makes drift a CI failure:
//   1. overlay package.json version == shared/version.ts KASHI_VERSION
//   2. extension manifest.json version == extension package.json version
//   3. version.ts EXPECTED_EXTENSION == "kashi-extension/<extension version>"
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => readFileSync(join(root, p), 'utf8');
const json = (p) => JSON.parse(read(p));

const overlayPkg = json('apps/overlay/package.json').version;
const extPkg = json('apps/extension/package.json').version;
const extManifest = json('apps/extension/manifest.json').version;
const versionTs = read('apps/overlay/src/shared/version.ts');

const kashiVersion = versionTs.match(/KASHI_VERSION = '([^']+)'/)?.[1];
const expectedExt = versionTs.match(/EXPECTED_EXTENSION = '([^']+)'/)?.[1];

const failures = [];
if (kashiVersion !== overlayPkg) {
  failures.push(`KASHI_VERSION ${kashiVersion} != overlay package.json ${overlayPkg}`);
}
if (extManifest !== extPkg) {
  failures.push(`extension manifest.json ${extManifest} != package.json ${extPkg}`);
}
if (expectedExt !== `kashi-extension/${extManifest}`) {
  failures.push(
    `EXPECTED_EXTENSION ${expectedExt} != kashi-extension/${extManifest} — bump it ` +
      'after testing the overlay against the new extension build',
  );
}

if (failures.length > 0) {
  console.error('version lockstep broken:\n  ' + failures.join('\n  '));
  process.exit(1);
}
console.log(
  `versions in lockstep: overlay ${overlayPkg}, extension ${extManifest}, expects ${expectedExt}`,
);
