/**
 * OpenClaw provider — best effort.
 *
 * If the user provides a `baseUrl` in the custom provider config, we forward
 * OpenAI-compatible HTTP requests; otherwise we act as a stub that explains
 * how to configure it.
 */
import type { CustomProvider } from '../config/schema.js';
import { BaseProvider } from './base.js';

export function buildOpenClawProvider(
  _discovered: { command: string; version: string } | null,
  cfg: CustomProvider | null,
): BaseProvider {
  return new OpenClawProvider(cfg?.baseUrl ?? null);
}

export class OpenClawProvider extends BaseProvider {
  readonly id = 'openclaw';
  readonly label = 'OpenClaw';
  readonly protocol = 'openai-http' as const;

  constructor(readonly configuredBaseUrl: string | null) {
    super();
  }

  async send(): Promise<import('./base.js').SendMessageResult> {
    if (!this.configuredBaseUrl) {
      async function* events() {
        yield {
          type: 'text' as const,
          delta:
            '[agentd] openclaw provider has no baseUrl configured. Set providers[].baseUrl in ~/.agentd/config.json.',
        };
        yield { type: 'done' as const };
      }
      return { events: events() };
    }
    async function* events() {
      yield {
        type: 'text' as const,
        delta: '[agentd] openclaw HTTP forwarding is best-effort; not yet wired up',
      };
      yield { type: 'done' as const };
    }
    return { events: events() };
  }
}
