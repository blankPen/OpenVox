import { describe, expect, it, beforeEach } from 'vitest';
import Fastify, { type FastifyInstance } from 'fastify';
import type { AgentdConfig } from '../../../src/config/schema.js';
import { ProviderRegistry, type ProviderEntry } from '../../../src/providers/registry.js';
import { BaseProvider, type ProviderEvent, type SendMessageResult } from '../../../src/providers/base.js';
import { SessionManager } from '../../../src/sessions/manager.js';
import { chatRoute } from '../../../src/api/routes/chat.js';

const cfg: AgentdConfig = {
  port: 8787, host: '127.0.0.1', logLevel: 'silent',
  sessionTtlSeconds: 1800, maxConcurrentPerProvider: 4,
  rateLimit: { max: 60, windowMs: 60_000 },
  auth: { tokens: [] },
  providers: [], cliOAuth: { probeClaudeCredentials: true },
  acp: { serverSocket: null },
};

class FakeProvider extends BaseProvider {
  readonly id = 'fake';
  readonly label = 'Fake';
  readonly protocol = 'stream-json' as const;
  sentInputs: Array<{ messages: Array<{ role: string; content: string }>; resumeCliSessionId?: string }> = [];
  eventsToEmit: ProviderEvent[] = [];
  async send(input: Parameters<BaseProvider['send']>[0]): Promise<SendMessageResult> {
    this.sentInputs.push({
      messages: input.messages.map((m) => ({ role: m.role, content: m.content })),
      resumeCliSessionId: input.resumeCliSessionId,
    });
    const events = this.eventsToEmit;
    async function* gen() {
      for (const e of events) yield e;
    }
    return { events: gen() };
  }
}

function makeRegistry(provider: BaseProvider): ProviderRegistry {
  const r = new ProviderRegistry();
  const entry: ProviderEntry = { provider, status: 'available', source: 'config' };
  // Inject our controllable provider by reaching into the registry's
  // internal map. We avoid the public load() because it would call into
  // factory.build() with a real CLI binary.
  (r as unknown as { entries: Map<string, ProviderEntry> }).entries =
    new Map([['fake', entry]]);
  return r;
}

let app: FastifyInstance;
let sessions: SessionManager;
let fake: FakeProvider;

beforeEach(async () => {
  sessions = new SessionManager();
  fake = new FakeProvider();
  fake.eventsToEmit = [
    { type: 'session_id', id: 'cli-xyz' },
    { type: 'text', delta: 'PONG' },
    { type: 'done', stopReason: 'end_turn' },
  ];
  const registry = makeRegistry(fake);
  app = Fastify({ logger: false });
  await chatRoute(app, { cfg, registry, sessions });
  await app.ready();
});

describe('api/routes/chat', () => {
  it('returns SSE chunks for a streaming chat request', async () => {
    const r = await app.inject({
      method: 'POST', url: '/v1/chat/completions',
      payload: {
        model: 'agentd/fake',
        messages: [{ role: 'user', content: 'Reply with the single word: PONG' }],
        stream: true,
      },
    });
    expect(r.statusCode).toBe(200);
    expect(r.headers['content-type']).toContain('text/event-stream');
    expect(r.body).toContain('"content":"PONG"');
    expect(r.body).toContain('data: [DONE]');
    expect(fake.sentInputs[0]?.messages[0]?.content).toBe('Reply with the single word: PONG');
  });

  it('anchors cli_session_id onto the agentd session', async () => {
    const r = await app.inject({
      method: 'POST', url: '/v1/chat/completions',
      payload: {
        model: 'agentd/fake',
        messages: [{ role: 'user', content: 'hi' }],
        stream: true,
      },
    });
    expect(r.statusCode).toBe(200);
    expect(sessions.list()[0]?.cliSessionId).toBe('cli-xyz');
  });

  it('reuses the same agentd session when room_id matches', async () => {
    const r1 = await app.inject({
      method: 'POST', url: '/v1/chat/completions',
      payload: {
        model: 'agentd/fake',
        messages: [{ role: 'user', content: 'first' }],
        stream: true, room_id: 'voice-room-42',
      },
    });
    expect(r1.statusCode).toBe(200);
    const r2 = await app.inject({
      method: 'POST', url: '/v1/chat/completions',
      payload: {
        model: 'agentd/fake',
        messages: [{ role: 'user', content: 'second' }],
        stream: true, room_id: 'voice-room-42',
      },
    });
    expect(r2.statusCode).toBe(200);
    expect(sessions.list()).toHaveLength(1);
    expect(fake.sentInputs[1]?.resumeCliSessionId).toBe('cli-xyz');
  });

  it('returns 404 for unknown model', async () => {
    const r = await app.inject({
      method: 'POST', url: '/v1/chat/completions',
      payload: {
        model: 'agentd/nonexistent',
        messages: [{ role: 'user', content: 'x' }],
        stream: false,
      },
    });
    expect(r.statusCode).toBe(404);
  });

  it('returns 400 for invalid body', async () => {
    const r = await app.inject({
      method: 'POST', url: '/v1/chat/completions',
      payload: { model: 'agentd/fake', messages: [] },
    });
    expect(r.statusCode).toBe(400);
  });

  it('returns a non-stream ChatCompletion when stream=false', async () => {
    fake.eventsToEmit = [
      { type: 'session_id', id: 'cli-1' },
      { type: 'text', delta: 'HELLO' },
      { type: 'text', delta: ' WORLD' },
      { type: 'usage', inputTokens: 7, outputTokens: 11 },
      { type: 'done', stopReason: 'end_turn' },
    ];
    const r = await app.inject({
      method: 'POST', url: '/v1/chat/completions',
      payload: {
        model: 'agentd/fake',
        messages: [{ role: 'user', content: 'hi' }],
        stream: false,
      },
    });
    expect(r.statusCode).toBe(200);
    const body = r.json();
    expect(body.object).toBe('chat.completion');
    expect(body.choices[0]?.message.content).toBe('HELLO WORLD');
    expect(body.usage).toEqual({ prompt_tokens: 7, completion_tokens: 11, total_tokens: 18 });
  });
});