// Quick diagnostic: spawn fixture within vitest and read stdout directly
// (no provider, no parseNdjson — just to isolate whether `for await` works
// inside vitest's forked runner).
import { describe, it } from 'vitest';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FIXTURE = path.join(__dirname, 'fixtures', 'mock-claude-cli.mjs');

describe('vitest stream sanity', () => {
  it('A: child.stdout as raw iterator', async () => {
    const child = spawn(process.execPath, [FIXTURE, '--session-id', 'A'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, MOCK_MODE: 'text-only' },
    });
    child.stderr?.on('data', (b) => process.stderr.write(`[A stderr] ${b}`));
    await new Promise((r) => setTimeout(r, 100));
    child.stdin.write('{"type":"user","message":{"role":"user","content":"hi"}}\n');
    const start = Date.now();
    for await (const chunk of child.stdout) {
      process.stderr.write(`[A stdout chunk ${Date.now() - start}ms] ${chunk.toString()}\n`);
    }
    process.stderr.write(`[A end ${Date.now() - start}ms]\n`);
  }, 10000);

  it('B: parseNdjson on child.stdout', async () => {
    const { parseNdjson } = await import('../../src/stream/ndjson.js');
    const child = spawn(process.execPath, [FIXTURE, '--session-id', 'B'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, MOCK_MODE: 'text-only' },
    });
    child.stderr?.on('data', (b) => process.stderr.write(`[B stderr] ${b}`));
    await new Promise((r) => setTimeout(r, 100));
    child.stdin.write('{"type":"user","message":{"role":"user","content":"hi"}}\n');
    const start = Date.now();
    for await (const obj of parseNdjson(child.stdout)) {
      process.stderr.write(`[B parsed at ${Date.now() - start}ms] ${obj.type}\n`);
    }
    process.stderr.write(`[B end ${Date.now() - start}ms]\n`);
  }, 10000);

  it('C: ClaudeProvider inside vitest, with delay before consume', async () => {
    const { ClaudeProvider } = await import('../../src/providers/claude.js');
    const provider = new ClaudeProvider(process.execPath, 'mock', [FIXTURE]);
    await provider.init({ maxPoolSize: 4, idleTtlMs: 60000 });
    const result = await provider.send({
      messages: [{ role: 'user', content: 'hi' }],
      sessionId: 'session-C',
      model: 'agentd/claude',
    });
    await new Promise((r) => setTimeout(r, 200));
    const start = Date.now();
    for await (const e of result.events) {
      process.stderr.write(`[C event at ${Date.now() - start}ms] ${e.type}\n`);
      if (e.type === 'done') break;
    }
    process.stderr.write(`[C end ${Date.now() - start}ms]\n`);
    await provider.shutdown();
  }, 10000);
});
