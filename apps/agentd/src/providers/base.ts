import type { CustomProvider } from '../config/schema.js';

/**
 * Abstract base for every provider implementation.
 *
 * A provider brokers a chat stream between agentd and one external CLI binary.
 * Implementations are responsible for:
 *  - spawning the subprocess,
 *  - feeding messages in (one per session),
 *  - reading NDJSON / ACP / HTTP events out,
 *  - mapping internal events to OpenAI ChatCompletionChunk shape.
 *
 * The session layer (`src/sessions/manager.ts`) owns lifecycle
 * (idle TTL, restart policy) and is unaware of protocols.
 */
export interface SendMessageInput {
  /** OpenAI-style messages. Last user message is the prompt; prior is context. */
  messages: Array<{ role: 'system' | 'user' | 'assistant'; content: string }>;
  /** CLI session id from a prior call — set means "resume". */
  resumeCliSessionId?: string;
  /** model id like `agentd/claude` — purely informational, unused by stream-json. */
  model?: string;
  /** Abort signal — provider should kill subprocess on abort. */
  signal?: AbortSignal;
}

export type ProviderEvent =
  | { type: 'text'; delta: string }
  | { type: 'tool_call'; name: string; args: unknown; id: string }
  | { type: 'tool_result'; id: string; result: unknown }
  | { type: 'session_id'; id: string }
  | { type: 'usage'; inputTokens: number; outputTokens: number }
  | { type: 'error'; message: string; restart?: boolean; fatal?: boolean }
  | { type: 'done'; stopReason?: string };

export interface SendMessageResult {
  events: AsyncIterable<ProviderEvent>;
  /** The CLI session id the caller should persist for resume. */
  cliSessionId?: string;
}

export interface ProviderCapabilities {
  supportsResume: boolean;
  supportsTools: boolean;
  supportsStreaming: boolean;
}

export abstract class BaseProvider {
  abstract readonly id: string;
  abstract readonly label: string;
  abstract readonly protocol: 'stream-json' | 'openai-http' | 'acp' | 'jsonrpc';

  /** Optional per-provider init (open file handles, auth probe). */
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  async init(_cfg: unknown): Promise<void> {
    /* default no-op */
  }

  /**
   * Spawn or reuse a subprocess and stream back events.
   * Implementations MUST honour `signal` (kill child on abort).
   */
  abstract send(input: SendMessageInput): Promise<SendMessageResult>;

  /** Default capabilities used by the API layer for content negotiation. */
  capabilities(): ProviderCapabilities {
    return {
      supportsResume: this.protocol === 'stream-json' || this.protocol === 'acp',
      supportsTools: this.protocol === 'openai-http' || this.protocol === 'acp',
      supportsStreaming: true,
    };
  }

  /** Default-factory hook so a config-file provider can be turned into an instance. */
  static fromConfig(_p: CustomProvider): BaseProvider {
    throw new Error('fromConfig must be implemented by subclass');
  }
}
