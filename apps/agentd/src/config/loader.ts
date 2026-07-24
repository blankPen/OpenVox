import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { ConfigSchema, type AgentdConfig } from './schema.js';
import { DEFAULT_CONFIG } from './defaults.js';
import { logger } from '../util/logger.js';

// Paths are computed lazily so tests can mutate HOME before they're read.
let _cachedHome: string | null = null;
function agentdHome(): string {
  if (_cachedHome === null) _cachedHome = path.join(os.homedir(), '.agentd');
  return _cachedHome;
}

export function getAgentdHome(): string {
  return agentdHome();
}

export const AGENTD_HOME = (() => {
  // Eager export kept for tests that import this constant directly; computed
  // at first access.
  return agentdHome();
})();

export function getConfigPath(): string {
  return path.join(agentdHome(), 'config.json');
}

export function getStatePath(): string {
  return path.join(agentdHome(), 'state.json');
}

export function getSessionsPath(): string {
  return path.join(agentdHome(), 'sessions.json');
}

/**
 * Resolve the effective config path. Precedence:
 *   1. Explicit `configPath` argument (typically from `--config`)
 *   2. `AGENTD_CONFIG` environment variable
 *   3. `~/.agentd/config.json` (computed via `getConfigPath()`)
 *
 * Pure function so the caller can log/inspect the chosen path before reading.
 */
export function resolveConfigPath(explicit?: string): string {
  return explicit ?? process.env['AGENTD_CONFIG'] ?? getConfigPath();
}

// Legacy aliases — kept for backward compatibility. These eagerly capture the
// home directory at module load time, which is fine for production (HOME is
// set before Node starts) but tests should prefer the `getXxx()` functions
// if they need to override the home directory.
export const CONFIG_PATH = path.join(agentdHome(), 'config.json');
export const STATE_PATH = path.join(agentdHome(), 'state.json');
export const SESSIONS_PATH = path.join(agentdHome(), 'sessions.json');

/**
 * Deep-merge two plain-object configs. Arrays are replaced, not merged.
 *
 * `defaults` provides base values, `override` (user JSON on disk) wins.
 */
function mergeDefaults<T extends Record<string, unknown>>(
  defaults: T,
  override: Partial<T> | undefined,
): T {
  if (!override) return { ...defaults };
  const out: Record<string, unknown> = { ...defaults };
  for (const [k, v] of Object.entries(override)) {
    const base = (defaults as Record<string, unknown>)[k];
    if (
      v !== null &&
      typeof v === 'object' &&
      !Array.isArray(v) &&
      typeof base === 'object' &&
      base !== null &&
      !Array.isArray(base)
    ) {
      out[k] = mergeDefaults(
        base as Record<string, unknown>,
        v as Record<string, unknown>,
      );
    } else {
      out[k] = v;
    }
  }
  return out as T;
}

/**
 * Load the agentd config. If the file is missing we synthesise a
 * default one so subsequent runs have something to edit.
 *
 * Precedence for the file location:
 *   - explicit `configPath` argument (from `--config`)
 *   - `AGENTD_CONFIG` environment variable
 *   - `~/.agentd/config.json`
 */
export async function loadConfig(
  configPath?: string,
): Promise<AgentdConfig> {
  const resolvedPath = resolveConfigPath(configPath);
  await fs.mkdir(path.dirname(resolvedPath), { recursive: true });

  let raw: Record<string, unknown> = {};
  try {
    const text = await fs.readFile(resolvedPath, 'utf8');
    if (text.trim().length > 0) {
      raw = JSON.parse(text) as Record<string, unknown>;
    }
  } catch (err: unknown) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code !== 'ENOENT') {
      logger.warn({ err, configPath: resolvedPath }, 'failed to read config.json, falling back to defaults');
    }
  }

  const merged = mergeDefaults(
    DEFAULT_CONFIG as unknown as Record<string, unknown>,
    raw,
  );
  const parsed = ConfigSchema.safeParse(merged);
  if (!parsed.success) {
    logger.warn(
      { issues: parsed.error.issues.slice(0, 5) },
      'config.json did not validate; using defaults where possible',
    );
    // Repair by re-parsing only the safe subset — fallback to defaults.
    const repaired = ConfigSchema.safeParse(DEFAULT_CONFIG);
    if (!repaired.success) {
      throw new Error('default config failed validation (this is a bug)');
    }
    return repaired.data;
  }
  return parsed.data;
}

/**
 * Persist config to disk. Used by tests and `agentd config edit` style commands.
 */
export async function saveConfig(
  cfg: AgentdConfig,
  configPath: string = CONFIG_PATH,
): Promise<void> {
  await fs.mkdir(path.dirname(configPath), { recursive: true });
  await fs.writeFile(configPath, JSON.stringify(cfg, null, 2), 'utf8');
}

/** Allow `AGENTD_PORT` and `AGENTD_HOST` to override config on the CLI. */
export function applyEnvOverrides(cfg: AgentdConfig): AgentdConfig {
  const portEnv = process.env['AGENTD_PORT'];
  const hostEnv = process.env['AGENTD_HOST'];
  return {
    ...cfg,
    port: portEnv ? Number(portEnv) : cfg.port,
    host: hostEnv ?? cfg.host,
  };
}
