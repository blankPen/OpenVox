import { spawn } from 'node:child_process';
import {
  BaseProvider,
  type ProviderEvent,
  type SendMessageInput,
  type SendMessageResult,
} from './base.js';
import type { CustomProvider } from '../config/schema.js';
import { parseNdjson } from '../stream/ndjson.js';

/**
 * Claude Code provider — uses `claude -p --output-format stream-json --verbose`.
 *
 * Spawns one subprocess per session; resumes by passing `--resume <cliSessionId>`.
 * NDJSON events on stdout are mapped to ProviderEvent and consumed by /v1/chat.
 */
export function buildClaudeProvider(
  discovered: { command: string; version: string } | null,
  cfg: CustomProvider | null,
): BaseProvider {
  const command = cfg?.command ?? discovered?.command ?? 'claude';
  const version = discovered?.version ?? 'unknown';
  return new ClaudeProvider(command, version);
}

export class ClaudeProvider extends BaseProvider {
  readonly id = 'claude';
  readonly label = 'Claude Code';
  readonly protocol = 'stream-json' as const;

  constructor(
    readonly command: string,
    readonly cliVersion: string,
  ) {
    super();
  }

  async send(input: SendMessageInput): Promise<SendMessageResult> {
    const args: string[] = ['-p', '--output-format', 'stream-json', '--verbose'];
    if (input.resumeCliSessionId) {
      args.push('--resume', input.resumeCliSessionId);
    } else {
      const seedSessionId = cryptoRandomUUID();
      args.push('--session-id', seedSessionId);
    }

    const lastUser = [...input.messages].reverse().find((m) => m.role === 'user');
    const prompt = lastUser?.content ?? '';
    args.push('--', prompt); // `--` avoids prompt being parsed as a flag.

    const child = spawn(this.command, args, {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env },
    });

    if (input.signal) {
      input.signal.addEventListener(
        'abort',
        () => {
          try {
            child.kill('SIGTERM');
          } catch {
            /* ignore */
          }
        },
        { once: true },
      );
    }

    // We return a single-pass async iterable that maps events in-line.
    // The chat route consumes this directly, so we don't need a queue.
    async function* events(): AsyncGenerator<ProviderEvent, void, void> {
      let stderrBuf = '';
      child.stderr?.on('data', (chunk: Buffer) => {
        stderrBuf += chunk.toString('utf8');
        if (stderrBuf.length > 4096) stderrBuf = stderrBuf.slice(-4096);
      });

      const stdout = child.stdout as unknown as import('node:stream').Readable;

      try {
        for await (const obj of parseNdjson(stdout)) {
          const evt = mapClaudeEvent(obj);
          if (evt) yield evt;
        }
      } catch (err) {
        yield { type: 'error', message: `claude stdout parse: ${String(err)}` };
      }

      const exitCode: number | null = await new Promise((res) => {
        child.once('exit', (code) => res(code));
      });
      if (exitCode !== 0 && exitCode !== null) {
        yield {
          type: 'error',
          message: `claude exited with code ${exitCode}: ${stderrBuf.slice(-512)}`,
          fatal: exitCode !== null,
        };
      }
      yield { type: 'done', stopReason: 'end_turn' };
    }

    return { events: events() };
  }
}

/** Map a Claude Code stream-json event to a ProviderEvent. */
function mapClaudeEvent(obj: Record<string, unknown>): ProviderEvent | null {
  const type = String(obj['type'] ?? '');
  if (type === 'system') {
    const sid = obj['session_id'];
    if (typeof sid === 'string') return { type: 'session_id', id: sid };
    return null;
  }
  if (type === 'assistant') {
    const msg = obj['message'] as
      | { content?: Array<{ type: string; text?: string }> }
      | undefined;
    const text = msg?.content?.find((c) => c.type === 'text')?.text;
    if (text) return { type: 'text', delta: text };
    return null;
  }
  if (type === 'result') {
    const usage = obj['usage'] as
      | { input_tokens?: number; output_tokens?: number }
      | undefined;
    if (usage) {
      return {
        type: 'usage',
        inputTokens: usage.input_tokens ?? 0,
        outputTokens: usage.output_tokens ?? 0,
      };
    }
    return { type: 'done', stopReason: 'end_turn' };
  }
  if (type === 'error') {
    return { type: 'error', message: String(obj['message'] ?? 'unknown error') };
  }
  return null;
}

function cryptoRandomUUID(): string {
  // RFC4122 v4 — Claude Code requires UUID-form session ids.
  return globalThis.crypto.randomUUID();
}
