import { describe, expect, it } from 'vitest';
import {
  buildFinalCompletion,
  mapEventToChunk,
  newChunkId,
} from '../../src/stream/openai-shape.js';

describe('stream/openai-shape', () => {
  it('maps text event to a content chunk', () => {
    const c = mapEventToChunk({ type: 'text', delta: 'hi' }, 'agentd/claude');
    expect(c).not.toBeNull();
    expect(c?.choices[0]?.delta).toMatchObject({ content: 'hi', role: 'assistant' });
    expect(c?.object).toBe('chat.completion.chunk');
    expect(c?.model).toBe('agentd/claude');
    expect(c?.choices[0]?.finish_reason).toBeNull();
  });

  it('maps done event to a finish_reason chunk', () => {
    const c = mapEventToChunk({ type: 'done', stopReason: 'end_turn' }, 'agentd/claude');
    expect(c).not.toBeNull();
    expect(c?.choices[0]?.finish_reason).toBe('stop');
    expect(c?.choices[0]?.delta).toEqual({});
  });

  it('maps done event with tool_use to tool_calls finish_reason', () => {
    const c = mapEventToChunk({ type: 'done', stopReason: 'tool_use' }, 'agentd/claude');
    expect(c?.choices[0]?.finish_reason).toBe('tool_calls');
  });

  it('maps tool_call event to a tool_calls chunk', () => {
    const c = mapEventToChunk(
      { type: 'tool_call', id: 'call_1', name: 'bash', args: { cmd: 'ls' } },
      'agentd/claude',
    );
    expect(c).not.toBeNull();
    expect(c?.choices[0]?.delta).toMatchObject({
      tool_calls: [
        { index: 0, id: 'call_1', type: 'function', function: { name: 'bash' } },
      ],
    });
  });

  it('returns null for internal events (session_id, tool_result)', () => {
    expect(mapEventToChunk({ type: 'session_id', id: 'x' }, 'm')).toBeNull();
    expect(mapEventToChunk({ type: 'tool_result', id: 'x', result: {} }, 'm')).toBeNull();
  });

  it('maps usage to an empty-delta chunk', () => {
    const c = mapEventToChunk(
      { type: 'usage', inputTokens: 10, outputTokens: 20 },
      'agentd/claude',
    );
    expect(c?.choices[0]?.delta).toEqual({});
  });

  it('maps error event to a stop chunk', () => {
    const c = mapEventToChunk({ type: 'error', message: 'oh no' }, 'agentd/claude');
    expect(c?.choices[0]?.finish_reason).toBe('stop');
  });

  it('newChunkId produces unique ids', () => {
    const ids = new Set([newChunkId(), newChunkId(), newChunkId()]);
    expect(ids.size).toBe(3);
  });

  it('buildFinalCompletion wraps content with usage', () => {
    const c = buildFinalCompletion('agentd/claude', 'PONG', 5, 2);
    expect(c.object).toBe('chat.completion');
    expect(c.choices[0]?.message.content).toBe('PONG');
    expect(c.usage).toEqual({ prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 });
  });
});