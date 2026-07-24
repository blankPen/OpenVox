// Vitest setup — runs before any test file imports its modules.
//
// We default HOME to a per-process temp directory so tests that touch
// ~/.agentd never collide. Tests that need a specific home override the
// value again in their own beforeAll and reload modules as needed.
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const defaultHome = path.join(os.tmpdir(), `agentd-test-home-${process.pid}`);
await fs.mkdir(defaultHome, { recursive: true });
process.env['AGENTD_HOME'] = defaultHome;
if (!process.env['HOME']) process.env['HOME'] = defaultHome;