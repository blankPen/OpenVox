import { describe, expect, it } from 'vitest';
import { BaseProvider } from '../../src/providers/base.js';
import type {
  ProviderCapabilities,
  ProviderEvent,
  SendMessageInput,
  SendMessageResult,
} from '../../src/providers/base.js';

class StubProvider extends BaseProvider {
  readonly id = 'stub';
  readonly label = 'Stub';
  readonly protocol = 'stream-json' as const;
  calls: SendMessageInput[] = [];
  async send(input: SendMessageInput): Promise<SendMessageResult> {
    this.calls.push(input);
    async function* events(): AsyncGenerator<ProviderEvent, void, void> {
      yield { type: 'text', delta: 'pong' };
      yield { type: 'done' };
    }
    return { events: events() };
  }
  override capabilities(): ProviderCapabilities {
    return { supportsResume: false, supportsTools: false, supportsStreaming: true };
  }
}

describe('providers/base', () => {
  it('captures the input and emits events', async () => {
    const p = new StubProvider();
    const r = await p.send({ messages: [{ role: 'user', content: 'ping' }] });
    expect(p.calls[0]?.messages[0]?.content).toBe('ping');
    const events: ProviderEvent[] = [];
    for await (const e of r.events) events.push(e);
    expect(events).toEqual([{ type: 'text', delta: 'pong' }, { type: 'done' }]);
  });

  it('default capabilities are reasonable', () => {
    const p = new StubProvider();
    expect(p.capabilities().supportsStreaming).toBe(true);
  });

  it('fromConfig throws unless overridden', () => {
    expect(() => BaseProvider.fromConfig({
      id: 'x', label: 'x', command: 'x', protocol: 'stream-json', args: [],
    })).toThrow();
  });
});