import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import {
  discoverProviders,
  readDiscoveryState,
  writeDiscoveryState,
} from '../../src/providers/discovery.js';

/**
 * Same gating as tests/discovery.test.ts: skip when `claude` is not on
 * PATH so CI runners without the binary still report green.
 */
function hasClaudeOnPath(): boolean {
  const probe = spawnSync('which', ['claude'], { encoding: 'utf8' });
  return probe.status === 0;
}
const claudeAvailable = hasClaudeOnPath();

let tmpDir: string;
let statePath: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'agentd-discovery-'));
  statePath = path.join(tmpDir, 'state.json');
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe('providers/discovery', () => {
  it('readDiscoveryState returns empty when file is missing', async () => {
    const s = await readDiscoveryState(statePath);
    expect(s.providers).toEqual([]);
  });

  it('writeDiscoveryState round-trips', async () => {
    const providers = [{
      id: 'claude', path: '/usr/local/bin/claude', version: '2.1.202', protocol: 'stream-json' as const,
    }];
    await writeDiscoveryState({ providers, lastRun: '2026-01-01T00:00:00Z' }, statePath);
    const s = await readDiscoveryState(statePath);
    expect(s.providers).toEqual(providers);
    expect(s.lastRun).toBe('2026-01-01T00:00:00Z');
  });

  it('readDiscoveryState recovers from corrupt JSON', async () => {
    await fs.writeFile(statePath, '{ not json', 'utf8');
    const s = await readDiscoveryState(statePath);
    expect(s.providers).toEqual([]);
  });

  it.skipIf(!claudeAvailable)('discoverProviders returns at least the installed claude binary', async () => {
    const providers = await discoverProviders(statePath);
    expect(providers.map((p) => p.id)).toContain('claude');
    for (const p of providers) {
      expect(p.path.length).toBeGreaterThan(0);
      expect(p.version.length).toBeGreaterThan(0);
    }
  }, 30_000);

  it('discoverProviders caches results in state.json', async () => {
    const first = await discoverProviders(statePath);
    const second = await discoverProviders(statePath);
    expect(second.map((p) => p.id).sort()).toEqual(first.map((p) => p.id).sort());
  }, 30_000);
});