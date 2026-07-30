/**
 * Minimal CLI argument parser for `agentd`.
 *
 * Supports:
 *   --check                 # validate config, print summary, exit
 *   --config <path>         # explicit config path (overrides AGENTD_CONFIG / ~/.agentd/config.json)
 *
 * Any other flag triggers a hard error so we don't silently ignore typos
 * like `--port 9000`.
 */

export interface ParsedAgentdArgs {
  check: boolean;
  configPath?: string;
}

export class AgentdArgError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AgentdArgError';
  }
}

export function parseAgentdArgs(argv: readonly string[]): ParsedAgentdArgs {
  const out: ParsedAgentdArgs = { check: false };

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--check') {
      out.check = true;
      continue;
    }
    if (arg === '--config') {
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) {
        throw new AgentdArgError('--config requires a path argument');
      }
      out.configPath = next;
      i += 1;
      continue;
    }
    throw new AgentdArgError(`unknown argument: ${arg}`);
  }

  return out;
}
