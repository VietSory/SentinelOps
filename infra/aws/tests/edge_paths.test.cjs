const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const hcl = fs.readFileSync(path.join(__dirname, '..', 'edge.tf'), 'utf8');
const source = hcl.match(/code\s*=\s*<<-JS\n([\s\S]*?)\n\s*JS/)[1];
const context = vm.createContext({});
vm.runInContext(source, context);

for (const uri of ['/ui', '/ui/', '/ui/callback', '/ui/assets/app.js', '/v1/incidents',
  '/v1/incidents/abc/actions', '/grafana', '/grafana/', '/grafana/login/generic_oauth',
  '/grafana/public/build/app.js', '/grafana/public/plugins/jaeger/module.js',
  '/grafana/public/plugins/jaeger/plugin.json', '/grafana/public/plugins/jaeger/img/logo.svg',
  '/grafana/api/ds/query']) {
  test(`allows ${uri} without changing OAuth query or headers`, () => {
    const request = {method: 'GET', uri, querystring: {code: {value: 'a%2Bb'}, state: {value: 'nonce'}},
      headers: {authorization: {value: 'Bearer token'}, cookie: {value: 'session'}}};
    assert.equal(context.handler({request}), request);
  });
}
for (const uri of ['/', '/v1', '/ui-other', '/grafana-evil/', '/docs', '/docs/', '/redoc',
  '/openapi.json', '/internal/faults', '/prometheus/', '/jaeger/', '/metrics',
  '/grafana/public/plugins/jaeger', '/grafana/public/plugins/jaeger/api',
  '/grafana/public/plugins/jaeger/img/other.txt', '/grafana/jaeger/', '/grafana/api/jaeger/',
  '/ui/docs', '/v1/internal/faults', '/grafana/prometheus/', '/v1/jaeger', '/v1/DOCS',
  '/ui/%2e%2e/docs', '/ui/%252e%252e/docs', '/ui/../docs', '/ui/./assets/app.js',
  '/ui//assets/app.js', '/ui\\..\\docs', '/%75i/', '/grafana/%2fdocs']) {
  test(`denies ${uri}`, () => assert.equal(context.handler({request: {uri}}).statusCode, 403));
}
for (const method of ['GET', 'HEAD']) {
  test(`allows ${method} for the Jaeger plugin module`, () => {
    const request = {method, uri: '/grafana/public/plugins/jaeger/module.js'};
    assert.equal(context.handler({request}), request);
  });
}
test('denies non-read methods for the Jaeger plugin module', () => {
  const request = {method: 'POST', uri: '/grafana/public/plugins/jaeger/module.js'};
  assert.equal(context.handler({request}).statusCode, 403);
});
