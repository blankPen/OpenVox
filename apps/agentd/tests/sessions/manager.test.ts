import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { SessionManager } from '../../src/sessions/manager.js';

let tmpDir: string;
let storePath: string;

beforeEach(async () => {
  tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'agentd-sess-'));
  storePath = path.join(tmpDir, 'sessions.json');
});

afterEach(async () => {
  // Allow in-flight persist() writes to drain before removing the tmp dir.
  await new Promise((r) => setTimeout(r, 30));
  try {
    await fs.rm(tmpDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 50 });
  } catch {
    /* cleanup races are non-fatal */
  }
});

describe('sessions/manager', () => {
  it('creates a session with a uuid', () => {
    const m = new SessionManager(storePath);
    const s = m.create({ provider: 'claude', roomId: 'r1' });
    expect(s.id).toMatch(/^[0-9a-f-]{36}$/);
    expect(s.provider).toBe('claude');
    expect(s.roomId).toBe('r1');
  });

  it('touch updates lastActiveAt', async () => {
    const m = new SessionManager(storePath);
    const s = m.create({ provider: 'claude' });
    const before = s.lastActiveAt;
    await new Promise((r) => setTimeout(r, 5));
    m.touch(s.id);
    expect(m.get(s.id)?.lastActiveAt).not.toBe(before);
  });

  it('setCliSessionId persists and is queryable', async () => {
    const m = new SessionManager(storePath);
    const s = m.create({ provider: 'claude' });
    m.setCliSessionId(s.id, 'cli-abc');
    expect(m.get(s.id)?.cliSessionId).toBe('cli-abc');
    await new Promise((r) => setTimeout(r, 20));
    const text = await fs.readFile(storePath, 'utf8');
    expect(text).toContain('cli-abc');
  });

  it('byRoom returns the latest session for that room', () => {
    const m = new SessionManager(storePath);
    const a = m.create({ provider: 'claude', roomId: 'r1' });
    const b = m.create({ provider: 'claude', roomId: 'r1' });
    expect(m.byRoom('r1')?.id).toBe(b.id);
    expect(a.id).not.toBe(b.id);
  });

  it('close removes the session and aborts in-flight', async () => {
    const m = new SessionManager(storePath);
    const s = m.create({ provider: 'claude' });
    const signal = m.signal(s.id);
    expect(signal?.aborted).toBe(false);
    const ok = await m.close(s.id);
    expect(ok).toBe(true);
    expect(m.signal(s.id)).toBeUndefined();
    expect(m.get(s.id)).toBeUndefined();
    expect(await m.close(s.id)).toBe(false);
  });

  it('load reads back persisted sessions', async () => {
    const m1 = new SessionManager(storePath);
    const s = m1.create({ provider: 'claude', roomId: 'r1' });
    m1.setCliSessionId(s.id, 'cli-x');
    await new Promise((r) => setTimeout(r, 20));

    const m2 = new SessionManager(storePath);
    await m2.load();
    const loaded = m2.get(s.id);
    expect(loaded?.cliSessionId).toBe('cli-x');
    expect(loaded?.roomId).toBe('r1');
  });

  it('list returns all sessions', () => {
    const m = new SessionManager(storePath);
    m.create({ provider: 'claude' });
    m.create({ provider: 'codex' });
    expect(m.list()).toHaveLength(2);
  });
});