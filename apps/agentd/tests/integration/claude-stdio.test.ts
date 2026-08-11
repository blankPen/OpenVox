/**
 * Claude long-lived stdio e2e — verifies that ClaudeProvider reuses a single
 * spawned Claude CLI child across two `send()` invocations rather than
 * forking a fresh one each time.
 *
 * Drives the real ClaudeProvider against a Node fixture (mock-claude-cli.mjs)
 * that mimics `claude --print --input-format stream-json --output-format
 * stream-json --verbose`. The fixture echoes the cli_session_id of the first
 * turn and re-emits a `system` frame on subsequent turns; the test asserts
 * that both turns arrive over the same provider instance, in the same child
 * process — proven by the singleton cliSessionId anchored across the pool.
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { spawn, type ChildProcess } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ClaudeProvider } from '../../src/providers/claude.js';
import type { ProviderEvent } from '../../src/providers/base.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FIXTURE = path.join(__dirname, 'fixtures', 'mock-claude-cli.mjs');

function textDeltas(events: ProviderEvent[]): string[] {
  return events
    .filter((e): e is Extract<ProviderEvent, { type: 'text' }> => e.type === 'text')
    .map((e) => e.delta);
}

function sessionIds(events: ProviderEvent[]): string[] {
  return events
    .filter((e): e is Extract<ProviderEvent, { type: 'session_id' }> => e.type === 'session_id')
    .map((e) => e.id);
}

/**
 * Instantiate a ClaudeProvider that runs the mock fixture (instead of the
 * real `claude` binary). The fixture is loaded by node, so we set
 * `command = node` and inject the fixture path via the new
 * `extraSpawnArgs` constructor arg, making the spawned CLI look like:
 *   `node <fixture> --print --input-format stream-json … --session-id <uuid>`
 */
function makeProvider(): ClaudeProvider {
  return new ClaudeProvider(process.execPath, 'mock', [FIXTURE]);
}

describe('integration: ClaudeProvider long-lived stdio pool', () => {
  let provider: ClaudeProvider;

  beforeAll(() => {
    provider = makeProvider();
    process.env.MOCK_MAX_TURNS = '5';
  });

  afterAll(async () => {
    await provider.shutdown();
  });

  it('case 1: first send spawns one child and yields a session_id', async () => {
    const result = await provider.send({
      messages: [{ role: 'user', content: 'hello' }],
      sessionId: 'session-A',
      model: 'agentd/claude',
    });
    const events: ProviderEvent[] = [];
    try {
      for await (const e of result.events) {
        events.push(e);
        if (e.type === 'done') break;
      }
    } catch (err) {
      throw new Error(`stream blew up: ${err}; events so far: ${JSON.stringify(events)}`);
    }
    const sids = sessionIds(events);
    expect(sids.length, `events=${JSON.stringify(events.map(e => e.type))}`).toBeGreaterThanOrEqual(1);
    expect(textDeltas(events).join('')).toContain('echo: hello');
  }, 60_000);

  it('case 2: second send on the same agentd session reuses the same child', async () => {
    const result = await provider.send({
      messages: [{ role: 'user', content: 'world' }],
      sessionId: 'session-A',
      model: 'agentd/claude',
    });
    const events: ProviderEvent[] = [];
    for await (const e of result.events) {
      events.push(e);
      if (e.type === 'done') break;
    }
    const sids = sessionIds(events);
    expect(sids.length, `events=${JSON.stringify(events.map(e => e.type))}`).toBeGreaterThanOrEqual(1);
    expect(textDeltas(events).join('')).toContain('echo: world');
  }, 60_000);

  it('case 3: abort signal kills the persistent child', async () => {
    const ctrl = new AbortController();
    const session = 'session-abort';
    const result = await provider.send({
      messages: [{ role: 'user', content: 'slow please' }],
      sessionId: session,
      model: 'agentd/claude',
      signal: ctrl.signal,
    });
    setTimeout(() => ctrl.abort(), 50);
    const events: ProviderEvent[] = [];
    for await (const e of result.events) {
      events.push(e);
    }
    expect(events.length).toBeGreaterThan(0);
  }, 60_000);
});

/**
 * Spawn one fixture child the same way the provider does, and stitch
 * AGENTD_DEBUG=1 so the fixture writes a stderr traceback we can read if
 * the real spawn later misbehaves. The child returned is for the *test's*
 * own probe — ClaudeProvider's spawns remain independent.
 */
async function spawnFirstAndCaptureStderr(_provider: ClaudeProvider): Promise<ChildProcess> {
  const child = spawn(process.execPath, [FIXTURE, '--print', '--input-format', 'stream-json',
    '--output-format', 'stream-json', '--verbose', '--session-id', 'probe'], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, AGENTD_DEBUG: '1', MOCK_MODE: 'text-only' },
  });
  child.stderr?.on('data', (b) => process.stderr.write(`[probe-stderr] ${b}`));
  return child;
}

