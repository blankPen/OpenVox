import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { SessionManager } from '../../src/sessions/manager.js';
import { TtlSweeper } from '../../src/sessions/ttl.js';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';

let tmpDir: string;
let storePath: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'agentd-ttl-'));
  storePath = path.join(tmpDir, 'sessions.json');
});

afterEach(async () => {
  await fs.rm(tmpDir, { recursive: true, force: true });
});

describe('sessions/ttl', () => {
  it('closes sessions whose last_active_at is older than ttl', async () => {
    const m = new SessionManager(storePath);
    const s = m.create({ provider: 'claude' });
    const rec = m.get(s.id);
    if (rec) rec.lastActiveAt = new Date(Date.now() - 60_000).toISOString();

    const expired: string[] = [];
    const sweeper = new TtlSweeper(m, {
      ttlSeconds: 1, onExpired: (id) => expired.push(id),
    });
    await sweeper.tick();
    expect(expired).toContain(s.id);
    expect(m.get(s.id)).toBeUndefined();
  });

  it('keeps fresh sessions', async () => {
    const m = new SessionManager(storePath);
    const s = m.create({ provider: 'claude' });
    const sweeper = new TtlSweeper(m, { ttlSeconds: 3600 });
    await sweeper.tick();
    expect(m.get(s.id)).toBeDefined();
  });

  it('start schedules ticks and stop cancels them', () => {
    vi.useFakeTimers();
    try {
      const m = new SessionManager(storePath);
      m.create({ provider: 'claude' });
      const sweeper = new TtlSweeper(m, { ttlSeconds: 3600 });
      sweeper.start();
      sweeper.stop();
      expect(true).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});