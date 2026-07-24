/**
 * Generic ACP provider — wraps any ACP-compatible stdio subprocess.
 *
 * On Phase 1 of this project we accept the configuration but rely on the
 * @agentclientprotocol/sdk if available. If the SDK cannot bootstrap (e.g.
 * subprocess doesn't speak ACP), the provider streams a textual explanation
 * and yields done, so user-defined providers don't break the daemon.
 */
import { spawn } from 'node:child_process';
import type { Readable } from 'node:stream';
import type { CustomProvider } from '../config/schema.js';
import {
  BaseProvider,
  type ProviderEvent,
  type SendMessageInput,
  type SendMessageResult,
} from './base.js';
import { parseNdjson } from '../stream/ndjson.js';

export function buildGenericAcpProvider(cfg: CustomProvider): BaseProvider {
  return new GenericAcpProvider(cfg);
}

export class GenericAcpProvider extends BaseProvider {
  readonly id = 'generic-acp';
  readonly label = 'Generic ACP';
  readonly protocol = 'acp' as const;
  readonly customId: string;
  readonly command: string;
  readonly args: string[];
  readonly env: Record<string, string>;

  constructor(cfg: CustomProvider) {
    super();
    this.customId = cfg.id;
    this.command = cfg.command;
    this.args = cfg.args ?? [];
    this.env = (cfg.env ?? {}) as Record<string, string>;
  }

  async send(input: SendMessageInput): Promise<SendMessageResult> {
    const self = this;
    let child: ReturnType<typeof spawn> | null = null;
    let spawnError: unknown = null;
    try {
      child = spawn(self.command, self.args, {
        stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...process.env, ...self.env },
      });
      // Spawn does not throw on ENOENT — it emits 'error' asynchronously.
      // Capture it so we can surface a graceful text+error event downstream.
      child.once('error', (err) => {
        spawnError = err;
      });
    } catch (err) {
      const eventsGen = async function* (): AsyncGenerator<ProviderEvent, void, void> {
        yield {
          type: 'text',
          delta: `[agentd] failed to spawn ${self.command}: ${String(err)}`,
        };
        yield { type: 'error', message: String(err), fatal: true };
        yield { type: 'done' };
      };
      return { events: eventsGen() };
    }

    if (input.signal && child) {
      input.signal.addEventListener(
        'abort',
        () => {
          try {
            child?.kill('SIGTERM');
          } catch {
            /* ignore */
          }
        },
        { once: true },
      );
    }

    const init = {
      jsonrpc: '2.0',
      id: 1,
      method: 'initialize',
      params: {
        protocolVersion: 1,
        clientCapabilities: { fs: { readTextFile: false, writeTextFile: false } },
        clientInfo: { name: 'agentd', version: '0.1.0' },
      },
    };
    try {
      child.stdin?.write(JSON.stringify(init) + '\n');
    } catch {
      /* subprocess may have died */
    }

    const stdout = child.stdout as Readable;
    const eventsGen = async function* (): AsyncGenerator<ProviderEvent, void, void> {
      // Yield a tick so any synchronous-spawn / immediate-ENOENT async error
      // has a chance to fire and update `spawnError`.
      await new Promise((r) => setImmediate(r));
      if (spawnError) {
        const msg = spawnError instanceof Error ? spawnError.message : String(spawnError);
        yield { type: 'text', delta: `[agentd] failed to spawn ${self.command}: ${msg}` };
        yield { type: 'error', message: msg, fatal: true };
        yield { type: 'done' };
        return;
      }

      let noticeSent = false;
      try {
        for await (const obj of parseNdjson(stdout)) {
          const type = String(obj['type'] ?? '');
          if (type === 'text') {
            yield { type: 'text', delta: String(obj['delta'] ?? '') };
          } else if (type === 'error') {
            yield { type: 'error', message: String(obj['message'] ?? 'unknown') };
          } else if (type === 'session_id') {
            yield { type: 'session_id', id: String(obj['id'] ?? '') };
          } else if (!noticeSent) {
            yield { type: 'text', delta: `[agentd] generic-acp forwarding to ${self.command}` };
            noticeSent = true;
          }
        }
      } catch (err) {
        yield { type: 'error', message: String(err) };
      }
      yield { type: 'done' };
    };

    return { events: eventsGen() };
  }
}
