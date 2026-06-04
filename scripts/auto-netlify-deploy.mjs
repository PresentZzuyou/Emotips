import { spawn } from 'node:child_process';
import { statSync, watchFile } from 'node:fs';
import { resolve } from 'node:path';

const rootDir = resolve(new URL('..', import.meta.url).pathname);
const targetFile = resolve(rootDir, 'index.html');
const debounceMs = 1500;

let lastMtime = statSync(targetFile).mtimeMs;
let timer = null;
let deploying = false;
let pending = false;

function log(message) {
  console.log(`[auto-netlify] ${new Date().toLocaleTimeString()} ${message}`);
}

function runDeploy() {
  if (deploying) {
    pending = true;
    return;
  }

  deploying = true;
  pending = false;
  log('Deploying index.html to Netlify production...');

  const deploy = spawn('netlify', ['deploy', '--prod', '--dir', '.'], {
    cwd: rootDir,
    stdio: 'inherit',
  });

  deploy.on('exit', code => {
    deploying = false;
    if (code === 0) {
      log('Deploy complete.');
    } else {
      log(`Deploy exited with code ${code}.`);
    }

    if (pending) {
      runDeploy();
    }
  });
}

watchFile(targetFile, { interval: 500 }, current => {
  if (current.mtimeMs === lastMtime) return;

  lastMtime = current.mtimeMs;
  clearTimeout(timer);
  timer = setTimeout(runDeploy, debounceMs);
});

log(`Watching ${targetFile}`);
