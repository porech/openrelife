```
   ____                   ____       __      ____   
  / __ \____  ___  ____  / __ \___  / /   (_) __/__ 
 / / / / __ \/ _ \/ __ \/ /_/ / _ \/ /   / / /_/ _ \
/ /_/ / /_/ /  __/ / / / _, _/  __/ /___/ / __/  __/
\____/ .___/\___/_/ /_/_/ |_|\___/_____/_/_/  \___/ 
    /_/                                             
```
_**This project is currently in active development and in an alpha state; expect frequent updates and changes, and please report any issues you may encounter.
Please read the readme below for more details.**_

**Do you like this project?** Show your support by starring it! ⭐️ Thank you!

> [!IMPORTANT]  
> **Platform Support**: This application is currently **ONLY** tested and verified on **macOS with Apple Silicon (ARM64)**. Support for Intel Macs, Windows, and Linux is experimental, and needs testing; help is much appreciated.

# Take Control of Your Digital Memory

OpenReLife is a fully open-source alternative to proprietary solutions like Microsoft's Windows Recall or Limitless' Rewind.ai, forked from [OpenRecall](https://github.com/openrecall/openrecall). With OpenReLife, you can easily access your digital history, enhancing your memory and productivity without compromising your privacy.

The main goal of this project is to provide an alternative to Rewind.ai, that was recently abandoned and remotely blocked after the company was acquired by Meta; 
OpenReLife was created to fill this gap as quickly as possible with the best possible UX and features. 
In future, we plan to change the codebase to enhance DX, but we are currently focused on adding the base features you loved from Rewind.ai, having the best possible experience and making the transition as smooth as possible.

## What does it do?

OpenReLife captures your digital history through regularly taken screenshots. The text and images within these screenshots are analyzed and made searchable, allowing you to quickly find specific information by typing relevant keywords into OpenReLife. You can also manually scroll back through your history to revisit past activities.

## Why Choose OpenReLife?

OpenReLife offers several key advantages over closed-source alternatives:

- **Transparency**: OpenReLife is 100% open-source, allowing you to audit the source code for potential backdoors or privacy-invading features.
- **Cross-platform Support**: OpenReLife aims to work on Windows, macOS, and Linux. **Currently, the application is officially tested and supported ONLY on macOS Apple Silicon (ARM64).**
- **Privacy-focused**: Your data is stored locally on your device, no internet connection or cloud is required. 
- **Smooth experience**: OpenReLife is designed to be smooth and easy to use, with a simple and intuitive interface.

## Status of the project

The software is currently in active development (with a huge help from AI agents). It allows for a good set of features, but has been strictly tested on an **Apple Silicon (M1)** machine only.

## Features

- **Time Travel**: Revisit and explore your past digital activities seamlessly, scrolling around the past activities.
- **Local-First AI**: OpenReLife harnesses the power of OpenRecall's local AI processing, with optional cloud-based AI processing for enhanced capabilities.
- **Semantic Search**: Advanced local OCR interprets your history, providing robust semantic search capabilities.
- **Full Control Over Storage**: Your data is stored locally, giving you complete control over its management and security.
- **Auto-Pause**: Recording automatically pauses when you are inactive to save space.
- **Incognito Detection**: Automatically detects incognito/private browsing mode on ALL visible browser windows (Chrome, Safari, Firefox, Edge, Arc, Brave, Opera, Vivaldi, and more). When detected, recording is paused to protect your privacy. This feature can be toggled in the settings.

## Ask your history and connect an agent (1.1.11)

The first **Tab** in the main view focuses the search bar, including after
reopening the window. Press **Command+Enter** there, click **Ask AI** beside the search
icon, or choose **Ask AI** in the Actions menu to ask the current question.
With an empty search, the button opens the agent panel. Under
**Agent command and MCP connection**, choose the editable
Codex or Claude preset, or enter a custom command, then save it. Use the CLI's
existing login; OpenReLife does not require a separate model API integration.
The chosen command may use a cloud model: retrieved OCR and images are then
sent through that command to its configured provider. Local capture, ordinary
search and OCR continue to work without an agent or cloud model.

While a command runs, the question field is disabled and its border animates.
Stop immediately restores editing and removes the animation; another run can
start once the previous command has finished stopping. Reduced-motion settings
keep the running border static.
Escape closes the agent panel before hiding the app window, preserving the
draft and leaving any active command running until it finishes or is stopped.

The panel supports Markdown answers (including lists, tables and code), with
hover/focus screenshot previews on citations and evidence links. Previews load
small thumbnails on demand. The panel also shows
tool-call counts, transferred bytes and an execution trace. The presets connect
OpenReLife's archive tools automatically. A custom command receives the question
on stdin and can return text or an `{"answer":"..."}` JSON object. Commands run
in a temporary working directory: use absolute paths for custom scripts.
`{mcp_config}` in an argument expands to the generated MCP configuration path;
the same path is available as `OPENRELIFE_MCP_CONFIG`. There is no shell
interpolation of the question. A response from a command that did not use any
archive tools is explicitly marked as ungrounded.

### Local MCP access

The panel displays the exact stdio command for external MCP clients. Register
that command in the client's MCP configuration while OpenReLife is running.
The bridge is `python -B -m openrelife.mcp_server --connection <connection-file>`;
the desktop app maintains the private connection file automatically. Tools:

- `archive_status`: available dates, applications, counts and pending OCR.
- `find_frames`: bounded chronological listing with time, app, title, OCR text,
  any-term and OCR-state filters. A text query is not required.
- `semantic_search`: keyword/semantic ranking with time and app filters.
- `read_frames` and `view_frame`: paginated OCR or a size-limited screenshot.
- `neighboring_frames` and `find_intervals`: chronological context, capture
  gaps and groups of matching frames, for investigating observed events.
- `propose_deletion`: select individual instants or disjoint intervals. Pass an
  existing `selection_id` to add another batch to the same proposal.

Agents must distinguish an observed frame from a proven event start. Missing
captures or pending OCR mean incomplete evidence, not proof an event did not
happen. The tools expose screenshot citations as `openrelife://frame/<timestamp>`.

### Deletion stays in the app

An agent can propose candidates but cannot execute deletion through MCP. Open
**Review deletion timeline** to inspect only the proposed frames, their
thumbnails, dates, count and size. Exclude individual frames or reset/cancel the
selection, then use the app's existing confirmation button. The confirmation
is bound to the exact reviewed snapshot; newly captured or newly matching
frames are not silently included. If an agent appends to a proposal, the old
preview cannot confirm it until reloaded.

Pending proposals retain a link to the original request and its answer across
app restarts. Opening an empty panel restores the latest available context
without executing it again; **Show request** switches to a proposal's context.
**Discard proposal** removes only that proposal, keeping screenshots and search
data intact. Proposals from older versions whose context was not retained are
explicitly marked as unavailable; their original prompt is not reconstructed
from the agent's selection description.

Within the supported macOS command runner, an outer OS sandbox prevents direct
archive reads/writes, including resolved screenshot-directory symlinks. Archive
access goes through the scoped MCP API. The desktop's separate mutation
capability travels over a private pipe and is added only to the owned WebView's
requests. The HTTP interface now requires that capability outside the scoped
MCP endpoint: open the desktop UI rather than browsing the raw localhost URL.
External MCP clients retain their host's own OS permissions; registering this
server does not sandbox the entire external client.

### Traces, budgets and validation

Runs have identifiers and local JSONL traces under `agent/traces` in the data
directory. They record queries, redacted commands, public tool inputs, returned
frame identifiers, output hashes, byte counts, timings, CLI events, failures,
exit codes, cancellation and timeout. Image payloads are omitted from traces.
No hidden model reasoning is reconstructed. Source-dependent in-app answers
and traces are invalidated when their screenshots are deleted. Saved proposal
contexts retain bounded dependency maps across restarts, so unrelated deletion
does not clear them. Older traces without a known map, and contexts whose full
dependency map exceeds the limit, are conservatively invalidated on deletion.

Proposal contexts are private snapshots (not executable sessions), limited to
30 records, 2 MiB per record, 100 MiB overall and 30 days, pruned at startup and
when new contexts are created. An unfinished request is restored as interrupted
and is never automatically resumed.

The runner permits one in-app run at a time, up to 150 tool calls and about
30 MiB of tool responses per run. The default timeout is 300 seconds and can be
configured between 10 and 1800 seconds. Traces are capped per run and pruned to
100 MiB/30 days on startup; pending proposals are bounded to 20 files,
100 MiB and seven days. Local command execution currently requires macOS.

Development checks use disposable synthetic archives, never the personal
capture store:

```sh
.venv/bin/python -m pytest -q
cd electron-app
node --test tests/agents.test.cjs tests/agent-security.test.cjs
cd ..
.venv/bin/python tests/manual/check_agents_public.py --providers codex claude
```

The public test starts the normal backend with `--storage-path` and
`--no-capture`, exercises the real stdio MCP client and command runner, and
preserves failures and semantic checks under `logs/agent-integration-*`.
JavaScript tests use the product HTML and scripts in a simulated DOM; they
are not native visual acceptance. Validate the native layout on a Mac as well.
For an isolated desktop archive, the app also accepts `--storage-path=<path>`,
`--no-capture` and `--show`; `--backend-python=<absolute-path>` selects an
existing development interpreter instead of creating another environment.

## Get Started
 
### Prerequisites
- macOS (Apple Silicon / ARM64 required; Intel/Windows/Linux support needs testing)
- [uv](https://astral.sh/uv) (needed, for backend's Python dependency management)
- [Node.js](https://nodejs.org/) & npm (facultative, needed to build the Electron app)
- Python 3.11+ (should be installed and managed by uv)
 
### Installation

You can directly download the latest release from [GitHub releases](https://github.com/porech/openrelife/releases); as per now, the app needs external dependencies to work ([uv](https://astral.sh/uv)), so please follow the manual installation instructions to get started.
In the near future, this will (hopefully) change.

The dmg file contains the .app file, that you can drag to your Applications folder.

The app is signed with a self-signed certificate but is **not notarized by Apple** (the project is free and has no paid Apple Developer account). On first launch macOS will say it cannot verify the developer and won't open it directly — this is expected, the app is safe. To open it:

1. Double-click OpenReLife once — macOS shows a "cannot be verified" dialog; click **Done**.
2. Open **System Settings → Privacy & Security** and scroll down to the **Security** section.
3. Next to the *"OpenReLife was blocked…"* message, click **Open Anyway**, then confirm with Touch ID / your password.

You only need to do this once. After that, the app opens normally from the Applications folder.

### Manual Installation 
We provide a helper script to set up the environment and build the application on MacOS:
 
```bash
# Clone the repository
git clone https://github.com/openrelife/openrelife.git
cd openrelife
 
# Run installation script
./install.sh
```
 
This script will:
1. Check for `uv` installation.
2. Install Python backend dependencies.
3. Install Electron frontend dependencies.
4. Build the standalone macOS `.app`.
 
### Running the App
 
After the installation completes, the application will be available at:
`electron-app/dist/mac-arm64/OpenReLife.app` (or `mac-x64` for Intel Macs).
 
You can drag this file to your **Applications** folder.
 
**On first launch:**
1. Open Spotlight / Raycast (**Cmd+Space**) or Launchpad and type **OpenReLife**.
2. You will be prompted to grant **Screen Recording** permission in System Settings (if not, please do it manually from System Settings -> Privacy & Security -> Screen Recording -> +). This is required to capture screenshots.

note: please be aware that the first run can take a little time, and should ask recording permission as well (without the permission, it won't work).

**Remember that you can always close the UI pressing ESC.**
If you experience a black page persisting for more than 30 seconds, the backend probably failed to start. You can check it opening http://localhost:8082 in a web browser - if the port 8082 is available, it should not happen. Please open an issue if you experience this.

### Backend Arguments (from OpenRecall)
--storage-path (default: user data path for your OS): allows you to specify the path where the screenshots and database should be stored. We recommend creating an encrypted volume to store your data.

--primary-monitor-only (default: False): only record the primary monitor (rather than individual screenshots for other monitors)

### Technical details

The app for now is a Flask backend with a Electron frontend. The backend is responsible for capturing screenshots, processing them, storing them in a database, and providing an API for the frontend to interact with. The frontend is responsible for displaying the UI and interacting with the backend. 

The backend is running on port 8082 (like OpenRecall, will be configurable in the future).

The frontend is running as a old way full screen app that can be toggled with a global hotkey (Cmd+Shift+Space by default, will be configurable in the future) and totally overlaps your current desktop. You can exit it by pressing the escape key (ESC), exactly like in Rewind.ai.
 
### Key Features & Usage
 
- **Global Hotkey**: Press **Cmd+Shift+Space** anytime to toggle the OpenReLife overlay.
- **Privacy First**: All data is stored locally on your machine.
- **Background Mode**: The app runs silently in the background; access it via the menu bar icon or hotkey.


## Uninstall instructions

To uninstall OpenReLife and remove all stored data:

1. Drag the OpenReLife app from your **Applications** folder to the **Trash**.

2. Remove stored data:
   - On Windows:
     ```
     rmdir /s %APPDATA%\openrelife
     ```
   - On macOS:
     ```
     rm -rf ~/Library/Application\ Support/openrelife
     ```
   - On Linux:
     ```
     rm -rf ~/.local/share/openrelife
     ```

If you use AppCleaner, just drag the OpenReLife app into AppCleaner and click the button.

## Contribute

As an open-source project, we welcome contributions from the community. If you'd like to help improve OpenReLife, please submit a pull request or open an issue on our GitHub repository.

## License

The whole source code is released under the terms of the GNU General Public License (GPL) v2. You can find a copy of the license in the LICENSE file.

