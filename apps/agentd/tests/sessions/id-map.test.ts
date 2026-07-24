import { describe, expect, it } from 'vitest';
import { IdMap, buildSessionId } from '../../src/sessions/id-map.js';

describe('sessions/id-map', () => {
  it('upserts and gets by id', () => {
    const m = new IdMap();
    const rec = { id: 'a1', provider: 'claude', createdAt: '2026-01-01T00:00:00Z', lastActiveAt: '2026-01-01T00:00:00Z' };
    m.upsert(rec);
    expect(m.get('a1')).toEqual(rec);
  });

  it('tracks the most recent agentd session per room', () => {
    const m = new IdMap();
    m.upsert({ id: 'a', provider: 'claude', roomId: 'r1', createdAt: '2026-01-01T00:00:00Z', lastActiveAt: '2026-01-01T00:00:00Z' });
    m.upsert({ id: 'b', provider: 'claude', roomId: 'r1', createdAt: '2026-01-02T00:00:00Z', lastActiveAt: '2026-01-02T00:00:00Z' });
    expect(m.byRoom('r1')?.id).toBe('b');
    expect(m.byRoom('r2')).toBeUndefined();
  });

  it('indexes by cliSessionId', () => {
    const m = new IdMap();
    m.upsert({ id: 'x', provider: 'claude', cliSessionId: 'cli-123', createdAt: '2026-01-01T00:00:00Z', lastActiveAt: '2026-01-01T00:00:00Z' });
    expect(m.byCliSessionId('cli-123')?.id).toBe('x');
  });

  it('delete removes from all indices', () => {
    const m = new IdMap();
    m.upsert({ id: 'a', provider: 'claude', roomId: 'r1', cliSessionId: 'c1', createdAt: '2026-01-01T00:00:00Z', lastActiveAt: '2026-01-01T00:00:00Z' });
    expect(m.delete('a')).toBe(true);
    expect(m.get('a')).toBeUndefined();
    expect(m.byRoom('r1')).toBeUndefined();
    expect(m.byCliSessionId('c1')).toBeUndefined();
  });

  it('buildSessionId returns a uuid-shaped string', () => {
    const id = buildSessionId();
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
  });
});