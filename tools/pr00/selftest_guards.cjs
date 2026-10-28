'use strict';
const assert = require('node:assert/strict');
const net = require('node:net');
const dns = require('node:dns');
const child = require('node:child_process');
assert.equal(globalThis.classroomPr00Guard, true, 'Node guard must be preloaded');
assert.throws(() => new net.Socket().connect({host: '192.0.2.1', port: 443}), /PR00 guard/);
assert.throws(() => net.createServer().listen({host: '0.0.0.0', port: 0}), /PR00 guard/);
assert.throws(() => dns.lookup('pr00-example.invalid', () => {}), /PR00 guard/);
assert.throws(() => child.spawn('codex', ['--version']), /PR00 guard/);
const server = net.createServer(socket => { socket.end('synthetic'); });
server.listen({host: '127.0.0.1', port: 0}, () => {
  const client = net.createConnection({host: '127.0.0.1', port: server.address().port});
  let value = '';
  client.on('data', bytes => { value += bytes.toString(); });
  client.on('end', () => {
    assert.equal(value, 'synthetic');
    server.close(() => console.log('PASS: Node guard, loopback, external/DNS/bind/subprocess denial; synthetic only'));
  });
  client.on('error', error => { throw error; });
});
server.on('error', error => { throw error; });
