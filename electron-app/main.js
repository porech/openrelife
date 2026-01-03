const { app, BrowserWindow, globalShortcut, screen, Tray, Menu, nativeImage, Notification, dialog, systemPreferences, desktopCapturer, shell } = require('electron');
const path = require('path');
const { spawn, exec, execSync } = require('child_process');
const fs = require('fs');

let serverPort = 8082;
let openRecallUrl = `http://127.0.0.1:${serverPort}`;
let mainWindow = null;
let tray = null;
let isPaused = false;
let pauseReminderInterval = null;


// Set explicit app name for notifications
if (process.platform === 'darwin') {
  app.setName('OpenReLife');
}

function hasScreenAccess() {
  return process.platform !== 'darwin' || systemPreferences.getMediaAccessStatus('screen') === 'granted';
}

function getBackendPort() {
  try {
    let settingsPath;
    if (process.platform === 'darwin') {
        // Backend uses lowercase 'openrelife'
        settingsPath = path.join(app.getPath('home'), 'Library', 'Application Support', 'openrelife', 'settings.json');
    } else if (process.platform === 'win32') {
        settingsPath = path.join(process.env.APPDATA, 'openrelife', 'settings.json');
    } else {
        settingsPath = path.join(app.getPath('home'), '.local', 'share', 'openrelife', 'settings.json');
    }

    if (fs.existsSync(settingsPath)) {
        const settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
        if (settings.server_port) {
            const port = parseInt(settings.server_port, 10);
            if (!isNaN(port)) {
                console.log(`Found configured port in settings: ${port}`);
                return port;
            }
        }
    }
  } catch (err) {
      console.error('Failed to read settings for port:', err);
  }
  console.log('Using default port 8082');
  return 8082;
}

function updateServerConfig() {
    serverPort = getBackendPort();
    openRecallUrl = `http://127.0.0.1:${serverPort}`;
}

function loadApp(retryCount = 0) {
  if (!mainWindow) return;

  if (retryCount === 0) console.log(`Attempting to connect to ${openRecallUrl}...`);

  fetch(openRecallUrl)
    .then(res => {
      if (res.ok) {
        console.log('✅ Backend connected! Loading app...');
        mainWindow.loadURL(openRecallUrl);
        if (process.platform === 'darwin' && hasScreenAccess()) {
           mainWindow.setSimpleFullScreen(true);
        }
        mainWindow.show();
        mainWindow.focus();
      } else {
        throw new Error(`Status check failed: ${res.status} ${res.statusText}`);
      }
    })
    .catch((err) => {
      // Log failure (every few attempts to avoid spam, but first few are important)
      if (retryCount < 5 || retryCount % 5 === 0) {
          console.log(`Backend connection failed (attempt ${retryCount}): ${err.cause ? err.cause.message : err.message}`);
      }
      setTimeout(() => loadApp(retryCount + 1), 1000);
    });
}

function createWindow() {
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width, height } = primaryDisplay.workAreaSize;
  const hasAccess = hasScreenAccess();

  // If we don't have screen access, don't cover the screen so user can see the prompt
  mainWindow = new BrowserWindow({
    width: hasAccess ? width : 900,
    height: hasAccess ? height : 600,
    center: !hasAccess,
    show: false, // Wait for ready-to-show
    frame: false,
    transparent: false,
    backgroundColor: '#000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      enableRemoteModule: false
    },
    skipTaskbar: true,
    simpleFullscreen: hasAccess,
    icon: path.join(__dirname, 'app-icon.png')
  });

  // Enable DevTools for debugging
  //mainWindow.webContents.openDevTools({ mode: 'detach' });

  if (!hasAccess && process.platform === 'darwin') {
      // Trigger the permission prompt
      desktopCapturer.getSources({ types: ['screen'] })
          .then(() => console.log('Permission check triggered'))
          .catch(err => console.error('Permission check error:', err));
      
      // Monitor for permission change
      const checkInterval = setInterval(() => {
          if (hasScreenAccess()) {
              clearInterval(checkInterval);
              if (mainWindow) {
                  mainWindow.setSimpleFullScreen(true);
                  mainWindow.setSize(width, height);
              }
          }
      }, 1000);
      mainWindow.on('closed', () => clearInterval(checkInterval));
  }

  // Load splash screen immediately
  const loadingPath = path.join(__dirname, 'loading.html');
  console.log('Loading splash screen from:', loadingPath);
  mainWindow.loadFile(loadingPath);
  
  // Start trying to connect to backend (it might be starting up)
  loadApp();

  mainWindow.once('ready-to-show', () => {
    console.log('✅ Window ready to show');
    mainWindow.show();
    mainWindow.focus();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // Handle navigation errors
  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    if (errorCode === -3) { // ERR_ABORTED is normal during navigation
      return;
    }
    console.error('Failed to load:', errorDescription);
    
    // Go back to loading screen if main app fails
    if (mainWindow) {
        mainWindow.loadFile(path.join(__dirname, 'loading.html'));
        setTimeout(loadApp, 1000);
    }
  });
} 

function showWindow() {
  if (mainWindow === null) {
    createWindow();
  }
  
  if (mainWindow) {
    mainWindow.webContents.send('reset-ui');
    if (hasScreenAccess()) {
        mainWindow.setSimpleFullScreen(true);
    } else {
        mainWindow.setSimpleFullScreen(false);
        // Maybe ensure reasonably sized if not fullscreen?
        if (mainWindow.getBounds().width < 100) {
             mainWindow.setSize(900, 600);
             mainWindow.center();
        }
    }
    mainWindow.show();
    mainWindow.focus();
  }
}

function hideWindow() {
  if (mainWindow) {
    mainWindow.webContents.send('reset-ui');
    mainWindow.setSimpleFullScreen(false);
    setTimeout(() => {
      if (mainWindow) {
        mainWindow.hide();
      }
    }, 100);
  }
}


async function checkRecordingStatus(retryCount = 0) {
  try {
    const res = await fetch(`${openRecallUrl}/api/recording-status`);
    if (!res.ok) throw new Error('Status check failed');
    const data = await res.json();
    isPaused = data.paused;
    updateTrayMenu();
    handlePauseReminder(isPaused);
  } catch (err) {
    if (retryCount < 10) {
        // Backend might be starting up, retry in 1s
        setTimeout(() => checkRecordingStatus(retryCount + 1), 1000);
    } else {
        console.error('Failed to check recording status after retries:', err);
    }
  }
}

async function toggleRecording() {
  try {
    const endpoint = isPaused ? '/api/resume-recording' : '/api/pause-recording';
    const res = await fetch(`${openRecallUrl}${endpoint}`, { method: 'POST' });
    const data = await res.json();
    isPaused = data.paused;
    updateTrayMenu();
    handlePauseReminder(isPaused);
  } catch (err) {
    console.error('Failed to toggle recording:', err);
  }
}

function handlePauseReminder(paused) {
  if (paused) {
    if (!pauseReminderInterval) {
      // Send reminder every 30 minutes
      pauseReminderInterval = setInterval(() => {
        const notification = new Notification({
          title: 'OpenReLife Paused',
          body: 'Recording is currently paused. Resume to capture your history.',
          silent: false,
          urgency: 'critical', // Attempt to make it more visible
          hasReply: false
        });
        
        notification.on('click', () => {
          showWindow();
        });
        
        notification.show();
      }, 30 * 60 * 1000); 
    }
  } else {
    if (pauseReminderInterval) {
      clearInterval(pauseReminderInterval);
      pauseReminderInterval = null;
    }
  }
}

function updateTrayMenu() {
  if (!tray) return;

  if (isPaused && trayIconPaused) {
    tray.setImage(trayIconPaused);
  } else if (trayIconNormal) {
    tray.setImage(trayIconNormal);
  }

  const contextMenu = Menu.buildFromTemplate([
    {
      label: isPaused ? 'Resume Recording' : 'Pause Recording',
      click: () => toggleRecording(),
      icon: isPaused ? null : null // Could add icon here
    },
    { type: 'separator' },
    { 
      label: 'Show OpenReLife', 
      click: () => showWindow(),
      accelerator: 'CommandOrControl+Shift+Space'
    },
    { 
      label: 'Settings', 
      click: () => {
        showWindow();
        if (mainWindow) {
            mainWindow.webContents.send('open-settings');
        }
      }
    },
    { type: 'separator' },
    { 
      label: 'About OpenReLife',
      click: () => {
        dialog.showMessageBox({
          type: 'info',
          title: 'About OpenReLife',
          message: 'OpenReLife v1.0.0',
          detail: 'Screen Memory (powered by AI) - made with ❤️ by Porech - https://github.com/porech/openrelife',
          buttons: ['OK'],
          icon: path.join(__dirname, 'app-icon.png')
        });
      }
    },
    { type: 'separator' },
    { 
      label: 'Quit', 
      click: () => {
        app.isQuitting = true;
        app.quit();
      },
      accelerator: 'CommandOrControl+Q'
    }
  ]);
  
  tray.setToolTip(isPaused ? 'OpenReLife (Paused)' : 'OpenReLife - Recording active');
  tray.setContextMenu(contextMenu);
}


let trayIconNormal = null;
let trayIconPaused = null;

function createTray() {
  const iconPath = path.join(__dirname, 'tray-iconTemplate.png');
  const pausedIconPath = path.join(__dirname, 'tray-icon-pausedTemplate.png');
  
  const icon = nativeImage.createFromPath(iconPath);
  const pausedIcon = nativeImage.createFromPath(pausedIconPath);
  
  trayIconNormal = icon.resize({ width: 22 });
  trayIconNormal.setTemplateImage(true);
  
  trayIconPaused = pausedIcon.resize({ width: 22 });
  trayIconPaused.setTemplateImage(true);
  
  tray = new Tray(trayIconNormal);
  updateTrayMenu();
}



let pythonProcess = null;
let backendLogStream = null;

function killProcessOnPort(port) {
  return new Promise((resolve) => {
    // Only verify on macOS/Linux/Unix
    if (process.platform === 'win32') {
        resolve();
        return;
    }

    exec(`lsof -i :${port} -sTCP:LISTEN -t`, (err, stdout) => {
      if (err || !stdout) {
        // No process found or error (e.g. lsof not installed)
        resolve();
        return;
      }
      const pids = stdout.trim().split('\n');
      console.log(`Found process(es) on port ${port}: ${pids.join(', ')}. Killing...`);
      // Kill PIDs
      exec(`kill -9 ${pids.join(' ')}`, (killErr) => {
         if (killErr) console.error(`Failed to kill process on port ${port}:`, killErr);
         else console.log(`Successfully killed process(es) on port ${port}`);
         resolve();
      });
    });
  });
}

// ... (other helper functions from before, if any, can be kept or redefined if needed)

function getUvBinary() {
    const isDev = !app.isPackaged;
    // In dev, we look in local bin/ folder. In prod, it's in resources/bin/
    const baseDir = isDev 
        ? path.join(__dirname, '..', 'bin')
        : path.join(process.resourcesPath, 'bin');
    
    let binaryName = 'uv-x64'; // Default to x64
    if (process.arch === 'arm64') {
        binaryName = 'uv-arm64';
    }
    
    const binaryPath = path.join(baseDir, binaryName);
    return binaryPath;
}

function getVenvPath() {
    // We want the venv to be in a writable location
    const userDataPath = app.getPath('userData') || app.getPath('appData');
    // On macOS: ~/Library/Application Support/OpenReLife/venv
    return path.join(userDataPath, 'venv');
}

function updateStatus(message) {
  if (mainWindow) {
    mainWindow.webContents.send('update-status', message);
  }
}

async function setupVenv(projectRoot, logStream) {
    const venvPath = getVenvPath();
    const uvBinary = getUvBinary();
    
    // Wait for window to be ready to receive IPC messages
    await new Promise(r => setTimeout(r, 1000));
    
    console.log(`Checking venv at: ${venvPath}`);
    updateStatus('Checking Python environment...');

    if (logStream) {
        logStream.write(`Venv Path: ${venvPath}\n`);
        logStream.write(`UV Binary: ${uvBinary}\n`);
    }

    // Basic check if python exists in venv
    const pythonPath = path.join(venvPath, 'bin', 'python');
    const venvExists = fs.existsSync(pythonPath);
    
    if (venvExists) {
        console.log('✅ Found existing venv');
        updateStatus('Environment found. Starting backend...');
        await new Promise(r => setTimeout(r, 500));
        return venvPath;
    }

    console.log('⚠️ Venv missing or incomplete. Creating/Syncing...');
    if (logStream) logStream.write('Creating/Syncing venv...\n');
    updateStatus('Initializing Python environment (this may take a minute)...');

    // Ensure directory exists
    if (!fs.existsSync(path.dirname(venvPath))) {
        fs.mkdirSync(path.dirname(venvPath), { recursive: true });
    }
    
    try {
        const env = {
            ...process.env,
            UV_PROJECT_ENVIRONMENT: venvPath,
            UV_CACHE_DIR: path.join(app.getPath('userData'), 'uv-cache')
        };
        
        if (!fs.existsSync(env.UV_CACHE_DIR)) {
            fs.mkdirSync(env.UV_CACHE_DIR, { recursive: true });
        }

        try { fs.chmodSync(uvBinary, 0o755); } catch {}

        console.log(`Running: ${uvBinary} sync --frozen`);
        if (logStream) logStream.write(`Running uv sync --frozen...\n`);
        
        updateStatus('Installing dependencies...');

        await new Promise((resolve, reject) => {
            const child = spawn(uvBinary, ['sync', '--frozen'], {
                cwd: projectRoot,
                env: env
            });
            
            child.stdout.on('data', (data) => {
                const str = data.toString();
                console.log(`uv: ${str}`);
            });
            
            child.stderr.on('data', (data) => {
                const str = data.toString();
                console.error(`uv stderr: ${str}`);
                if (logStream) logStream.write(`uv stderr: ${str}`);
            });
            
            child.on('close', (code) => {
                if (code === 0) resolve();
                else reject(new Error(`uv sync exited with code ${code}`));
            });
            
            child.on('error', (err) => reject(err));
        });
        
        console.log('✅ Venv setup complete');
        if (logStream) logStream.write('Venv setup complete\n');
        
    } catch (err) {
        updateStatus('Error setting up environment.');
        console.error('Failed to setup venv:', err);
        if (logStream) {
             logStream.write(`Failed to setup venv: ${err.message}\n`);
        }
        throw err;
    }

    return venvPath;
}

async function startBackend() {
  // Update port config before starting
  updateServerConfig();
  
  // First, check if port is occupied and kill it
  await killProcessOnPort(serverPort);

  const isDev = !app.isPackaged;
  // In prod, resourcesPath points to Contents/Resources where we copied openrelife and pyproject.toml
  const projectRoot = isDev ? path.join(__dirname, '..') : process.resourcesPath;

  console.log('Starting backend using files in:', projectRoot);
  
  // Set up logging
  const userDataPath = app.getPath('userData');
  const logsDir = path.join(userDataPath, 'logs');
  if (!fs.existsSync(logsDir)) {
      fs.mkdirSync(logsDir, { recursive: true });
  }
  const logPath = path.join(logsDir, 'backend.log');
  console.log('Backend logs will be written to:', logPath);
  
  try {
    backendLogStream = fs.createWriteStream(logPath, { flags: 'a' });
  } catch (err) {
    console.error('Failed to create log stream:', err);
  }
  
  // SETUP VENV
  let venvPath;
  try {
      // internal logging already done in setupVenv
      venvPath = await setupVenv(projectRoot, backendLogStream);
  } catch (err) {
      throw err; // Re-throw to be caught by app.whenReady
  }

  // Determine python executable in the venv
  const pythonExec = path.join(venvPath, 'bin', 'python');

  const env = { 
    ...process.env, 
    PYTHONUNBUFFERED: '1',
    VIRTUAL_ENV: venvPath,
    PATH: `${path.join(venvPath, 'bin')}:${process.env.PATH}`,
    // Ensure we don't pick up other envs
    PYTHONPATH: '' 
  };
  
  console.log('Spawning backend with python:', pythonExec);

  pythonProcess = spawn(pythonExec, ['-m', 'openrelife.app'], {
    cwd: projectRoot,
    shell: false, 
    env: env,
    detached: false 
  });
  
  if (backendLogStream) {
     const timestamp = new Date().toISOString();
     backendLogStream.write(`\n--- New Session: ${timestamp} ---\n`);
  }

  pythonProcess.stdout.on('data', (data) => {
    if (backendLogStream) backendLogStream.write(data);
    console.log(`Backend: ${data}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    if (backendLogStream) backendLogStream.write(data);
    const str = data.toString();
    if (str.includes('Error:') || str.includes('Exception:') || str.includes('Traceback')) {
        console.error(`Backend Error: ${str}`);
    } else {
        console.log(`Backend Log: ${str}`); 
    }
  });
  
  pythonProcess.on('error', (err) => {
    console.error('Failed to spawn python process:', err);
    if (backendLogStream) backendLogStream.write(`Failed to spawn: ${err.message}\n`);
    dialog.showErrorBox('Backend Error', `Failed to start backend: ${err.message}`);
  });

  pythonProcess.on('close', (code) => {
    console.log(`Backend exited with code ${code}`);
    if (backendLogStream) backendLogStream.write(`Backend exited with code ${code}\n`);
    pythonProcess = null;
    
    // If exit was abnormal and not during quit
    if (code !== 0 && !app.isQuitting) {
        let errorDetails = `Code: ${code}`;
        try {
            if (fs.existsSync(logPath)) {
                const logs = fs.readFileSync(logPath, 'utf8');
                const lines = logs.trim().split('\n');
                errorDetails = lines.slice(-10).join('\n');
            }
        } catch (e) {}

        dialog.showMessageBox({
            type: 'error',
            title: 'Backend Failed',
            message: 'The backend service stopped unexpectedly.',
            detail: errorDetails,
            buttons: ['OK', 'Open Logs']
        }).then(({ response }) => {
            if (response === 1) {
                shell.openPath(logPath);
            }
        });
    }
  });
}

function stopBackend() {
  if (pythonProcess) {
    console.log('Stopping backend...');
    if (backendLogStream) backendLogStream.end();

    if (process.platform === 'win32') {
        spawn("taskkill", ["/pid", pythonProcess.pid, '/f', '/t']);
    } else {
        try {
            // Kill the entire process group
            process.kill(-pythonProcess.pid, 'SIGTERM'); 
        } catch (err) {
            console.error('Failed to kill process group:', err);
            // Fallback
            try {
                pythonProcess.kill();
            } catch (e) {
                console.error('Failed to kill process:', e);
            }
        }
    }
    pythonProcess = null;
  }
}

app.whenReady().then(async () => {
  // Show window immediately (with loading screen)
  createWindow();
  
  if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
  }

  // Set app icon (works for dock in dev mode)
  if (process.platform === 'darwin') {
    app.dock.setIcon(path.join(__dirname, 'app-icon.png'));
  }

  // Start backend server (this will take time on first run)
  // The loading screen is already visible, so user sees progress.
  // We don't await this blocking UI, but we await it before setting up tray?
  // Actually, we should await it, but since window is already shown, it's fine.
  // Start backend server (this will take time on first run)
  // The loading screen is already visible, so user sees progress.
  try {
      await startBackend();
  } catch (err) {
      console.error('Failed to start backend:', err);
      updateStatus('Fatal Error: Failed to start backend.');
      dialog.showErrorBox('Startup Error', `Failed to start application backend:\n${err.message}`);
      // Don't quit immediately so they can read it, but nothing else will happen
  }

  // Create Application Menu
  createMenu();

  // Create tray icon first
  createTray();
  
  // Check initial recording status
  checkRecordingStatus();
  
  // Don't show app in dock
  app.dock.hide();
  
  // Register global shortcut: Cmd+Shift+Space
  // ...
  const ret = globalShortcut.register('CommandOrControl+Shift+Space', () => {
    // ...
    console.log('🎯 Hotkey pressed: Cmd+Shift+Space');
    if (mainWindow && mainWindow.isVisible()) {
      // If already visible, hide and show again to move to current virtual desktop
      console.log('♻️ Refreshing window position...');
      hideWindow();
      setTimeout(showWindow, 300);
      return;
    }
    showWindow();
  });
  
  // ...


  if (!ret) {
    console.error('❌ Failed to register global shortcut');
  }

  // Listen for hide-window from renderer
  const { ipcMain } = require('electron');
  ipcMain.on('hide-window', () => {
    if (mainWindow && mainWindow.isVisible()) {
      console.log('🔒 ESC pressed in app - hiding window');
      hideWindow();
    }
  });

  console.log('='.repeat(50));
  console.log('🎯 OpenReLife Electron App');
  console.log('='.repeat(50));
  console.log('⌨️  Cmd+Shift+Space: Open/Focus OpenReLife');
  console.log('⎋  ESC: Close OpenReLife window');
  console.log('📍 Tray icon: Click to open');
  console.log('='.repeat(50));

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  // Keep app running even if window is closed
  // Only quit if explicitly requested
  if (app.isQuitting) {
    app.quit();
  }
});

app.on('will-quit', () => {
  // Unregister all shortcuts
  globalShortcut.unregisterAll();
  stopBackend();
});

// Prevent app from quitting
app.on('before-quit', (event) => {
  // stopBackend(); // will-quit handles it
  // Allow quit only if explicitly requested
});

function createMenu() {
  const isMac = process.platform === 'darwin';
  
  const template = [
    // { role: 'appMenu' }
    ...(isMac ? [{
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { 
            label: 'Quit OpenReLife',
            accelerator: 'Command+Q',
            click: () => { app.isQuitting = true; app.quit(); }
        }
      ]
    }] : []),
    // { role: 'fileMenu' }
    {
      label: 'File',
      submenu: [
        { 
            label: 'Close Window',
            accelerator: 'CmdOrCtrl+W',
            click: () => hideWindow()
        },
        isMac ? { role: 'close' } : { role: 'quit' }
      ]
    },
    // { role: 'editMenu' }
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'delete' },
        { role: 'selectAll' }
      ]
    },
    // { role: 'viewMenu' }
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
      ]
    },
    {
      label: 'Window',
      submenu: [
        { role: 'minimize' },
        { role: 'zoom' },
        ...(isMac ? [
          { type: 'separator' },
          { role: 'front' },
          { type: 'separator' },
          { role: 'window' }
        ] : [
          { role: 'close' }
        ])
      ]
    }
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}
