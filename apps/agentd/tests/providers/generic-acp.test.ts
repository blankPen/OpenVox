import { describe, expect, it } from 'vitest';
import { GenericAcpProvider, buildGenericAcpProvider } from '../../src/providers/generic-acp.js';

describe('providers/generic-acp', () => {
  it('buildGenericAcpProvider captures all config fields', () => {
    const p = buildGenericAcpProvider({
      id: 'my-acp', label: 'My ACP',
      command: '/usr/local/bin/my-cli', args: ['--serve'],
      protocol: 'acp', env: { FOO: 'bar' },
    });
    expect(p.id).toBe('generic-acp');
    expect((p as GenericAcpProvider).customId).toBe('my-acp');
    expect((p as GenericAcpProvider).command).toBe('/usr/local/bin/my-cli');
    expect((p as GenericAcpProvider).args).toEqual(['--serve']);
    expect((p as GenericAcpProvider).env).toEqual({ FOO: 'bar' });
  });

  it('streams a failure notice for a missing binary', async () => {
    const p = new GenericAcpProvider({
      id: 'missing', label: 'Missing',
      command: '/this/binary/does/not/exist/__definitely__',
      args: [], protocol: 'acp',
    });
    const r = await p.send({ messages: [{ role: 'user', content: 'hi' }] });
    const out: Array<{ type: string; message?: string; delta?: string }> = [];
    try {
      for await (const e of r.events) out.push(e);
    } catch {
      /* spawn may emit an async error; either way we expect either an error
         event or a "failed to spawn" text delta. */
    }
    const sawFailure =
      out.some((e) => e.type === 'error') ||
      out.some((e) => e.type === 'text' && (e.delta ?? '').includes('failed to spawn'));
    expect(sawFailure).toBe(true);
  });
});