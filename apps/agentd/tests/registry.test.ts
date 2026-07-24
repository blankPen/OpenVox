/**
 * Tests the FACTORIES table and ProviderRegistry load() merging behaviour.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { FACTORIES, ProviderRegistry } from '../src/providers/registry.js';
import type { DiscoveredProvider } from '../src/providers/discovery.js';

describe('FACTORIES table', () => {
  it('exposes the four core factories', () => {
    expect(Object.keys(FACTORIES).sort()).toEqual(
      ['claude', 'codex', 'generic-acp', 'openclaw'].sort(),
    );
  });

  it('claude factory maps binary id `claude`', () => {
    expect(FACTORIES['claude']!.binaryIds).toContain('claude');
  });

  it('generic-acp factory has no binary ids (config-only)', () => {
    expect(FACTORIES['generic-acp']!.binaryIds).toEqual([]);
  });
});

describe('ProviderRegistry', () => {
  let reg: ProviderRegistry;
  beforeEach(() => {
    reg = new ProviderRegistry();
  });

  it('registers custom providers from config', () => {
    reg.registerCustom({
      id: 'my-agent',
      label: 'My Agent',
      command: 'echo',
      args: ['hello'],
      protocol: 'acp',
    });
    reg.load([]);
    const entry = reg.get('my-agent');
    expect(entry).toBeDefined();
    expect(entry?.source).toBe('config');
  });

  it('merges discovered binaries with custom providers, custom wins', () => {
    reg.registerCustom({
      id: 'claude',
      label: 'My Claude override',
      command: '/usr/local/bin/claude',
      args: [],
      protocol: 'stream-json',
    });
    const discovered: DiscoveredProvider[] = [
      {
        id: 'claude',
        path: '/Users/pz/.local/bin/claude',
        version: '2.1.202',
        protocol: 'stream-json',
      },
    ];
    reg.load(discovered);
    expect(reg.get('claude')?.source).toBe('config');
  });

  it('lists all loaded providers', () => {
    reg.load([
      { id: 'claude', path: '/x/claude', version: '1.0', protocol: 'stream-json' },
      { id: 'codex', path: '/x/codex', version: '1.0', protocol: 'jsonrpc' },
    ]);
    expect(reg.list().length).toBe(2);
  });

  it('resolves model id `agentd/claude` to entry', () => {
    reg.load([
      { id: 'claude', path: '/x/claude', version: '1.0', protocol: 'stream-json' },
    ]);
    const e = reg.resolveByModel('agentd/claude');
    expect(e).toBeDefined();
    expect(e?.provider.id).toBe('claude');
  });

  it('returns undefined for unknown model id', () => {
    reg.load([]);
    expect(reg.resolveByModel('agentd/nope')).toBeUndefined();
  });

  it('uses user-supplied id/label for custom providers', () => {
    reg.registerCustom({
      id: 'custom-echo',
      label: 'Custom Echo',
      command: '/bin/echo',
      args: ['hi'],
      protocol: 'acp',
    });
    reg.load([]);
    const e = reg.get('custom-echo');
    expect(e?.provider.id).toBe('custom-echo');
    expect(e?.provider.label).toBe('Custom Echo');
  });
});
