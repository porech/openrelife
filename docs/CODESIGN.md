# macOS code signing

OpenReLife is signed with a **local self-signed certificate**, not an Apple
Developer ID. The project is free and has no paid Apple account, so notarization
(which would give a zero-friction install) is not possible. Self-signing is the
free middle ground and buys two concrete things over an unsigned build:

1. **Valid signature** → macOS shows the recoverable *"cannot verify developer"*
   prompt (one click in System Settings → Privacy & Security → **Open Anyway**)
   instead of the *"app is damaged"* dead-end that forced users to run
   `sudo xattr -d com.apple.quarantine` in a Terminal.
2. **Stable code identity** → TCC permissions (Screen Recording, etc.) survive
   app updates. A plain ad-hoc signature is pinned to the cdhash, which changes
   on every build, so the permission would be lost after each update. The
   self-signed certificate makes the *designated requirement* depend on the
   certificate instead:
   `identifier "com.openrelife.app" and certificate leaf = H"<cert sha1>"`.

What self-signing does **not** do: it is not trusted by Apple, so Gatekeeper
still shows the first-launch prompt on downloaded builds. Only Apple
notarization removes that, and that needs the paid Developer Program.

## The certificate

- Common Name (identity): `porech`
- Type: self-signed, RSA 2048, `codeSigning` extended key usage, ~20y validity
- Lives in a dedicated local keychain: `~/Library/Keychains/openrelife-codesign.keychain-db`
- Encrypted backup (`.p12`): stored in Bitwarden (file + password). Keep it — it
  is the only copy of the private key outside this machine.

> **Critical:** the *same* certificate must sign every release. If it is lost and
> regenerated, its leaf hash changes, the designated requirement changes, and
> users will have to re-grant Screen Recording once after the next update.

## How the build uses it

`electron-app/package.json` sets `"mac": { "identity": null }` so electron-builder
does **not** run its own (Developer-ID-oriented) signing, and an `afterPack` hook
`electron-app/scripts/sign-selfsigned.js` signs the bundle with the certificate:

```
codesign --force --deep --sign porech \
  --keychain ~/Library/Keychains/openrelife-codesign.keychain-db <App>.app
```

Overridable via env vars: `CODESIGN_IDENTITY`, `CODESIGN_KEYCHAIN`,
`CODESIGN_KEYCHAIN_PW`.

Building normally (`npm run build-mac`) signs automatically — no extra step.

### Keychain password

The dedicated signing keychain is created **without a password** (empty), so the
build is non-interactive and **no password is hardcoded anywhere** — the hook
unlocks with an empty string by default. Tradeoff: any process running as your
user can use the `porech` key to sign. For a self-signed cert on a free project
this is low risk (the signature grants no Apple trust; the only special property
is satisfying OpenReLife's TCC designated requirement).

To harden it, set a real password on the keychain and provide it at build time
via `CODESIGN_KEYCHAIN_PW` (e.g. from a git-ignored `.codesign.env` or your
shell) — never commit it. The private-key backup that matters lives password-
protected in the `.p12` in Bitwarden.

## Verify a build

```bash
codesign --verify --strict --verbose=2 dist/mac-arm64/OpenReLife.app
codesign -dv --verbose=4 dist/mac-arm64/OpenReLife.app | grep -E "Authority=|Sealed"
codesign -d --requirements - dist/mac-arm64/OpenReLife.app | grep designated
```

Expected: `valid on disk`, `Authority=porech`, `Sealed Resources version=2`, and a
`certificate leaf` designated requirement.

## Restore the certificate on another machine

Download the `.p12` backup from Bitwarden, then:

```bash
KC=~/Library/Keychains/openrelife-codesign.keychain-db
security create-keychain -p "" "$KC"
security set-keychain-settings "$KC"          # disable auto-lock
security unlock-keychain -p "" "$KC"
security import porech-codesign-backup.p12 -k "$KC" -P "<p12 password>" -A -T /usr/bin/codesign
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "" "$KC"
security list-keychains -d user -s "$KC" $(security list-keychains -d user | sed 's/[" ]//g')
security find-identity "$KC"                  # should list "porech"
```
