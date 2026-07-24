/**
 * Codex provider — best effort.
 *
 * If the `codex` binary is present, we spawn `codex app-server` (JSON-RPC over stdio).
 * If not, `availability` reports `unavailable` and the registry simply excludes it
 * from active routing — so absence of the binary is non-fatal.
 */
import type { CustomProvider } from '../config/schema.js';
import { BaseProvider } from './base.js';

export function buildCodexProvider(
  discovered: { command: string; version: string } | null,
  _cfg: CustomProvider | null,
): BaseProvider {
  const command = discovered?.command ?? _cfg?.command ?? 'codex';
  return new CodexProvider(command, discovered !== null);
}

export class CodexProvider extends BaseProvider {
  readonly id = 'codex';
  readonly label = 'Codex';
  readonly protocol = 'jsonrpc' as const;

  constructor(
    readonly command: string,
    readonly binaryAvailable: boolean,
  ) {
    super();
  }

  // Full implementation is intentionally a stub — agentd will surface
  // `binary_not_found` to callers and emit a stream with explanatory content.
  async send(): Promise<import('./base.js').SendMessageResult> {
    if (!this.binaryAvailable) {
      async function* events() {
        yield { type: 'text' as const, delta: '[agentd] codex binary not found in PATH' };
        yield { type: 'error' as const, message: 'codex unavailable', fatal: false };
        yield { type: 'done' as const };
      }
      return { events: events() };
    }
    async function* events() {
      yield { type: 'text' as const, delta: '[agentd] codex provider is best-effort; JSON-RPC not yet wired up' };
      yield { type: 'done' as const };
    }
    return { events: events() };
  }
}
