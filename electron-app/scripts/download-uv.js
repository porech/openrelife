const fs = require('fs');
const path = require('path');
const https = require('https');
const { execSync } = require('child_process');

const UV_VERSION = '0.5.11'; // Pin a specific version
const BIN_DIR = path.join(__dirname, '..', 'bin');

if (!fs.existsSync(BIN_DIR)) {
    fs.mkdirSync(BIN_DIR, { recursive: true });
}

// Map of platform/arch to uv release filenames
const TARGETS = [
    {
        platform: 'darwin',
        arch: 'x64',
        url: `https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-apple-darwin.tar.gz`,
        binaryName: 'uv-x64'
    },
    {
        platform: 'darwin',
        arch: 'arm64',
        url: `https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-aarch64-apple-darwin.tar.gz`,
        binaryName: 'uv-arm64'
    }
];

async function downloadFile(url, dest) {
    return new Promise((resolve, reject) => {
        const file = fs.createWriteStream(dest);
        https.get(url, (response) => {
            if (response.statusCode === 302 || response.statusCode === 301) {
                downloadFile(response.headers.location, dest).then(resolve).catch(reject);
                return;
            }
            response.pipe(file);
            file.on('finish', () => {
                file.close(resolve);
            });
        }).on('error', (err) => {
            fs.unlink(dest, () => reject(err));
        });
    });
}

async function install() {
    console.log(`Downloading uv ${UV_VERSION}...`);
    
    for (const target of TARGETS) {
        console.log(`Processing ${target.binaryName}...`);
        const tarPath = path.join(BIN_DIR, `${target.binaryName}.tar.gz`);
        const extractPath = path.join(BIN_DIR, `temp-${target.binaryName}`);
        
        // Download
        if (!fs.existsSync(tarPath)) { // simple cache check
             await downloadFile(target.url, tarPath);
        }

        // Extract
        if (!fs.existsSync(extractPath)) {
            fs.mkdirSync(extractPath);
        }
        
        try {
            execSync(`tar -xzf "${tarPath}" -C "${extractPath}"`);
            
            // Find binary (it's usually in a subdir like uv-x86_64-apple-darwin/uv)
            const findBinary = (dir) => {
                const files = fs.readdirSync(dir);
                for (const file of files) {
                    const fullPath = path.join(dir, file);
                    if (fs.statSync(fullPath).isDirectory()) {
                        const res = findBinary(fullPath);
                        if (res) return res;
                    } else if (file === 'uv') {
                        return fullPath;
                    }
                }
                return null;
            };

            const binaryPath = findBinary(extractPath);
            if (!binaryPath) throw new Error('Could not find uv binary in archive');

            // Move to final location
            const finalPath = path.join(BIN_DIR, target.binaryName);
            fs.copyFileSync(binaryPath, finalPath);
            fs.chmodSync(finalPath, 0o755);
            console.log(`Installed to ${finalPath}`);

        } catch (e) {
            console.error(`Failed to process ${target.binaryName}:`, e);
        } finally {
            // Cleanup
            fs.rmSync(extractPath, { recursive: true, force: true });
            fs.unlinkSync(tarPath);
        }
    }
}

install().catch(console.error);
