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
 * Runs only on darwin; a no-op on the Windows/Linux packers.
 */
const { execFileSync } = require('node:child_process');
const path = require('node:path');

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return;
  const appName = `${context.packager.appInfo.productFilename}.app`;
  const appPath = path.join(context.appOutDir, appName);
  // --deep signs the nested Electron frameworks/helpers too; --force replaces
  // any partial signature. Ad-hoc identity is the literal "-".
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', appPath], {
    stdio: 'inherit',
  });
  console.log(`afterPack: ad-hoc signed ${appName}`);
};
