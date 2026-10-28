// Opt-in guard for synthetic Node tests; not an OS sandbox or app dependency.
'use strict';
if (process.env.CLASSROOM_PR00_TEST_GUARD === '1') {
  const net = require('node:net');
  const dns = require('node:dns');
  const child = require('node:child_process');
  const dgram = require('node:dgram');
  const loopback = host => {
    host = String(host).toLowerCase().replace(/\.$/, '');
    return host === 'localhost' || host === '::1' ||
      (net.isIP(host) === 4 && host.startsWith('127.')) ||
      (net.isIP(host) === 6 && host.startsWith('::ffff:127.'));
  };
  const deny = () => { throw new Error('PR00 guard: external network or real subprocess is disabled'); };
  const hostFrom = (args, listening = false) => {
    if (Array.isArray(args[0])) return hostFrom(args[0], listening);
    if (args[0] && typeof args[0] === 'object') {
      if (args[0].path) return null;
      return args[0].host || (listening ? null : 'localhost');
    }
    if (typeof args[0] === 'number') return typeof args[1] === 'string' ? args[1] : (listening ? null : 'localhost');
    return null;
  };
  const connect = net.Socket.prototype.connect;
  net.Socket.prototype.connect = function (...args) {
    if (!loopback(hostFrom(args))) deny();
    return connect.apply(this, args);
  };
  const listen = net.Server.prototype.listen;
  net.Server.prototype.listen = function (...args) {
    if (!loopback(hostFrom(args, true))) deny();
    return listen.apply(this, args);
  };
  for (const owner of [dns, dns.promises]) {
    for (const name of Object.keys(owner)) {
      if ((name === 'lookup' || name.startsWith('resolve') || name === 'reverse') && typeof owner[name] === 'function') {
        const original = owner[name];
        owner[name] = function (host, ...args) {
          if (!loopback(host)) deny();
          return original.call(this, host, ...args);
        };
      }
    }
  }
  // The current test suite needs TCP loopback, never UDP or child processes.
  dgram.createSocket = deny;
  for (const name of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) child[name] = deny;
  globalThis.classroomPr00Guard = true;
}
