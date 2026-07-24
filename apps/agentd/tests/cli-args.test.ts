import { describe, expect, it } from 'vitest';
import { parseAgentdArgs } from '../src/cli-args.js';

describe('parseAgentdArgs', () => {
  it('parses --check and an explicit config path', () => {
    expect(parseAgentdArgs(['--check', '--config', '/tmp/openvox-agentd.json'])).toEqual({
      check: true,
      configPath: '/tmp/openvox-agentd.json',
    });
  });

  it('rejects --config without a value', () => {
    expect(() => parseAgentdArgs(['--config'])).toThrow(/--config requires a path/);
  });
});
