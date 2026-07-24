import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {
  applyEnvOverrides,
  loadConfig,
  saveConfig,
  STATE_PATH,
} from '../../src/config/loader.js';

let tmpDir: string;
let configPath: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'agentd-test-'));
  configPath = path.join(tmpDir, 'config.json');
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe('config/loader', () => {
  it('returns defaults when config file is missing', async () => {
    const cfg = await loadConfig(configPath);
    expect(cfg.port).toBe(8787);
    expect(cfg.providers).toEqual([]);
  });

  it('merges user values over defaults', async () => {
    await fs.writeFile(configPath,
      JSON.stringify({ port: 9999, auth: { tokens: ['x'] } }), 'utf8');
    const cfg = await loadConfig(configPath);
    expect(cfg.port).toBe(9999);
    expect(cfg.auth.tokens).toEqual(['x']);
    expect(cfg.host).toBe('127.0.0.1');
    expect(cfg.sessionTtlSeconds).toBe(1800);
  });

  it('falls back to defaults when JSON is malformed', async () => {
    await fs.writeFile(configPath, '{ not valid json', 'utf8');
    const cfg = await loadConfig(configPath);
    expect(cfg.port).toBe(8787);
  });

  it('saveConfig then loadConfig round-trips', async () => {
    const cfg = await loadConfig(configPath);
    cfg.port = 12345;
    cfg.auth.tokens.push('round-trip');
    await saveConfig(cfg, configPath);
    const reloaded = await loadConfig(configPath);
    expect(reloaded.port).toBe(12345);
    expect(reloaded.auth.tokens).toContain('round-trip');
  });

  it('applyEnvOverrides respects AGENTD_PORT and AGENTD_HOST', () => {
    const original = process.env['AGENTD_PORT'];
    const originalHost = process.env['AGENTD_HOST'];
    try {
      process.env['AGENTD_PORT'] = '5555';
      process.env['AGENTD_HOST'] = '0.0.0.0';
      const cfg = applyEnvOverrides({
        port: 8787, host: '127.0.0.1', logLevel: 'info',
        sessionTtlSeconds: 1800, maxConcurrentPerProvider: 4,
        rateLimit: { max: 60, windowMs: 60_000 },
        auth: { tokens: [] }, providers: [],
        cliOAuth: { probeClaudeCredentials: true },
        acp: { serverSocket: null },
      });
      expect(cfg.port).toBe(5555);
      expect(cfg.host).toBe('0.0.0.0');
    } finally {
      if (original === undefined) delete process.env['AGENTD_PORT'];
      else process.env['AGENTD_PORT'] = original;
      if (originalHost === undefined) delete process.env['AGENTD_HOST'];
      else process.env['AGENTD_HOST'] = originalHost;
    }
  });

  it('exports STATE_PATH under ~/.agentd', () => {
    expect(STATE_PATH).toBe(path.join(os.homedir(), '.agentd', 'state.json'));
  });
});