"""Real wrapper -> dashboard HTTP/WS auth, entirely synthetic and offline.

Run in a fresh container with --network none. Never mounts a production home.
No agent turn, provider request, platform adapter or paid operation is invoked.
"""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from urllib.parse import urlencode

import httpx
import websockets
import hermes_yaml as yaml

home=Path('/data/.hermes')
home.mkdir(parents=True,exist_ok=True)
assert not (home/'state.db').exists(), 'Requires a fresh disposable container'
config='_config_version: 51\ndelegation:\n  max_iterations: 50\ndisplay:\n  background_process_notifications: all\nplatform_toolsets:\n  telegram: [jira, wiki, jira-read]\nmodel:\n  default: synthetic-model\n  provider: azure\ntelegram:\n  gateway_restart_notification: false\n'
(home/'config.yaml').write_text(config)
env_contents='LLM_MODEL=synthetic-model\nAZURE_FOUNDRY_API_KEY=synthetic-offline-key\n'
(home/'.env').write_text(env_contents)
username,password='synthetic-existing-admin','synthetic-existing-password'
env=dict(os.environ,ADMIN_USERNAME=username,ADMIN_PASSWORD=password,PORT='18080',
    HERMES_DASHBOARD_PUBLIC_URL='http://synthetic.example.invalid',
    HERMES_DASHBOARD_BASIC_AUTH_PASSWORD='synthetic-unwanted-override')
base='http://127.0.0.1:18080'

async def socket_probe(cookie, ticket, *, protocols=False, accepted=True, origin='http://synthetic.example.invalid'):
    url='ws://127.0.0.1:18080/api/events?'+urlencode({'channel':'synthetic-auth-check',**({} if protocols else {'ticket':ticket})})
    kwargs={'additional_headers':{'Cookie':cookie},'open_timeout':5,'origin':origin}
    if protocols:
        kwargs['subprotocols']=['hermes-gateway-v1','hermes-gateway-ticket.'+ticket]
    try:
        async with websockets.connect(url,**kwargs) as socket:
            assert accepted, 'Unauthenticated or reused ticket admitted'
            # The events endpoint may select no protocol. Never reflect the
            # credential-bearing ticket protocol back to the browser.
            if protocols:assert socket.subprotocol in (None,'hermes-gateway-v1')
    except websockets.exceptions.InvalidStatus:
        assert not accepted, 'Authenticated WebSocket was rejected'

with tempfile.TemporaryDirectory(prefix='wrapper-auth-log-') as tmp:
    logpath=Path(tmp)/'wrapper.log'
    with logpath.open('w') as log:
        process=subprocess.Popen(['/app/start.sh'],env=env,stdout=log,stderr=log,start_new_session=True)
        try:
            with httpx.Client(base_url=base,trust_env=False,timeout=10,follow_redirects=False) as client:
                for _ in range(120):
                    if process.poll() is not None:
                        raise AssertionError('Wrapper exited: '+logpath.read_text()[-3000:])
                    try:
                        health=client.get('/health')
                        # Probe the actual backend's public provider metadata only.
                        ready=httpx.get('http://127.0.0.1:9119/api/auth/providers',trust_env=False,timeout=1)
                        status=home/'gateway_state.json'
                        state=json.loads(status.read_text()) if status.exists() else {}
                        if health.status_code==200 and ready.status_code==200 and state.get('gateway_state')=='running':
                            initial_gateway_pid=state['pid']
                            break
                    except httpx.HTTPError:pass
                    time.sleep(.2)
                else:raise AssertionError('Dashboard did not bind: '+logpath.read_text()[-3000:])
                assert client.get('/api/auth/me').status_code==401
                bad=client.post('/login',data={'username':username,'password':'wrong','returnTo':'/'})
                assert bad.status_code==302 and 'error=1' in bad.headers['location']
                assert not bad.headers.get_list('set-cookie')
                good=client.post('/login',data={'username':username,'password':password,'returnTo':'/?force=1'})
                assert good.status_code==302 and len(good.headers.get_list('set-cookie'))==4
                assert client.get('/api/auth/me').status_code==200
                page=client.get('/?force=1',headers={'accept':'text/html'})
                assert page.status_code==200
                assert '__HERMES_AUTH_REQUIRED__' in page.text
                # Only an existing outer cookie: no shared-httpx-cookie leakage,
                # then the backend /login redirect is bridged without a loop.
                with httpx.Client(base_url=base,trust_env=False,timeout=10,
                                  cookies={'hermes_auth':client.cookies['hermes_auth']}) as old:
                    assert old.get('/api/auth/me').status_code==401
                    redirect=old.get('/?force=1',headers={'accept':'text/html'})
                    assert redirect.status_code==302 and '/login' in redirect.headers['location']
                    bridged=old.get(redirect.headers['location'])
                    assert bridged.status_code==302 and len(bridged.headers.get_list('set-cookie'))==3
                    assert old.get('/api/auth/me').status_code==200
                # Real upstream response through the ordinary HTTP proxy keeps
                # all Set-Cookie fields, not a comma-folded single header.
                upstream_login=client.post('/auth/password-login',json={
                    'provider':'basic','username':username,'password':password,'next':'/'})
                assert upstream_login.status_code==200
                assert len(upstream_login.headers.get_list('set-cookie'))==3
                cookie='; '.join(f'{k}={v}' for k,v in client.cookies.items())
                ticket=client.post('/api/auth/ws-ticket').json()['ticket']
                asyncio.run(socket_probe('',ticket,accepted=False))
                asyncio.run(socket_probe(cookie,ticket,accepted=False,origin='http://untrusted.example.invalid'))
                # Upstream consumes the one-shot ticket before checking Origin.
                ticket=client.post('/api/auth/ws-ticket').json()['ticket']
                asyncio.run(socket_probe(cookie,ticket))
                asyncio.run(socket_probe(cookie,ticket,accepted=False))
                ticket2=client.post('/api/auth/ws-ticket').json()['ticket']
                asyncio.run(socket_probe(cookie,ticket2,protocols=True))
                # TLS termination preserves upstream secure, prefixed cookies.
                secure=client.post('/login',headers={'x-forwarded-proto':'https'},
                    data={'username':username,'password':password,'returnTo':'/'})
                backend_cookies=[h for h in secure.headers.get_list('set-cookie') if h.startswith('__Host-hermes_session_')]
                assert len(backend_cookies)==3 and all('Secure' in h for h in backend_cookies)
                response=client.get('/logout')
                assert response.status_code==302
                assert client.get('/api/auth/me').status_code==401
                saved=yaml.safe_load((home/'config.yaml').read_text())
                original=yaml.safe_load(config)
                def assert_retained(expected, actual):
                    if isinstance(expected,dict):
                        for key,value in expected.items():assert_retained(value,actual[key])
                    else:assert actual==expected
                assert_retained(original,saved)
                assert 'basic_auth' not in saved.get('dashboard',{})
                assert (home/'.env').read_text()==env_contents
                # Catch a child that briefly reported running and then failed
                # while mounting its own dashboard-backed runtime API.
                time.sleep(4)
                state=json.loads((home/'gateway_state.json').read_text())
                assert state['gateway_state']=='running' and state['pid']==initial_gateway_pid
                assert logpath.read_text().count('[gateway] model=')==1
                assert password not in logpath.read_text()
                assert 'scrypt$' not in logpath.read_text()
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
print(json.dumps({'outer_bad_login_rejected':True,'same_identity_backend_bridge':True,
    'protected_http_pass':True,'old_outer_cookie_repaired_without_loop':True,
    'multiple_set_cookie_preserved':True,'ws_outer_gate':True,'ws_query_ticket':True,
    'ws_ticket_replay_rejected':True,'ws_untrusted_origin_rejected':True,'ws_subprotocol_ticket':True,'https_secure_backend_cookies':True,
    'logout_relocks_outer_gate':True,'explicit_config_and_env_unchanged':True,'no_secret_logs':True,
    'gateway_stays_running_with_public_url_and_dashboard':True,'version51_original_policies_stable':True,
    'public_url_preserved':True,'network':'disabled','model_turns':0,'production_access':False}))
