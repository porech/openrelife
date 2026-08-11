/**
 * electron-builder afterPack hook — self-signed code signing.
 *
 * The app is distributed without an Apple Developer ID (the project is free and
 * has no paid Apple account). We sign it with a local self-signed certificate.
 * This does two things a plain unsigned build cannot:
 *   1. Produces a VALID signature, so macOS shows the recoverable "unidentified
 *      developer" prompt (one click in System Settings > Privacy & Security)
 *      instead of the "app is damaged" dead-end that forces a Terminal command.
 *   2. Gives the app a STABLE code identity (the certificate), so TCC permissions
 *      like Screen Recording survive app updates. A plain ad-hoc signature is
 *      pinned to the cdhash and would be lost on every update.
 *
 * We sign in afterPack (which always runs) with electron-builder's own signing
 * disabled ("identity": null), so nothing overwrites this signature.
 *
 * The certificate lives in a dedicated local keychain (see docs/CODESIGN.md).
 * The SAME certificate must sign every release, otherwise users re-grant
 * permissions once after the next update.
 *
 * Override via env vars if needed:
 *   CODESIGN_IDENTITY    (default: "porech")
 *   CODESIGN_KEYCHAIN    (default: ~/Library/Keychains/openrelife-codesign.keychain-db)
 *   CODESIGN_KEYCHAIN_PW (default: "" — the signing keychain has no password;
 *                         set this only if you protect the keychain. Never hardcode it.)
 */
const { execFileSync } = require('child_process');
const path = require('path');
const os = require('os');

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return;

  const identity = process.env.CODESIGN_IDENTITY || 'porech';
  const keychain =
    process.env.CODESIGN_KEYCHAIN ||
    path.join(os.homedir(), 'Library/Keychains/openrelife-codesign.keychain-db');

  const appName = context.packager.appInfo.productFilename;
  const appPath = path.join(context.appOutDir, `${appName}.app`);

  console.log(`[sign-selfsigned] signing ${appPath} with identity "${identity}"`);

  // Unlock the keychain. The password comes from the environment; the default is
  // empty because the dedicated signing keychain is created without a password,
  // so nothing secret is hardcoded here. Best-effort: ignore if already unlocked.
  const keychainPw = process.env.CODESIGN_KEYCHAIN_PW || '';
  try {
    execFileSync('security', ['unlock-keychain', '-p', keychainPw, keychain], { stdio: 'ignore' });
  } catch (_) {}

  // Sign the whole bundle with the self-signed certificate.
  execFileSync(
    'codesign',
    ['--force', '--deep', '--sign', identity, '--keychain', keychain, appPath],
    { stdio: 'inherit' }
  );

  // Fail the build if the resulting signature is not valid.
  execFileSync('codesign', ['--verify', '--strict', appPath], { stdio: 'inherit' });

  console.log('[sign-selfsigned] signature valid');
};
