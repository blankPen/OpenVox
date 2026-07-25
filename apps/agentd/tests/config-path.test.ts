import { describe, expect, it } from 'vitest';
import { resolveConfigPath } from '../src/config/loader.js';

describe('resolveConfigPath', () => {
  it('prefers explicit path over AGENTD_CONFIG', () => {
    process.env.AGENTD_CONFIG = '/tmp/from-env.json';
    expect(resolveConfigPath('/tmp/explicit.json')).toBe('/tmp/explicit.json');
  });

  it('falls back to AGENTD_CONFIG when no explicit path', () => {
    process.env.AGENTD_CONFIG = '/tmp/from-env.json';
    expect(resolveConfigPath(undefined)).toBe('/tmp/from-env.json');
  });
});
