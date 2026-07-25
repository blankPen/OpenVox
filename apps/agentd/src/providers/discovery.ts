import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { STATE_PATH } from '../config/loader.js';

export interface DiscoveredProvider {
  /** Binary name (e.g. `claude`). */
  id: string;
  /** Absolute path to the binary on disk (best effort). */
  path: string;
  version: string;
  /** Discovered protocol — best guess, may be overridden by user config. */
  protocol: 'stream-json' | 'openai-http' | 'acp' | 'jsonrpc' | 'unknown';
}

const PROBES: Array<{
  id: string;
  protocols: DiscoveredProvider['protocol'][];
}> = [
  { id: 'claude', protocols: ['stream-json'] },
  { id: 'codex', protocols: ['jsonrpc'] },
  { id: 'openclaw', protocols: ['openai-http'] },
];

/** Resolve a binary by walking PATH and a few well-known dirs. */
async function which(bin: string): Promise<string | null> {
  const pathEnv = process.env['PATH'] ?? '';
  const dirs = pathEnv.split(path.delimiter).filter(Boolean);
  for (const dir of dirs) {
    const candidate = path.join(dir, bin);
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      /* not here */
    }
  }
  // ~/.local/bin fallback (Claude Code self-installs there).
  try {
    const fallback = path.join(os.homedir(), '.local', 'bin', bin);
    await fs.access(fallback);
    return fallback;
  } catch {
    /* not here */
  }
  return null;
}

/** Run `<bin> --version` with a timeout. Returns version string or null. */
function probeVersion(binPath: string): Promise<string | null> {
  return new Promise((res) => {
    let stdout = '';
    const child = spawn(binPath, ['--version'], { stdio: ['ignore', 'pipe', 'pipe'] });
    const timer = setTimeout(() => {
      try {
        child.kill('SIGKILL');
      } catch {
        /* ignore */
      }
    }, 5_000);
    child.stdout?.on('data', (c: Buffer) => {
      stdout += c.toString('utf8');
    });
    child.once('error', () => {
      clearTimeout(timer);
      res(null);
    });
    child.once('exit', (code) => {
      clearTimeout(timer);
      if (code === 0) {
        res(stdout.trim().split('\n')[0] ?? '');
      } else {
        res(null);
      }
    });
  });
}

interface DiscoveryState {
  providers: DiscoveredProvider[];
  lastRun: string;
}

const EMPTY_STATE: DiscoveryState = { providers: [], lastRun: '' };

/** Read cached discovery state from disk. */
export async function readDiscoveryState(
  statePath: string = STATE_PATH,
): Promise<DiscoveryState> {
  try {
    const text = await fs.readFile(statePath, 'utf8');
    const obj = JSON.parse(text) as Partial<DiscoveryState>;
    if (Array.isArray(obj.providers)) {
      return {
        providers: obj.providers as DiscoveredProvider[],
        lastRun: obj.lastRun ?? '',
      };
    }
  } catch {
    /* missing or invalid */
  }
  return EMPTY_STATE;
}

/** Persist discovery state to disk. */
export async function writeDiscoveryState(
  state: DiscoveryState,
  statePath: string = STATE_PATH,
): Promise<void> {
  await fs.mkdir(path.dirname(statePath), { recursive: true });
  await fs.writeFile(statePath, JSON.stringify(state, null, 2), 'utf8');
}

/**
 * Walk PATH, probe each known binary, return a list of detected providers.
 * Cached results are returned as-is for binaries that still exist on disk,
 * avoiding re-probing on every daemon restart.
 */
export async function discoverProviders(
  statePath: string = STATE_PATH,
): Promise<DiscoveredProvider[]> {
  const cached = await readDiscoveryState(statePath);
  const out: DiscoveredProvider[] = [];

  for (const probe of PROBES) {
    const found = await which(probe.id);
    if (!found) continue;

    // Was this binary previously known?
    const prev = cached.providers.find((p) => p.id === probe.id && p.path === found);
    if (prev) {
      out.push(prev);
      continue;
    }

    const version = await probeVersion(found);
    if (version === null) continue;

    out.push({
      id: probe.id,
      path: found,
      version,
      protocol: probe.protocols[0] ?? 'unknown',
    });
  }

  const newState: DiscoveryState = { providers: out, lastRun: new Date().toISOString() };
  await writeDiscoveryState(newState, statePath);
  return out;
}
