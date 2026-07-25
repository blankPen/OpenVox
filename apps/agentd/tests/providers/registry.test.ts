import { describe, expect, it } from 'vitest';
import { FACTORIES, ProviderRegistry, listFactoryIds } from '../../src/providers/registry.js';

describe('providers/registry', () => {
  it('exposes the four built-in factories', () => {
    expect(listFactoryIds().sort()).toEqual(['claude', 'codex', 'generic-acp', 'openclaw']);
    expect(FACTORIES['claude']?.id).toBe('claude');
    expect(FACTORIES['codex']?.binaryIds).toContain('codex');
  });

  it('loads a discovered claude provider', () => {
    const r = new ProviderRegistry();
    const result = r.load([{
      id: 'claude', path: '/usr/local/bin/claude', version: '2.1.202', protocol: 'stream-json',
    }]);
    expect(result.added).toContain('claude');
    const e = r.get('claude');
    expect(e?.provider.id).toBe('claude');
    // status is `available` when credentials exist; `degraded` when not — both valid.
    expect(['available', 'degraded']).toContain(e?.status);
    expect(e?.binaryPath).toBe('/usr/local/bin/claude');
  });

  it('skips unknown discovered binaries', () => {
    const r = new ProviderRegistry();
    const result = r.load([
      { id: 'mystery-cli', path: '/x', version: '1', protocol: 'unknown' },
    ]);
    expect(result.skipped).toContain('mystery-cli');
    expect(r.list()).toHaveLength(0);
  });

  it('registers a custom provider via config and overrides its id', () => {
    const r = new ProviderRegistry();
    r.registerCustom({
      id: 'my-acp', label: 'My ACP',
      command: '/usr/local/bin/my-cli', args: [], protocol: 'acp',
    });
    const result = r.load([]);
    expect(result.added).toContain('my-acp');
    expect(r.get('my-acp')?.provider.id).toBe('my-acp');
    expect(r.get('my-acp')?.source).toBe('config');
  });

  it('resolveByModel strips the agentd/ prefix', () => {
    const r = new ProviderRegistry();
    r.load([{ id: 'claude', path: '/usr/local/bin/claude', version: '1', protocol: 'stream-json' }]);
    expect(r.resolveByModel('agentd/claude')?.provider.id).toBe('claude');
    expect(r.resolveByModel('claude')?.provider.id).toBe('claude');
    expect(r.resolveByModel('agentd/missing')).toBeUndefined();
  });

  it('lists all loaded entries', () => {
    const r = new ProviderRegistry();
    r.load([{ id: 'claude', path: '/usr/local/bin/claude', version: '1', protocol: 'stream-json' }]);
    r.registerCustom({ id: 'my-acp', label: 'x', command: 'x', args: [], protocol: 'acp' });
    r.load([]);
    expect(r.list().length).toBeGreaterThanOrEqual(2);
  });
});