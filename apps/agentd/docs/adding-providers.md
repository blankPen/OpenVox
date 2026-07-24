# Adding a Provider

Three ways to bring a new agent binary into agentd, in increasing effort.

## 1. Config-only (no code change) — for ACP-compatible binaries

If your CLI speaks ACP-compatible JSON-RPC over stdio, drop a config entry
into `~/.agentd/config.json`:

```json
{
  "providers": [
    {
      "id": "my-acp",
      "label": "My ACP CLI",
      "command": "/usr/local/bin/my-acp-cli",
      "args": ["--serve", "--stdio"],
      "protocol": "acp",
      "env": { "MY_CLI_API_KEY": "..." }
    }
  ]
}
```

Restart `pnpm dev`. The new provider will appear in `/v1/models`
(`agentd/my-acp`) and start receiving requests addressed to it.

The `generic-acp` factory (`src/providers/generic-acp.ts`) handles the rest:
it spawns the subprocess, sends an `initialize` JSON-RPC frame, and
forwards any `text` / `error` / `session_id` events through the stream.

## 2. Auto-discovered factory (binary already in PATH)

If a binary named `claude`, `codex`, or `openclaw` lives in `PATH` (or
`~/.local/bin`), `src/providers/discovery.ts` will pick it up
automatically. The `FACTORIES` table (`src/providers/registry.ts`)
maps binary names to providers.

To add a new auto-discovered binary:

1. Add a probe entry in `src/providers/discovery.ts`:

   ```ts
   { id: 'your-cli', protocols: ['stream-json'] }
   ```

2. Add a factory in `src/providers/registry.ts`:

   ```ts
   import { buildYourProvider } from './your-provider.js';

   export const FACTORIES = {
     // ...
     'your-cli': {
       id: 'your-cli',
       label: 'Your CLI',
       binaryIds: ['your-cli'],
       build: (discovered, cfg) => buildYourProvider(discovered, cfg),
     },
   };
   ```

3. Implement `src/providers/your-provider.ts`. A `stream-json` provider
   follows the same shape as `claude.ts` — see that file's structure.

## 3. Full custom provider (new protocol, business logic)

Implement a `BaseProvider` subclass that:

- spawns / connects to the upstream,
- maps upstream events into the internal `ProviderEvent` shape
  (see `src/providers/base.ts`),
- honours `input.signal` for cleanup.

Then add it to `FACTORIES` as in option 2.

```ts
import { BaseProvider, type ProviderEvent, type SendMessageInput, type SendMessageResult } from './base.js';

export class MyProvider extends BaseProvider {
  readonly id = 'my-provider';
  readonly label = 'My Provider';
  readonly protocol = 'stream-json' as const;

  async send(input: SendMessageInput): Promise<SendMessageResult> {
    async function* events(): AsyncGenerator<ProviderEvent, void, void> {
      yield { type: 'text', delta: 'Hello from my provider!' };
      yield { type: 'done', stopReason: 'end_turn' };
    }
    return { events: events() };
  }
}
```

## Testing the new provider

Add a vitest spec under `tests/providers/`:

```ts
import { describe, expect, it } from 'vitest';
import { MyProvider } from '../../src/providers/my-provider.js';

describe('my-provider', () => {
  it('emits a single text event and done', async () => {
    const p = new MyProvider();
    const result = await p.send({ messages: [{ role: 'user', content: 'hi' }] });
    const events: string[] = [];
    for await (const e of result.events) {
      if (e.type === 'text') events.push(e.delta);
      if (e.type === 'done') break;
    }
    expect(events).toEqual(['Hello from my provider!']);
  });
});
```

Then:

```bash
pnpm test
./scripts/verify.sh
```

## Reference

- `~/workspace/paseo/packages/server/src/server/agent/provider-registry.ts:109`
  — `PROVIDER_CLIENT_FACTORIES` factory table pattern.
- `~/workspace/paseo/packages/server/src/server/agent/providers/claude/agent.ts`
  — Claude Code subprocess spawn.
- `~/workspace/paseo/packages/server/src/server/agent/providers/copilot-acp-agent.ts`
  — ACP client connection example (for a future ACP-server-mode agentd).
