// Fixture MCP server: running it leaves a marker beside it, so a test can prove it never started.
import { writeFileSync } from 'node:fs';

writeFileSync(new URL('./server-ran.txt', import.meta.url), 'server ran');
