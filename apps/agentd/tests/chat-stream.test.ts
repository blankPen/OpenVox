/**
 * Tests the stream layer: NDJSON parser, OpenAI shape conversion.
 */
import { describe, it, expect } from 'vitest';
import { Readable } from 'node:stream';
import { parseNdjson } from '../src/stream/ndjson.js';
import {
  mapEventToChunk,
  buildFinalCompletion,
} from '../src/stream/openai-shape.js';

describe('parseNdjson', () => {
  it('parses well-formed NDJSON line by line', async () => {
    const input = Readable.from(['{"a":1}\n', '{"b":2}\n', '{"c":3}\n']);
    const out: Array<Record<string, unknown>> = [];
    for await (const obj of parseNdjson(input)) out.push(obj);
    expect(out).toEqual([{ a: 1 }, { b: 2 }, { c: 3 }]);
  });

  it('tolerates CR/LF line endings', async () => {
    const input = Readable.from(['{"a":1}\r\n', '{"b":2}\r\n']);
    const out: Array<Record<string, unknown>> = [];
    for await (const obj of parseNdjson(input)) out.push(obj);
    expect(out).toEqual([{ a: 1 }, { b: 2 }]);
  });

  it('drops malformed JSON without throwing', async () => {
    const input = Readable.from([
      '{"good":1}\n',
      'this is not json\n',
      '{"also":"good"}\n',
    ]);
    const out: Array<Record<string, unknown>> = [];
    for await (const obj of parseNdjson(input)) out.push(obj);
    expect(out).toEqual([{ good: 1 }, { also: 'good' }]);
  });

  it('flushes trailing unterminated line', async () => {
    const input = Readable.from(['{"tail":1}']);
    const out: Array<Record<string, unknown>> = [];
    for await (const obj of parseNdjson(input)) out.push(obj);
    expect(out).toEqual([{ tail: 1 }]);
  });
});

describe('mapEventToChunk', () => {
  it('turns a text delta into a content chunk', () => {
    const chunk = mapEventToChunk({ type: 'text', delta: 'PONG' }, 'agentd/test');
    expect(chunk).not.toBeNull();
    expect(chunk!.choices[0]!.delta['content']).toBe('PONG');
    expect(chunk!.choices[0]!.finish_reason).toBeNull();
    expect(chunk!.model).toBe('agentd/test');
  });

  it('done event maps to finish_reason: stop', () => {
    const chunk = mapEventToChunk({ type: 'done', stopReason: 'end_turn' }, 'agentd/x');
    expect(chunk!.choices[0]!.finish_reason).toBe('stop');
  });

  it('done with tool_use maps to finish_reason: tool_calls', () => {
    const chunk = mapEventToChunk({ type: 'done', stopReason: 'tool_use' }, 'agentd/x');
    expect(chunk!.choices[0]!.finish_reason).toBe('tool_calls');
  });

  it('session_id event returns null (internal, not surfaced)', () => {
    const chunk = mapEventToChunk({ type: 'session_id', id: 'abc' }, 'agentd/x');
    expect(chunk).toBeNull();
  });

  it('error event yields empty delta with finish_reason: stop', () => {
    const chunk = mapEventToChunk({ type: 'error', message: 'bad' }, 'agentd/x');
    expect(chunk!.choices[0]!.finish_reason).toBe('stop');
  });
});

describe('buildFinalCompletion', () => {
  it('builds a non-stream ChatCompletion envelope', () => {
    const out = buildFinalCompletion('agentd/x', 'hello world', 5, 7);
    expect(out.choices[0]!.message.content).toBe('hello world');
    expect(out.usage).toEqual({ prompt_tokens: 5, completion_tokens: 7, total_tokens: 12 });
    expect(out.model).toBe('agentd/x');
    expect(out.object).toBe('chat.completion');
  });
});
