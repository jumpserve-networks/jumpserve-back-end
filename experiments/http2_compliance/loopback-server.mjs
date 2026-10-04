import http2 from 'node:http2';
const sessions = new Set();
const server = http2.createServer();
server.on('error', error => console.error(error.code ?? String(error)));
server.on('session', session => {
  sessions.add(session);
  session.on('error', error => console.error(error.code ?? String(error)));
  session.on('close', () => sessions.delete(session));
});
server.on('stream', stream => {
  stream.on('error', error => console.error(error.code ?? String(error)));
  stream.respond({ ':status': 200 });
  stream.end('loopback-control');
});
server.listen(0, '127.0.0.1', () => console.log(JSON.stringify({ port: server.address().port, versions: process.versions })));
process.on('SIGTERM', () => {
  for (const session of sessions) session.destroy();
  server.close(() => process.exit(0));
});
