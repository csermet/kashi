/**
 * electron-builder afterPack hook: AD-HOC code-sign the macOS .app.
 *
 * Apple Silicon (arm64) refuses to launch UNSIGNED code — a truly unsigned
 * app shows "Kashi.app is damaged and can't be opened" (not a real corruption;
 * the kernel requires at least an ad-hoc signature to exec arm64 binaries).
 * We have no Apple Developer certificate (notarization is out of scope), so
 * electron-builder's own signing stays off (mac.identity: null) and WE sign
 * the packed bundle ad-hoc here — `codesign --sign -`. That makes the app
 * launchable after the user only clears the download quarantine (xattr -cr),
 * exactly like a normal unsigned-but-runnable app. Version-independent: it
 * doesn't rely on electron-builder's identity auto-discovery behavior.
 *
 * Signs only on darwin. On every platform it first refuses to package a
 * renderer built with a dev-only drawing switched on, and it looks at WHAT WAS
 * PACKED, not at the packer's environment: `KASHI_LATENCY_MARKER=1
 * electron-vite build` followed by `electron-builder` from a clean shell used
 * to pack the marker. Such a build carries a dev-only-build meta tag (named
 * DEV_BUILD_META below) in its HTML (electron.vite.config.ts, markDevOnlyBuild); asar stores files
 * uncompressed, so the tag's bytes are searched in app.asar itself (or in an
 * unpacked app/ directory). A pack with neither fails too: the guard must
 * have looked at something. The packer's own environment is still refused
 * first (a `dist:*` script builds and packs in one go).
 */
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const DEV_ONLY_FLAGS = ['KASHI_LATENCY_MARKER', 'KASHI_FX_DEBUG'];
// Assembled, not spelled out: this file must not carry the bytes it searches for.
const DEV_BUILD_META = ['kashi', 'dev', 'only', 'build'].join('-');

/** Where the packed app's resources sit for a platform's output dir. */
function resourcesDir(context) {
  if (context.electronPlatformName === 'darwin') {
    const appName = `${context.packager.appInfo.productFilename}.app`;
    return path.join(context.appOutDir, appName, 'Contents', 'Resources');
  }
  return path.join(context.appOutDir, 'resources');
}

/** The flags a dev-only build names after its meta tag, from raw bytes. */
function flagsAt(bytes, at) {
  const tail = bytes.subarray(at, at + 300).toString('latin1');
  const match = /content="([^"]*)"/.exec(tail);
  return match ? match[1] : 'unknown';
}

/** Every file under `dir`, recursively. */
function filesUnder(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? filesUnder(full) : [full];
  });
}

/**
 * The dev-only flags the packed app was built with, or null for a normal
 * build. Throws when there is nothing packed to look at.
 */
function packedDevOnlyBuild(resources) {
  const needle = Buffer.from(DEV_BUILD_META);
  const asar = path.join(resources, 'app.asar');
  const dir = path.join(resources, 'app');
  const sources = [];
  if (fs.existsSync(asar)) sources.push(asar);
  if (fs.existsSync(dir)) sources.push(...filesUnder(dir));
  if (sources.length === 0) {
    throw new Error(`afterPack: no app.asar or app/ under ${resources}; nothing to check for a dev-only build`);
  }
  for (const file of sources) {
    const bytes = fs.readFileSync(file);
    const at = bytes.indexOf(needle);
    if (at >= 0) return flagsAt(bytes, at);
  }
  return null;
}

async function afterPack(context) {
  for (const flag of DEV_ONLY_FLAGS) {
    if (process.env[flag] === '1') {
      throw new Error(`afterPack: ${flag}=1 is set; a dev-only renderer build must not be packaged`);
    }
  }
  const devOnly = packedDevOnlyBuild(resourcesDir(context));
  if (devOnly !== null) {
    throw new Error(
      `afterPack: the packed renderer was built with ${devOnly} on; rebuild without it before packaging`,
    );
  }
  if (context.electronPlatformName !== 'darwin') return;
  const appName = `${context.packager.appInfo.productFilename}.app`;
  const appPath = path.join(context.appOutDir, appName);
  // --deep signs the nested Electron frameworks/helpers too; --force replaces
  // any partial signature. Ad-hoc identity is the literal "-".
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', appPath], {
    stdio: 'inherit',
  });
  console.log(`afterPack: ad-hoc signed ${appName}`);
}

exports.default = afterPack;
exports.packedDevOnlyBuild = packedDevOnlyBuild;
exports.resourcesDir = resourcesDir;
exports.DEV_ONLY_FLAGS = DEV_ONLY_FLAGS;
