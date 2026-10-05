# Release pipeline (CI-on-tag + Homebrew cask) — design

## Problem / goal
Publishing a release today is a manual local sequence (bump version, build+sign, push, `gh release create`, upload DMGs). We want a **one-shot release**: push a tag → GitHub Actions builds the signed DMGs, creates the GitHub release, and (later) publishes a Homebrew cask — extensible to Linux/other channels. Modeled on how `porech/dwshell` does it with GoReleaser, adapted for our Electron + Python stack (GoReleaser is Go-only, so we hand-roll the equivalent).

## Decisions (settled with the maintainer)
- **Approach B — CI on tag.** The signing cert `porech` goes into GitHub secrets; the build runs on a macOS runner. (Approach A, a local one-command script, was rejected in favor of full automation.)
- **Homebrew via our own tap `porech/homebrew-tap`**, NOT the official `Homebrew/homebrew-cask`. The official repo deprecates/disables casks that fail Gatekeeper (unsigned/un-notarized) — e.g. CopyQ, Sept 2026. Third-party taps are not policed; `dwshell` (unsigned) lives on this same tap. So we are not exposed to that policy.
- **Cask strips quarantine post-install** (`xattr -dr`, like dwshell) → zero-friction install. The signature still gives us TCC permission-persistence; quarantine-stripping only affects the Gatekeeper first-launch prompt.
- **No `depends_on "uv"`.** The app ships its own `uv` binaries via `extraResources` (`electron-app/bin/uv-{arm64,x64}`, resolved by `getUvBinary()`); the README line claiming "needs external uv" is stale and gets fixed here.
- **`uv.lock` stays gitignored.** It is deliberately build-generated (commit `3e3cdee` "gitignore uv.lock, auto-generate it during electron build"); the `prebuild*` scripts run `uv lock` and it is bundled as an `extraResource`. It is a derivative of `pyproject.toml`, so committing it would be an independent copy that can drift (single-source-of-truth). CI regenerates it the same way a local build does.
- **Remove the unused `canvas` devDependency** (a native module, not referenced anywhere) — avoids a native-build step in CI and slims the install.

## Scope
- **In now:** CI workflow that, on a `v*` tag, builds+signs both-arch DMGs and creates the GitHub release (DMGs only). Plus `workflow_dispatch` dry-run. Plus the cask template + render script. Plus README/`canvas` cleanups.
- **Deferred:** the tap-push job is wired but inactive until the maintainer adds the tap credentials (see Prerequisites). Until then, releases ship via GitHub; Homebrew goes live when the secrets land.
- **Future (not this spec):** Linux packaging (deb/rpm), Scoop, in-app electron-updater.

## Architecture

### Trigger & flow
- `.github/workflows/release.yml`, triggered by `on: push: tags: ['v*']` and `on: workflow_dispatch` (dry-run).
- Human release flow (unchanged bump discipline): bump `pyproject.toml` + `electron-app/package.json` to the next `1.1.x`, commit, `git push origin dev` + `git push prod dev:main`, then `git push prod v1.1.x`. The tag on **GitHub (`prod`)** fires the workflow; GitLab runs nothing.
- **Dry-run first, always, for any pipeline change:** `workflow_dispatch` builds+signs and uploads the DMGs as workflow artifacts, then STOPS (no release, no tap). We verify the artifact before ever pushing a real tag — a failed real tag would pollute the release list and the versioning standard forbids `-rc` tags.

### Job 1 — `build` (runs on `macos-latest`)
Steps, in order:
1. `actions/checkout` (the tag's commit).
2. `actions/setup-node` (Node 20+); install `uv` on the runner (official installer or `brew install uv`) — `prebuild-mac` runs `uv lock`.
3. **Version guard (single source of truth):** on a tag build, fail fast unless `${GITHUB_REF_NAME#v}` equals `jq -r .version electron-app/package.json`. electron-builder names the DMGs from `package.json`, so a mismatched tag would break the cask URL.
4. **Import the signing cert into a temp keychain** (this exact sequence is required — `codesign --keychain` alone gave "no identity found" in testing):
   - decode `CODESIGN_P12_BASE64` → `cert.p12`
   - `security create-keychain -p "$KP" build.keychain-db`
   - `security set-keychain-settings build.keychain-db` (disable auto-lock; build runs for minutes)
   - `security unlock-keychain -p "$KP" build.keychain-db`
   - `security import cert.p12 -k build.keychain-db -P "$CODESIGN_P12_PASSWORD" -A -T /usr/bin/codesign`
   - `security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KP" build.keychain-db`
   - `security list-keychains -d user -s build.keychain-db $(existing login.keychain-db)`
   - export `CODESIGN_KEYCHAIN=<path>` and `CODESIGN_KEYCHAIN_PW="$KP"` for the afterPack hook. (`$KP` is a throwaway keychain password generated in-job, NOT a project secret. Do NOT gate on `find-identity -v` — an untrusted self-signed cert reports 0 valid.)
5. `npm ci` in `electron-app` (uses `package-lock.json`), then `npx electron-builder --mac --publish never`. `--publish never` prevents electron-builder from auto-publishing ZIPs/blockmaps/`latest-mac.yml`/a draft release when it detects CI + a token — keeping our DMG-only convention. The `afterPack` hook signs with `porech` via the temp keychain.
6. **Acceptance verification on the artifact:** `codesign --verify --strict`; `codesign -dv --verbose=4` shows `Authority=porech`; `codesign -d --requirements -` shows `certificate leaf = H"346c61350800ffbb42012ae0ff140d26a0e71fad"` — the SAME leaf as v1.1.1. Fail the job otherwise. This is what keeps TCC permissions persisting for v1.1.1 users (locally signed) who upgrade to the first CI build; a different leaf makes every user re-grant once.
7. Upload the two DMGs as workflow artifacts (always).
8. **On a tag only:** `gh release create "$GITHUB_REF_NAME" -R porech/openrelife --title ... --notes ...` with **only** the two DMGs (`OpenReLife-<v>-arm64.dmg`, `OpenReLife-<v>.dmg`).

### Job 2 — `homebrew` (deferred; needs tap secrets; `needs: build`)
- Guard: skip cleanly if the tap secrets are absent (so Job 1 is never blocked by the tap not being set up).
- Compute `sha256` of both DMGs; render `Casks/openrelife.rb` from a template (below); commit+push to `porech/homebrew-tap` using a token minted from the `porech-tap-deploy` GitHub App (`TAP_APP_ID` + `TAP_APP_PRIVATE_KEY`), mirroring dwshell — or a PAT scoped to `homebrew-tap`.

### Homebrew cask template (`Casks/openrelife.rb`)
Rendered per release; `version` + both `sha256` computed from the built DMGs — never hand-maintained.
```ruby
cask "openrelife" do
  arch arm: "-arm64", intel: ""
  version "<VERSION>"
  sha256 arm: "<SHA_ARM>", intel: "<SHA_INTEL>"
  url "https://github.com/porech/openrelife/releases/download/v#{version}/OpenReLife-#{version}#{arch}.dmg"
  name "OpenReLife"
  desc "Screen memory for macOS"
  homepage "https://github.com/porech/openrelife"
  app "OpenReLife.app"
  postflight do
    system_command "/usr/bin/xattr",
      args: ["-dr", "com.apple.quarantine", "#{appdir}/OpenReLife.app"]
  end
end
```
(No `depends_on`; the app bundles `uv`. No `auto_updates` — Homebrew IS the updater: `brew upgrade`.)

## Prerequisites (maintainer provides; design does not block on the deferred ones)
GitHub repo secrets in `porech/openrelife`:
- `CODESIGN_P12_BASE64` — `base64 < porech-codesign-backup.p12` (the .p12 lives in the maintainer's pCloud / Bitwarden).
- `CODESIGN_P12_PASSWORD` — the .p12 password.
- **(deferred)** `TAP_APP_ID` + `TAP_APP_PRIVATE_KEY` (from the `porech-tap-deploy` App or org settings), or a PAT scoped to `homebrew-tap`.

## Collateral changes
- `electron-app/package.json`: remove the unused `canvas` devDependency (+ regenerate `package-lock.json`).
- `README.md`: fix the stale "needs external uv / manual installation" line (the app bundles uv); add `brew install --cask porech/tap/openrelife` next to the DMG download.
- `CLAUDE.md` / release-workflow memory: note the new "push a tag → CI releases" path alongside the manual one.

## Caveats / notes
- macOS GitHub Actions minutes are free for public repos.
- `uv.lock` is resolved fresh each CI run (same as local builds); `uv lock` is largely deterministic given `pyproject.toml` + uv version.
- First real tag only after a green dry-run (artifact passes the Job-1 acceptance checks and `brew install --cask ./openrelife.rb` works locally from the rendered cask).

## Out of scope
Linux packaging, Scoop/Windows, in-app electron-updater, and moving signing to a paid Developer ID / notarization.
