import { describe, expect, it } from 'vitest';
import { ClaudeProvider, buildClaudeProvider } from '../../src/providers/claude.js';

describe('providers/claude', () => {
  it('buildClaudeProvider returns a ClaudeProvider', () => {
    const p = buildClaudeProvider(
      { command: '/usr/local/bin/claude', version: '2.1.202' }, null,
    );
    expect(p.id).toBe('claude');
    expect(p.protocol).toBe('stream-json');
  });

  it('honours cfg.command when provided', () => {
    const p = buildClaudeProvider(null, {
      id: 'claude-custom', label: 'Custom Claude',
      command: '/opt/claude/bin/claude', args: [], protocol: 'stream-json',
    });
    expect((p as ClaudeProvider).command).toBe('/opt/claude/bin/claude');
  });

  it('falls back to bare "claude" command when nothing provided', () => {
    const p = buildClaudeProvider(null, null);
    expect((p as ClaudeProvider).command).toBe('claude');
  });
});