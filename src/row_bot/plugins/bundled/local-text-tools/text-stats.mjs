// Dependency-free example MCP server. No file, network or telemetry operations.
import { createInterface } from 'node:readline';

const tool = {
  name: 'text_stats', description: 'Count words, Unicode characters and lines in supplied text.',
  inputSchema: {type: 'object', properties: {text: {type: 'string', maxLength: 100000}}, required: ['text'], additionalProperties: false},
  annotations: {readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false},
};
const input = createInterface({input: process.stdin, crlfDelay: Infinity});
input.on('line', line => {
  if (line.length > 1024 * 1024) { input.close(); process.stdin.destroy(); return; }
  let request;
  try { request = JSON.parse(line); } catch { return; }
  if (!Object.hasOwn(request, 'id')) return;
  let result;
  if (request.method === 'initialize') result = {protocolVersion: request.params?.protocolVersion ?? '2025-11-25', capabilities: {tools: {}}, serverInfo: {name: 'row-bot-local-text-tools', version: '1.0.0'}};
  else if (request.method === 'ping') result = {};
  else if (request.method === 'tools/list') result = {tools: [tool]};
  else if (request.method === 'tools/call' && request.params?.name === 'text_stats') {
    const text = request.params.arguments?.text;
    if (typeof text !== 'string' || text.length > 100000) result = {isError: true, content: [{type: 'text', text: 'Supply text up to 100,000 characters.'}]};
    else result = {content: [{type: 'text', text: JSON.stringify({words: text.trim() ? text.trim().split(/\s+/u).length : 0, characters: [...text].length, lines: text ? text.split(/\r?\n/u).length : 0})}]};
  }
  const response = result ? {jsonrpc: '2.0', id: request.id, result} : {jsonrpc: '2.0', id: request.id, error: {code: -32601, message: 'Method not found'}};
  process.stdout.write(JSON.stringify(response) + '\n');
});
