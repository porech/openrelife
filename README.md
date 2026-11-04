```
   ____                   ____       __      ____   
  / __ \____  ___  ____  / __ \___  / /   (_) __/__ 
 / / / / __ \/ _ \/ __ \/ /_/ / _ \/ /   / / /_/ _ \
/ /_/ / /_/ /  __/ / / / _, _/  __/ /___/ / __/  __/
\____/ .___/\___/_/ /_/_/ |_|\___/_____/_/_/  \___/ 
    /_/                                             
```

**OpenReLife is in active development.** Tested on macOS Apple Silicon (M1);
Intel builds are available but have not been tested on Intel hardware.
Windows and Linux support is experimental.

# Take Control of Your Digital Memory

OpenReLife records your screen and makes its text searchable, so you can find
past work or scroll back through your history. It is an open-source alternative
to Rewind.ai and Windows Recall, forked from [OpenRecall](https://github.com/openrecall/openrecall).

## Features

- **Screen history:** browse past screenshots on a timeline.
- **Local search:** find captured text with OCR and semantic search.
- **Storage controls:** choose capture quality, frequency and retention.
- **Privacy controls:** pause when inactive and skip supported private browser windows.
- **Local storage:** screenshots and search data stay on your device. Cloud AI is optional.

## Ask your history

Ask questions about your screenshots using Codex, Claude or a custom command,
configured in **Settings → AI & agents**. A local MCP server also connects external
assistants; your chosen command may send retrieved content to its cloud provider.

See the [agent integration reference](docs/AGENT_INTEGRATION.md) for setup and technical details.

## Install on macOS

1. Download the Apple Silicon or Intel DMG from [GitHub releases](https://github.com/porech/openrelife/releases).
2. Drag **OpenReLife.app** into **Applications** and open it.
3. Grant **Screen Recording** access when prompted. **Accessibility** access lets the app read window titles.

The app bundles `uv` and prepares its Python environment on first launch.

The app is self-signed and not Apple-notarized. If macOS blocks the first launch,
use **System Settings → Privacy & Security → Open Anyway**.
[Code-signing details](docs/CODESIGN.md).

## Everyday use

- Open the timeline with **Cmd+Shift+Space** or the menu bar icon.
- Search your history or use **Ask AI** for questions.
- Open settings with **Cmd+,**. Press **Esc** to close panels or hide the viewer.

The app continues recording in the background while the viewer is hidden.

## Build from source

Install [uv](https://astral.sh/uv), Node.js and npm, then run:

```sh
git clone https://github.com/porech/openrelife.git
cd openrelife
./install.sh
```

The script installs dependencies and builds the app under `electron-app/dist/`.
Local signing requires the certificate described in [CODESIGN.md](docs/CODESIGN.md).

The backend uses Flask and the desktop UI uses Electron. The server port is
configurable in **Settings → Advanced**; open the UI through the desktop app.
For development and backend command-line options, see the
[technical reference](docs/AGENT_INTEGRATION.md#development).

## Uninstall

Quit OpenReLife and move the app to the Trash. This keeps your screen history.
To permanently remove that history too, delete its data directory:

| Platform | Default data directory |
| --- | --- |
| macOS | `~/Library/Application Support/openrelife` |
| Windows | `%APPDATA%\openrelife` |
| Linux | `~/.local/share/openrelife` |

If you configured a custom archive location, use that directory instead.

## Contribute

Bug reports and pull requests are welcome on [GitHub](https://github.com/porech/openrelife).
If OpenReLife is useful to you, consider starring the project.

## License

[GNU General Public License v2](LICENSE).
