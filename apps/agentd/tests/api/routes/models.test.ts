import { describe, expect, it, beforeEach } from 'vitest';
import Fastify, { type FastifyInstance } from 'fastify';
import { ProviderRegistry, type ProviderEntry } from '../../../src/providers/registry.js';
import { BaseProvider } from '../../../src/providers/base.js';
import { modelsRoute } from '../../../src/api/routes/models.js';

class StubProvider extends BaseProvider {
  readonly id = 'stub';
  readonly label = 'Stub';
  readonly protocol = 'stream-json' as const;
}

class StubProvider2 extends BaseProvider {
  readonly id = 'stub2';
  readonly label = 'Stub2';
  readonly protocol = 'openai-http' as const;
}

let app: FastifyInstance;
let registry: ProviderRegistry;

beforeEach(async () => {
  registry = new ProviderRegistry();
  const entries = [
    { provider: new StubProvider(), status: 'available' as const, source: 'config' as const },
    { provider: new StubProvider2(), status: 'degraded' as const, source: 'config' as const },
  ];
  (registry as unknown as { entries: Map<string, ProviderEntry> }).entries =
    new Map(entries.map((e) => [e.provider.id, e]));
  app = Fastify({ logger: false });
  await modelsRoute(app, registry);
  await app.ready();
});

describe('api/routes/models', () => {
  it('returns the agentd/-prefixed model list', async () => {
    const r = await app.inject({ method: 'GET', url: '/v1/models' });
    expect(r.statusCode).toBe(200);
    const body = r.json();
    expect(body.object).toBe('list');
    expect(body.data).toHaveLength(2);
    expect(body.data[0]?.id).toBe('agentd/stub');
    expect(body.data[1]?.id).toBe('agentd/stub2');
    expect(body.data[0]?.agentd?.status).toBe('available');
    expect(body.data[1]?.agentd?.status).toBe('degraded');
    expect(body.data[0]?.agentd?.protocol).toBe('stream-json');
  });
});