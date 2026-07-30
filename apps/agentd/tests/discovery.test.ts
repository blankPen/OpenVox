/**
 * Tests CLI binary discovery + cached state persistence.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {
  discoverProviders,
  readDiscoveryState,
  writeDiscoveryState,
} from '../src/providers/discovery.js';

const TMP = path.join(os.tmpdir(), `agentd-test-${process.pid}`);

beforeEach(async () => {
  await fs.mkdir(TMP, { recursive: true });
});

afterEach(async () => {
  await fs.rm(TMP, { recursive: true, force: true });
});

describe('discovery', () => {
  it('finds `claude` on PATH (machine integration test)', async () => {
    const state = path.join(TMP, 'state.json');
    const providers = await discoverProviders(state);
    const claude = providers.find((p) => p.id === 'claude');
    expect(claude).toBeDefined();
    expect(claude?.path.length).toBeGreaterThan(0);
  });

  it('returns empty list when no known binaries exist', async () => {
    const prevPath = process.env['PATH'];
    const prevHome = process.env['HOME'];
    const fakeHome = path.join(TMP, 'fake-home');
    await fs.mkdir(fakeHome, { recursive: true });
    process.env['PATH'] = '/this/does/not/exist';
    process.env['HOME'] = fakeHome;
    try {
      const state = path.join(TMP, 'state-empty.json');
      const providers = await discoverProviders(state);
      expect(providers).toEqual([]);
    } finally {
      process.env['PATH'] = prevPath;
      process.env['HOME'] = prevHome;
    }
  });

  it('persists discovery state to disk', async () => {
    const state = path.join(TMP, 'persist.json');
    const sample = [
      { id: 'claude', path: '/x/claude', version: '1.0', protocol: 'stream-json' as const },
    ];
    await writeDiscoveryState(
      { providers: sample, lastRun: new Date().toISOString() },
      state,
    );
    const read = await readDiscoveryState(state);
    expect(read.providers).toEqual(sample);
  });

  it('reads empty state gracefully on missing file', async () => {
    const state = path.join(TMP, 'missing.json');
    const read = await readDiscoveryState(state);
    expect(read.providers).toEqual([]);
  });
});
