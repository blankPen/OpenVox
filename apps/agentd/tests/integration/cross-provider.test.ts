/**
 * Cross-provider integration test.
 *
 * Spins up an in-memory Fastify daemon (`buildServer` semantics but without
 * @fastify/rate-limit / pino overhead), wires up a `ProviderRegistry` whose
 * entries are controllable fakes — one per protocol kind
 * (claude / codex / openclaw / generic-acp) — and exercises:
 *
 *   1. /v1/models — model id format is `agentd/<provider>`, one per registry entry.
 *   2. /v1/chat/completions streaming — every registered provider routes
 *      correctly, the SSE framing is `data: {...}\n\n` + terminating
 *      `data: [DONE]\n\n`, and `tool_call` events are serialised into the
 *      OpenAI `tool_calls` delta shape.
 *   3. /v1/chat/completions non-streaming — provider counts, session_id
 *      anchored onto the agentd session record.
 *
 * Style mirrors tests/api/routes/chat.test.ts (Fastify inject + fake
 * BaseProvider subclasses that record what they receive).
 */
import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import Fastify, { type FastifyInstance } from 'fastify';
import type { AgentdConfig } from '../../src/config/schema.js';
import {
  ProviderRegistry,
  type ProviderEntry,
} from '../../src/providers/registry.js';
import {
  BaseProvider,
  type ProviderEvent,
  type SendMessageResult,
} from '../../src/providers/base.js';
import { SessionManager } from '../../src/sessions/manager.js';
import { chatRoute } from '../../src/api/routes/chat.js';
import { modelsRoute } from '../../src/api/routes/models.js';

const cfg: AgentdConfig = {
  port: 8787,
  host: '127.0.0.1',
  logLevel: 'silent',
  sessionTtlSeconds: 1800,
  maxConcurrentPerProvider: 4,
  rateLimit: { max: 60, windowMs: 60_000 },
  auth: { tokens: [] },
  providers: [],
  cliOAuth: { probeClaudeCredentials: false },
  acp: { serverSocket: null },
};

/**
 * FakeProvider — records every send() call and yields a configured list of
 * ProviderEvents. One instance per protocol kind lets the test assert that
 * the right provider saw the right request.
 */
class FakeProvider extends BaseProvider {
  readonly protocol;
  readonly sentInputs: Array<{
    messages: Array<{ role: string; content: string }>;
    resumeCliSessionId?: string;
    model?: string;
  }> = [];
  private events: ProviderEvent[];

  constructor(
    readonly id: string,
    readonly label: string,
    protocol: 'stream-json' | 'openai-http' | 'acp' | 'jsonrpc',
    events: ProviderEvent[] = [],
  ) {
    super();
    this.protocol = protocol;
    this.events = events;
  }

  setEvents(events: ProviderEvent[]): void {
    this.events = events;
  }

  async send(input: Parameters<BaseProvider['send']>[0]): Promise<SendMessageResult> {
    this.sentInputs.push({
      messages: input.messages.map((m) => ({ role: m.role, content: m.content })),
      resumeCliSessionId: input.resumeCliSessionId,
      model: input.model,
    });
    const queue = this.events;
    async function* gen() {
      for (const e of queue) yield e;
    }
    return { events: gen() };
  }
}

interface Harness {
  app: FastifyInstance;
  sessions: SessionManager;
  providers: Record<string, FakeProvider>;
}

function buildHarness(): Harness {
  const sessions = new SessionManager();
  const claude = new FakeProvider('claude', 'Claude Code', 'stream-json');
  const codex = new FakeProvider('codex', 'Codex', 'jsonrpc');
  const openclaw = new FakeProvider('openclaw', 'OpenClaw', 'openai-http');
  const genericAcp = new FakeProvider('generic-acp', 'Generic ACP', 'acp');

  const registry = new ProviderRegistry();
  const entries = new Map<string, ProviderEntry>();
  for (const p of [claude, codex, openclaw, genericAcp]) {
    entries.set(p.id, { provider: p, status: 'available', source: 'config' });
  }
  (registry as unknown as { entries: Map<string, ProviderEntry> }).entries = entries;

  const app = Fastify({ logger: false });
  return {
    app,
    sessions,
    providers: {
      claude: claude,
      codex: codex,
      openclaw: openclaw,
      'generic-acp': genericAcp,
    },
  };
}

let h: Harness;

beforeEach(async () => {
  h = buildHarness();
  await chatRoute(h.app, { cfg, registry: hProvidersRegistry(h), sessions: h.sessions });
  await modelsRoute(h.app, hProvidersRegistry(h));
  await h.app.ready();
});

afterEach(async () => {
  await h.app.close();
});

/**
 * Helper — re-pulls the registry out of the harness by reading the inner
 * entries map. Both chatRoute and modelsRoute want the same registry object.
 */
function hProvidersRegistry(h: Harness): ProviderRegistry {
  // We only have one ProviderRegistry instance per harness; rebuild it from
  // the providers so chatRoute + modelsRoute see consistent state.
  const r = new ProviderRegistry();
  const entries = new Map<string, ProviderEntry>();
  for (const id of Object.keys(h.providers)) {
    const p = h.providers[id]!;
    entries.set(p.id, { provider: p, status: 'available', source: 'config' });
  }
  (r as unknown as { entries: Map<string, ProviderEntry> }).entries = entries;
  return r;
}

describe('integration: cross-provider', () => {
  it('GET /v1/models lists every registered provider as agentd/<id>', async () => {
    const r = await h.app.inject({ method: 'GET', url: '/v1/models' });
    expect(r.statusCode).toBe(200);
    const body = r.json() as {
      object: string;
      data: Array<{
        id: string;
        agentd: { protocol: string; status: string; label: string };
      }>;
    };
    expect(body.object).toBe('list');
    const ids = body.data.map((m) => m.id).sort();
    expect(ids).toEqual([
      'agentd/claude',
      'agentd/codex',
      'agentd/generic-acp',
      'agentd/openclaw',
    ]);

    // Each entry should surface protocol + status + label in the agentd key.
    for (const entry of body.data) {
      expect(entry.id.startsWith('agentd/')).toBe(true);
      expect(entry.agentd.status).toBe('available');
      expect(['stream-json', 'openai-http', 'acp', 'jsonrpc']).toContain(
        entry.agentd.protocol,
      );
    }
  });

  it('routes /v1/chat/completions to the correct provider per model id', async () => {
    // Configure each fake to emit a text delta that names itself so we can
    // assert which provider's events made it to the SSE stream.
    for (const id of Object.keys(h.providers)) {
      h.providers[id]!.setEvents([
        { type: 'session_id', id: `cli-${id}-1` },
        { type: 'text', delta: `from-${id}` },
        { type: 'done', stopReason: 'end_turn' },
      ]);
    }

    const targets = ['claude', 'codex', 'openclaw', 'generic-acp'] as const;
    for (const id of targets) {
      // Reset sentInputs before each iteration so we can assert per-call routing.
      for (const k of Object.keys(h.providers)) {
        (h.providers[k] as unknown as { sentInputs: unknown[] }).sentInputs = [];
      }

      const r = await h.app.inject({
        method: 'POST',
        url: '/v1/chat/completions',
        payload: {
          model: `agentd/${id}`,
          messages: [{ role: 'user', content: `ping-${id}` }],
          stream: true,
        },
      });
      expect(r.statusCode, `provider ${id}`).toBe(200);
      expect(r.headers['content-type'], `provider ${id}`).toContain('text/event-stream');
      expect(r.body, `provider ${id}`).toContain(`"content":"from-${id}"`);
      expect(r.body, `provider ${id}`).toContain('data: [DONE]');

      // And the right provider actually saw the message — the others did not.
      expect(h.providers[id]!.sentInputs, `${id} should see 1 request`).toHaveLength(1);
      expect(h.providers[id]!.sentInputs[0]?.messages[0]?.content).toBe(`ping-${id}`);
      for (const other of targets) {
        if (other === id) continue;
        expect(h.providers[other]!.sentInputs, `${other} should not see ${id} request`).toHaveLength(0);
      }
    }
  });

  it('emits clean SSE framing: data: <json>\\n\\n lines plus terminating [DONE]', async () => {
    h.providers['claude']!.setEvents([
      { type: 'session_id', id: 'cli-claude-2' },
      { type: 'text', delta: 'one ' },
      { type: 'text', delta: 'two ' },
      { type: 'text', delta: 'three' },
      { type: 'done', stopReason: 'end_turn' },
    ]);
    const r = await h.app.inject({
      method: 'POST',
      url: '/v1/chat/completions',
      payload: {
        model: 'agentd/claude',
        messages: [{ role: 'user', content: 'count' }],
        stream: true,
      },
    });
    expect(r.statusCode).toBe(200);

    // Split on the SSE record separator and verify each line is well-formed.
    const records = r.body.split('\n\n').filter((l) => l.length > 0);
    expect(records.length).toBeGreaterThan(0);
    for (const rec of records) {
      // [DONE] is the well-known terminator — skip JSON validation for it.
      if (rec === 'data: [DONE]') continue;
      expect(rec.startsWith('data: ')).toBe(true);
      const payload = rec.slice('data: '.length);
      // Every payload must parse as a ChatCompletionChunk-shaped object.
      expect(() => JSON.parse(payload) as unknown).not.toThrow();
      const parsed = JSON.parse(payload) as {
        object: string;
        choices: Array<{ delta: Record<string, unknown>; finish_reason: string | null }>;
      };
      expect(parsed.object).toBe('chat.completion.chunk');
      expect(Array.isArray(parsed.choices)).toBe(true);
    }
    // The final record must be [DONE].
    expect(r.body.trimEnd().endsWith('data: [DONE]')).toBe(true);
  });

  it('serialises tool_call events into the OpenAI tool_calls delta shape', async () => {
    h.providers['claude']!.setEvents([
      { type: 'session_id', id: 'cli-tool-1' },
      {
        type: 'tool_call',
        id: 'call_001',
        name: 'bash',
        args: { cmd: 'ls -la' },
      },
      { type: 'text', delta: 'done' },
      { type: 'done', stopReason: 'tool_use' },
    ]);
    const r = await h.app.inject({
      method: 'POST',
      url: '/v1/chat/completions',
      payload: {
        model: 'agentd/claude',
        messages: [{ role: 'user', content: 'list files' }],
        stream: true,
      },
    });
    expect(r.statusCode).toBe(200);

    // Find the SSE record carrying the tool_calls delta and parse it.
    // Skip the [DONE] terminator (it is not JSON).
    const records = r.body
      .split('\n\n')
      .filter((l) => l.startsWith('data: ') && l !== 'data: [DONE]');
    const toolRecord = records
      .map((rec) => JSON.parse(rec.slice('data: '.length)) as {
        choices: Array<{
          delta: { tool_calls?: Array<{ id: string; type: string; function: { name: string; arguments: string } }> };
        }>;
      })
      .find((p) => Array.isArray(p.choices[0]?.delta.tool_calls) && p.choices[0]!.delta.tool_calls!.length > 0);

    expect(toolRecord).toBeDefined();
    const tc = toolRecord!.choices[0]!.delta.tool_calls![0]!;
    expect(tc.id).toBe('call_001');
    expect(tc.type).toBe('function');
    expect(tc.function.name).toBe('bash');
    expect(JSON.parse(tc.function.arguments)).toEqual({ cmd: 'ls -la' });

    // The terminal chunk carries finish_reason: tool_calls for tool_use stops.
    const last = records[records.length - 1];
    const lastParsed = JSON.parse(last!.slice('data: '.length)) as {
      choices: Array<{ finish_reason: string | null }>;
    };
    expect(lastParsed.choices[0]?.finish_reason).toBe('tool_calls');
  });

  it('non-stream response aggregates text deltas into a single ChatCompletion', async () => {
    h.providers['openclaw']!.setEvents([
      { type: 'session_id', id: 'cli-oc-1' },
      { type: 'text', delta: 'OPEN' },
      { type: 'text', delta: 'CLAW' },
      { type: 'usage', inputTokens: 12, outputTokens: 34 },
      { type: 'done', stopReason: 'end_turn' },
    ]);
    const r = await h.app.inject({
      method: 'POST',
      url: '/v1/chat/completions',
      payload: {
        model: 'agentd/openclaw',
        messages: [{ role: 'user', content: 'identify' }],
        stream: false,
      },
    });
    expect(r.statusCode).toBe(200);
    expect(r.headers['content-type']).toContain('application/json');
    const body = r.json() as {
      object: string;
      choices: Array<{ message: { role: string; content: string }; finish_reason: string }>;
      usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
    };
    expect(body.object).toBe('chat.completion');
    expect(body.choices[0]?.message.role).toBe('assistant');
    expect(body.choices[0]?.message.content).toBe('OPENCLAW');
    expect(body.choices[0]?.finish_reason).toBe('stop');
    expect(body.usage).toEqual({
      prompt_tokens: 12,
      completion_tokens: 34,
      total_tokens: 46,
    });
  });

  it('anchors provider-reported cli_session_id onto the agentd session record', async () => {
    h.providers['generic-acp']!.setEvents([
      { type: 'session_id', id: 'cli-acp-77' },
      { type: 'text', delta: 'ack' },
      { type: 'done', stopReason: 'end_turn' },
    ]);
    const r = await h.app.inject({
      method: 'POST',
      url: '/v1/chat/completions',
      payload: {
        model: 'agentd/generic-acp',
        messages: [{ role: 'user', content: 'go' }],
        stream: true,
        room_id: 'voice-room-x',
      },
    });
    expect(r.statusCode).toBe(200);
    const list = h.sessions.list();
    expect(list).toHaveLength(1);
    expect(list[0]?.provider).toBe('generic-acp');
    expect(list[0]?.roomId).toBe('voice-room-x');
    expect(list[0]?.cliSessionId).toBe('cli-acp-77');
  });

  it('returns 404 for an unknown model id', async () => {
    const r = await h.app.inject({
      method: 'POST',
      url: '/v1/chat/completions',
      payload: {
        model: 'agentd/does-not-exist',
        messages: [{ role: 'user', content: 'hi' }],
        stream: true,
      },
    });
    expect(r.statusCode).toBe(404);
  });
});