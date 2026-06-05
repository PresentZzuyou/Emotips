import { spawnSync } from 'node:child_process';
import { readdirSync, statSync, watchFile } from 'node:fs';
import { resolve } from 'node:path';

const rootDir = resolve(new URL('..', import.meta.url).pathname);
const debounceMs = 1500;
const once = process.argv.includes('--once');

let syncing = false;
let pending = false;
let timer = null;

function log(message) {
  console.log(`[auto-github] ${new Date().toLocaleTimeString()} ${message}`);
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: rootDir,
    encoding: 'utf8',
    stdio: options.quiet ? 'pipe' : 'inherit',
  });

  if (options.quiet) {
    return {
      ok: result.status === 0,
      stdout: result.stdout.trim(),
      stderr: result.stderr.trim(),
      status: result.status,
    };
  }

  return { ok: result.status === 0, status: result.status };
}

function git(args, options) {
  return run('git', args, options);
}

function hasRemote() {
  return git(['remote', 'get-url', 'origin'], { quiet: true }).ok;
}

function hasChanges() {
  const status = git(['status', '--porcelain'], { quiet: true });
  return status.ok && status.stdout.length > 0;
}

function syncToGitHub() {
  if (syncing) {
    pending = true;
    return;
  }

  syncing = true;
  pending = false;

  try {
    if (!hasChanges()) {
      log('No changes to sync.');
      return;
    }

    log('Committing local changes...');
    git(['add', '.']);

    const timestamp = new Date().toISOString().replace('T', ' ').slice(0, 19);
    const commit = git(['commit', '-m', `Auto update from Codex (${timestamp})`]);

    if (!commit.ok) {
      log('Commit failed. Check git user.name/user.email or conflicts.');
      return;
    }

    if (!hasRemote()) {
      log('Committed locally, but no GitHub origin remote is configured yet.');
      return;
    }

    log('Pushing to GitHub origin/main...');
    const push = git(['push', 'origin', 'main']);
    if (push.ok) {
      log('GitHub sync complete.');
    } else {
      log('Push failed. Check GitHub login, remote URL, or branch permissions.');
    }
  } finally {
    syncing = false;
    if (pending) syncToGitHub();
  }
}

function scheduleSync() {
  clearTimeout(timer);
  timer = setTimeout(syncToGitHub, debounceMs);
}

function htmlFiles() {
  return readdirSync(rootDir)
    .filter(name => name.endsWith('.html'))
    .map(name => resolve(rootDir, name));
}

if (once) {
  syncToGitHub();
} else {
  const watched = new Map();

  for (const file of htmlFiles()) {
    watched.set(file, statSync(file).mtimeMs);
    watchFile(file, { interval: 500 }, current => {
      const lastMtime = watched.get(file);
      if (current.mtimeMs === lastMtime) return;
      watched.set(file, current.mtimeMs);
      scheduleSync();
    });
  }

  log(`Watching HTML files in ${rootDir}`);
}
