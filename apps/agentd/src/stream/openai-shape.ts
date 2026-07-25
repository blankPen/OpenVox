/**
 * Convert internal ProviderEvent stream into OpenAI ChatCompletionChunk objects.
 *
 * Each `text` event becomes a delta `{content: "..."}` chunk.
 * `tool_call` / `tool_result` / `usage` are passed through (OpenAI tolerates
 * the same shape but does not require them).
 * `done` emits a final chunk with `finish_reason: "stop"`.
 */

import type { ProviderEvent } from '../providers/base.js';

export interface ChatCompletionChunk {
  id: string;
  object: 'chat.completion.chunk';
  created: number;
  model: string;
  choices: Array<{
    index: number;
    delta: Record<string, unknown>;
    finish_reason: string | null;
  }>;
}

let counter = 0;
const idSeed = Date.now().toString(36);

export function newChunkId(): string {
  counter += 1;
  return `chatcmpl-${idSeed}-${counter.toString(36)}`;
}

export function mapEventToChunk(
  evt: ProviderEvent,
  modelId: string,
): ChatCompletionChunk | null {
  const id = newChunkId();
  const created = Math.floor(Date.now() / 1000);
  switch (evt.type) {
    case 'text':
      return {
        id,
        object: 'chat.completion.chunk',
        created,
        model: modelId,
        choices: [
          { index: 0, delta: { content: evt.delta, role: 'assistant' }, finish_reason: null },
        ],
      };
    case 'tool_call':
      return {
        id,
        object: 'chat.completion.chunk',
        created,
        model: modelId,
        choices: [
          {
            index: 0,
            delta: {
              tool_calls: [
                {
                  index: 0,
                  id: evt.id,
                  type: 'function',
                  function: { name: evt.name, arguments: JSON.stringify(evt.args ?? {}) },
                },
              ],
            },
            finish_reason: null,
          },
        ],
      };
    case 'tool_result':
      // Surface as a system-style delta; clients that don't expect it will ignore.
      return null;
    case 'usage':
      return {
        id,
        object: 'chat.completion.chunk',
        created,
        model: modelId,
        choices: [{ index: 0, delta: {}, finish_reason: null }],
      };
    case 'session_id':
      return null; // internal, not exposed
    case 'error':
      return {
        id,
        object: 'chat.completion.chunk',
        created,
        model: modelId,
        choices: [
          {
            index: 0,
            delta: {},
            finish_reason: 'stop',
          },
        ],
      };
    case 'done':
      return {
        id,
        object: 'chat.completion.chunk',
        created,
        model: modelId,
        choices: [
          {
            index: 0,
            delta: {},
            finish_reason: evt.stopReason === 'tool_use' ? 'tool_calls' : 'stop',
          },
        ],
      };
    default:
      return null;
  }
}

/** Final ChatCompletion envelope for non-streaming responses. */
export interface ChatCompletion {
  id: string;
  object: 'chat.completion';
  created: number;
  model: string;
  choices: Array<{
    index: number;
    message: { role: 'assistant'; content: string };
    finish_reason: string;
  }>;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
}

export function buildFinalCompletion(
  modelId: string,
  content: string,
  inputTokens = 0,
  outputTokens = 0,
): ChatCompletion {
  return {
    id: newChunkId(),
    object: 'chat.completion',
    created: Math.floor(Date.now() / 1000),
    model: modelId,
    choices: [
      {
        index: 0,
        message: { role: 'assistant', content },
        finish_reason: 'stop',
      },
    ],
    usage: {
      prompt_tokens: inputTokens,
      completion_tokens: outputTokens,
      total_tokens: inputTokens + outputTokens,
    },
  };
}
