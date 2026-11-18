import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {CognitoLogin} from '../../src/sentinelops/ui/assets/client.js';
import {Workspace} from '../../src/sentinelops/ui/assets/workspace.js';

const config = {mode:'oidc',issuer:'https://cognito-idp.example/pool',client_id:'public-client',
  domain:'https://operators.auth.example',jwks_url:'https://cognito-idp.example/pool/.well-known/jwks.json',
  callback_path:'/ui/callback',scopes:['openid','sentinelops/incidents.read','sentinelops/remediation.approve'],
  read_scope:'sentinelops/incidents.read',approval_scope:'sentinelops/remediation.approve'};
const origin = 'https://demo.cloudfront.example', now = Date.now();
const pair = await webcrypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,
  publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
const publicKey = {...await webcrypto.subtle.exportKey('jwk',pair.publicKey),kid:'id-key',use:'sig',alg:'RS256'};
const b64 = value => Buffer.from(typeof value === 'object' ? JSON.stringify(value) : value).toString('base64url');
async function signed(claims) {
  const input = b64({alg:'RS256',kid:'id-key'})+'.'+b64(claims);
  return input+'.'+Buffer.from(await webcrypto.subtle.sign('RSASSA-PKCS1-v1_5',pair.privateKey,new TextEncoder().encode(input))).toString('base64url');
}
function fixture({mutateId = () => {}, mutateTokens = () => {}, readOnly = false, fail = false} = {}) {
  const values = new Map(), calls = [], storage = {setItem:(k,v) => values.set(k,v), getItem:k => values.get(k) ?? null, removeItem:k => values.delete(k)};
  let transaction;
  const fetcher = async (url,options) => {
    calls.push({url,options});
    if (fail) return Response.json({error:'PRIVATE-PROVIDER-ERROR'},{status:400});
    if (url === config.jwks_url) return Response.json({keys:[publicKey]});
    assert.equal(url,config.domain+'/oauth2/token');
    const id = {iss:config.issuer,aud:config.client_id,sub:'operator-subject',token_use:'id',
      iat:Math.floor(now/1000),exp:Math.floor(now/1000)+300,nonce:transaction.nonce}; mutateId(id);
    const access = {iss:config.issuer,client_id:config.client_id,sub:'operator-subject',token_use:'access',
      exp:Math.floor(now/1000)+250,scope:config.read_scope+(readOnly ? '' : ' '+config.approval_scope)};
    // API signature verification is tested in Python; this fixture's access token
    // only exercises browser parsing. The ID token really is signed and verified.
    const tokens = {token_type:'Bearer',expires_in:300,id_token:await signed(id),
      access_token:b64({alg:'RS256',kid:'access-key'})+'.'+b64(access)+'.software-fixture',refresh_token:'NEVER-PERSIST-REFRESH'};
    mutateTokens(tokens); return Response.json(tokens);
  };
  const login = new CognitoLogin(config,{storage,crypto:webcrypto,fetcher,origin,now:() => now});
  return {login,storage,values,calls,async begin() {
    const url = new URL(await login.begin()); transaction = JSON.parse([...values.values()][0]);
    return {url,transaction,callback:origin+'/ui/callback?code=PRIVATE-CODE&state='+transaction.state};
  }};
}

test('authorize uses S256, random state/nonce, public client and exact callback; only transaction reaches sessionStorage',async () => {
  const f = fixture(), {url,transaction} = await f.begin();
  assert.equal(url.origin,config.domain); assert.equal(url.pathname,'/oauth2/authorize');
  assert.equal(url.searchParams.get('response_type'),'code');
  assert.equal(url.searchParams.get('code_challenge_method'),'S256');
  assert.equal(url.searchParams.get('redirect_uri'),origin+'/ui/callback');
  assert.equal(url.searchParams.get('scope'),config.scopes.join(' '));
  const challenge = Buffer.from(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(transaction.verifier))).toString('base64url');
  assert.equal(url.searchParams.get('code_challenge'),challenge);
  assert.match(transaction.state,/^[A-Za-z0-9_-]{43}$/); assert.match(transaction.nonce,/^[A-Za-z0-9_-]{43}$/);
  assert.notEqual(transaction.verifier,transaction.state); assert.notEqual(transaction.state,transaction.nonce);
  assert.equal(url.href.includes(transaction.verifier),false); assert.equal(url.searchParams.has('client_secret'),false);
  assert.deepEqual(Object.keys(transaction).sort(),['client_id','created_at','domain','issuer','nonce','redirect_uri','state','verifier']);
});

test('callback exchanges once, verifies signed nonce/issuer/client and keeps tokens entirely out of storage',async () => {
  const f = fixture(), {callback,transaction} = await f.begin();
  const result = await f.login.complete(callback);
  assert.equal(result.canApprove,true); assert.ok(result.expiresAt <= now+250000);
  assert.equal(f.values.size,0); assert.equal(JSON.stringify(f.login).includes(result.accessToken),false);
  const request = f.calls[0], body = new URLSearchParams(request.options.body);
  assert.equal(request.options.method,'POST'); assert.equal(request.options.credentials,'omit');
  assert.equal(request.options.redirect,'error'); assert.equal(body.get('code_verifier'),transaction.verifier);
  assert.equal(body.get('grant_type'),'authorization_code'); assert.equal(body.get('code'),'PRIVATE-CODE');
  assert.equal(body.has('client_secret'),false); assert.equal(request.options.headers.Authorization,undefined);
  assert.equal(f.calls[1].url,config.jwks_url);
  await assert.rejects(() => f.login.complete(callback),/đăng nhập lại/);
  assert.equal(f.calls.length,2);
});

test('read-only access stays readable and cannot dispatch a UI decision',async () => {
  const f = fixture({readOnly:true}), {callback} = await f.begin();
  const session = await f.login.complete(callback); assert.equal(session.canApprove,false);
  let sent = 0;
  const workspace = new Workspace(() => {},() => ({list:async () => ({items:[],next_cursor:null}),
    readiness:async () => ({}),decideProposal:async () => {sent++;}}));
  await workspace.connect(session.accessToken,{canApprove:false});
  workspace.state.proposals=[{proposal:{proposal_id:'a'.repeat(64),expires_at:'2099-01-01T00:00:00Z'},status:'pending'}];
  await workspace.decideProposal('a'.repeat(64),'approve','reviewed'); assert.equal(sent,0);
});

test('state, duplicate parameters, provider denial and expired/config-swapped transactions fail before token exchange',async () => {
  for (const modify of [x => x.callback.replace('state=','state=foreign'),x => x.callback+'&state=other',
    x => x.callback+'&code=duplicate',x => x.callback+'&error=access_denied',x => x.callback+'#access_token=forged',
    x => x.callback.replace(origin,'https://evil.example')]) {
    const f = fixture(), started = await f.begin();
    await assert.rejects(() => f.login.complete(modify(started)),/đăng nhập lại/);
    assert.equal(f.calls.length,0); assert.equal(f.values.size,0);
  }
  for (const patch of [{created_at:now-600001},{created_at:now+6000},{client_id:'other'},
    {redirect_uri:'https://evil.example/ui/callback'},{issuer:'https://evil.example'},{domain:'https://evil.example'}]) {
    const f=fixture(), started=await f.begin();
    f.storage.setItem('sentinelops.pkce.v1',JSON.stringify({...started.transaction,...patch}));
    await assert.rejects(() => f.login.complete(started.callback),/đăng nhập lại/);
    assert.equal(f.calls.length,0); assert.equal(f.values.size,0);
  }
});

test('ID nonce, issuer, audience, expiry or signature mismatch never creates a session',async () => {
  for (const mutateId of [id => id.nonce='other',id => id.iss='https://evil.example',id => id.aud='other',
    id => id.exp=0,id => id.token_use='access']) {
    const f=fixture({mutateId}), {callback}=await f.begin();
    await assert.rejects(() => f.login.complete(callback),/đăng nhập lại/); assert.equal(f.values.size,0);
  }
  const f=fixture({mutateTokens:t => t.id_token=t.id_token.slice(0,-5)+'AAAAA'}),{callback}=await f.begin();
  await assert.rejects(() => f.login.complete(callback),/đăng nhập lại/);
});

test('token/provider errors are sanitized, refresh tokens are discarded and pending redirect survives pagehide',async () => {
  const f=fixture({fail:true}),{callback}=await f.begin();
  f.login.suspend(); assert.equal(f.values.size,1);
  await assert.rejects(() => f.login.complete(callback),e => !e.message.includes('PRIVATE') && /đăng nhập lại/.test(e.message));
  assert.equal(f.values.size,0);
  for (const mutateTokens of [t => t.token_type='Basic',t => t.expires_in=-1,t => t.access_token='bad',
    t => t.id_token='bad']) {
    const bad=fixture({mutateTokens}),started=await bad.begin();
    await assert.rejects(() => bad.login.complete(started.callback),/đăng nhập lại/); assert.equal(bad.values.size,0);
  }
});

test('expiry disconnects and clear removes transient verifier',async () => {
  const f=fixture(); await f.begin(); let disconnected=0;
  f.login.expireAt(now-1,() => {disconnected++;}); assert.equal(disconnected,1);
  f.login.clear(); assert.equal(f.values.size,0);
});

