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
The app is not signed and new MacOS versions will block the opening, saying it's "damaged" and suggesting to throw it in the trash; the app is not damaged: to fix this, you need to open a terminal, write down the following command (do not run it yet):

```bash
sudo xattr -d com.apple.quarantine
```
now drag'n'drop the .app file (from Applications folder) in the terminal to add it's full path, then run it pressing enter.

for me, the full line was:

```bash
sudo xattr -d com.apple.quarantine /System/Volumes/Data/Applications/OpenReLife.app
```

Then, just re-open the app from the Applications folder and it will work.

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

