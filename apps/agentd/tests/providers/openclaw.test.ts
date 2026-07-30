import { describe, expect, it } from 'vitest';
import { OpenClawProvider, buildOpenClawProvider } from '../../src/providers/openclaw.js';

describe('providers/openclaw', () => {
  it('buildOpenClawProvider returns provider with configured baseUrl', () => {
    const p = buildOpenClawProvider(null, {
      id: 'openclaw', label: 'OpenClaw', command: 'openclaw',
      protocol: 'openai-http', baseUrl: 'http://localhost:9000',
    });
    expect(p.id).toBe('openclaw');
    expect(p.protocol).toBe('openai-http');
    expect((p as OpenClawProvider).configuredBaseUrl).toBe('http://localhost:9000');
  });

  it('default baseUrl is null when not provided', () => {
    const p = new OpenClawProvider(null);
    expect(p.configuredBaseUrl).toBeNull();
  });

  it('streams configuration hint when baseUrl is missing', async () => {
    const p = new OpenClawProvider(null);
    const r = await p.send({ messages: [{ role: 'user', content: 'hi' }] });
    const out = [];
    for await (const e of r.events) out.push(e);
    const textEvts = out.filter((e) => e.type === 'text') as Array<{ type: 'text'; delta: string }>;
    expect(textEvts[0]?.delta).toContain('no baseUrl configured');
  });

  it('streams best-effort message when baseUrl is configured', async () => {
    const p = new OpenClawProvider('http://localhost:9000');
    const r = await p.send({ messages: [{ role: 'user', content: 'hi' }] });
    const out = [];
    for await (const e of r.events) out.push(e);
    const textEvts = out.filter((e) => e.type === 'text') as Array<{ type: 'text'; delta: string }>;
    expect(textEvts[0]?.delta).toContain('best-effort');
  });
});