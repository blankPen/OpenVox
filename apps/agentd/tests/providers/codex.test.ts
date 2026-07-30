import { describe, expect, it } from 'vitest';
import { CodexProvider, buildCodexProvider } from '../../src/providers/codex.js';

describe('providers/codex', () => {
  it('buildCodexProvider returns a CodexProvider with default command', () => {
    const p = buildCodexProvider(null, null);
    expect(p.id).toBe('codex');
    expect(p.protocol).toBe('jsonrpc');
  });

  it('uses discovered command when available', () => {
    const p = buildCodexProvider(
      { command: '/usr/local/bin/codex', version: '1.2.3' }, null,
    );
    expect(p.command).toBe('/usr/local/bin/codex');
    expect(p.binaryAvailable).toBe(true);
  });

  it('flags binaryAvailable=false when not discovered', () => {
    const p = new CodexProvider('codex', false);
    expect(p.binaryAvailable).toBe(false);
  });

  it('streams a "binary not found" notice when unavailable', async () => {
    const p = new CodexProvider('codex', false);
    const r = await p.send({ messages: [{ role: 'user', content: 'hi' }] });
    const out = [];
    for await (const e of r.events) out.push(e);
    const textEvts = out.filter((e) => e.type === 'text') as Array<{ type: 'text'; delta: string }>;
    expect(textEvts.some((e) => e.delta.includes('binary not found'))).toBe(true);
    expect(out.at(-1)?.type).toBe('done');
  });
});