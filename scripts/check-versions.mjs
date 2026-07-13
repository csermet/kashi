// Version-lockstep guard. The bindings below were previously enforced only by
// comments ("Bump IN LOCKSTEP") — this makes drift a CI failure:
//   1. overlay package.json version == shared/version.ts KASHI_VERSION
//   2. extension manifest.json version == extension package.json version
//   3. version.ts EXPECTED_EXTENSION == "kashi-extension/<extension version>"
//   4. server pyproject.toml == __init__.py __version__ == uv.lock (drift
//      actually happened: ec446e1 "uv.lock version sync")
//   5. version.py PIPELINE_MAJOR == major(PIPELINE_VERSION)
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

const serverPyproject = read('apps/server/pyproject.toml').match(/^version = "([^"]+)"/m)?.[1];
const serverInit = read('apps/server/src/kashi_server/__init__.py').match(
  /__version__ = "([^"]+)"/,
)?.[1];
const serverLock = read('apps/server/uv.lock').match(
  /name = "kashi-server"\r?\nversion = "([^"]+)"/,
)?.[1];
const versionPy = read('apps/server/src/kashi_server/version.py');
const pipelineVersion = versionPy.match(/PIPELINE_VERSION = "([^"]+)"/)?.[1];
const pipelineMajor = versionPy.match(/PIPELINE_MAJOR = (\d+)/)?.[1];

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
if (serverPyproject !== serverInit || serverPyproject !== serverLock) {
  failures.push(
    `server version trio out of sync: pyproject ${serverPyproject}, ` +
      `__init__.py ${serverInit}, uv.lock ${serverLock} (run \`uv lock\` after a bump)`,
  );
}
if (!pipelineVersion || pipelineMajor !== pipelineVersion.split('.')[0]) {
  failures.push(
    `PIPELINE_MAJOR ${pipelineMajor} != major of PIPELINE_VERSION ${pipelineVersion}`,
  );
}

if (failures.length > 0) {
  console.error('version lockstep broken:\n  ' + failures.join('\n  '));
  process.exit(1);
}
console.log(
  `versions in lockstep: overlay ${overlayPkg}, extension ${extManifest}, expects ${expectedExt}, ` +
    `server ${serverPyproject} (pipeline ${pipelineVersion})`,
);
