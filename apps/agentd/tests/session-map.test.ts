/**
 * Tests for the session id map (room_id ↔ agentd ↔ cli_session_id) and
 * the SessionManager lifecycle.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { IdMap, buildSessionId } from '../src/sessions/id-map.js';
import { SessionManager } from '../src/sessions/manager.js';

describe('IdMap', () => {
  it('upsert and look up by id', () => {
    const m = new IdMap();
    const rec = {
      id: 'a1',
      provider: 'claude',
      createdAt: '2026-01-01T00:00:00Z',
      lastActiveAt: '2026-01-01T00:00:00Z',
    };
    m.upsert(rec);
    expect(m.get('a1')).toEqual(rec);
  });

  it('maps room_id → agentd_session_id (last wins)', () => {
    const m = new IdMap();
    m.upsert({
      id: 'a1',
      provider: 'claude',
      roomId: 'r1',
      createdAt: 'x',
      lastActiveAt: 'x',
    });
    m.upsert({
      id: 'a2',
      provider: 'claude',
      roomId: 'r1',
      createdAt: 'x',
      lastActiveAt: 'x',
    });
    expect(m.byRoom('r1')?.id).toBe('a2');
  });

  it('maps cli_session_id → agentd_session_id', () => {
    const m = new IdMap();
    m.upsert({
      id: 'a1',
      provider: 'claude',
      cliSessionId: 'cli-1',
      createdAt: 'x',
      lastActiveAt: 'x',
    });
    expect(m.byCliSessionId('cli-1')?.id).toBe('a1');
  });

  it('delete removes id and breaks all lookups', () => {
    const m = new IdMap();
    m.upsert({
      id: 'a1',
      provider: 'claude',
      roomId: 'r',
      cliSessionId: 'c',
      createdAt: 'x',
      lastActiveAt: 'x',
    });
    m.delete('a1');
    expect(m.get('a1')).toBeUndefined();
    expect(m.byRoom('r')).toBeUndefined();
    expect(m.byCliSessionId('c')).toBeUndefined();
  });

  it('buildSessionId returns RFC4122 v4 UUID', () => {
    const id = buildSessionId();
    expect(id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
  });
});

describe('SessionManager', () => {
  const TMP = path.join(os.tmpdir(), `agentd-mgr-${process.pid}`);
  let storePath: string;

  beforeEach(async () => {
    await fs.mkdir(TMP, { recursive: true });
    storePath = path.join(TMP, `sessions-${Math.random()}.json`);
  });

  it('creates sessions and exposes them', () => {
    const mgr = new SessionManager(storePath);
    const s = mgr.create({ provider: 'claude', roomId: 'rA' });
    expect(s.id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(mgr.get(s.id)?.provider).toBe('claude');
  });

  it('touches sessions and updates lastActiveAt', async () => {
    const mgr = new SessionManager(storePath);
    const s = mgr.create({ provider: 'claude' });
    const original = s.lastActiveAt;
    await new Promise((r) => setTimeout(r, 5));
    mgr.touch(s.id);
    expect(mgr.get(s.id)?.lastActiveAt).not.toBe(original);
  });

  it('sets cli_session_id and persists', async () => {
    const mgr = new SessionManager(storePath);
    const s = mgr.create({ provider: 'claude' });
    mgr.setCliSessionId(s.id, 'cli-uuid');
    expect(mgr.get(s.id)?.cliSessionId).toBe('cli-uuid');
    await new Promise((r) => setTimeout(r, 50));
    const text = await fs.readFile(storePath, 'utf8');
    expect(text).toContain('cli-uuid');
  });

  it('closes sessions and clears abort controllers', () => {
    const mgr = new SessionManager(storePath);
    const s = mgr.create({ provider: 'claude' });
    const signal = mgr.signal(s.id);
    expect(signal).toBeDefined();
    expect(signal?.aborted).toBe(false);
    void mgr.close(s.id);
    expect(mgr.get(s.id)).toBeUndefined();
  });

  it('load reads sessions from disk', async () => {
    await fs.writeFile(
      storePath,
      JSON.stringify([
        {
          id: 'restored-1',
          provider: 'claude',
          createdAt: '2026-01-01T00:00:00Z',
          lastActiveAt: '2026-01-01T00:00:00Z',
        },
      ]),
      'utf8',
    );
    const mgr = new SessionManager(storePath);
    await mgr.load();
    expect(mgr.get('restored-1')?.provider).toBe('claude');
  });
});
