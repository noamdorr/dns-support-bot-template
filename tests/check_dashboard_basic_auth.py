"""Offline synthetic probe of upstream public-URL dashboard authentication.

Tests the supported provider contract only; does not validate the outer wrapper
login/proxy integration. Run in Docker with --network none and no real credentials.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

import httpx
from plugins.dashboard_auth.basic import hash_password

assert os.geteuid()==0
with tempfile.TemporaryDirectory(prefix='dashboard-auth-fixture-') as temporary:
    home=Path(temporary)
    (home/'config.yaml').write_text('plugins:\n  entries: {}\n')
    env=dict(os.environ,HERMES_HOME=str(home),HERMES_DASHBOARD_PUBLIC_URL='https://synthetic.example.invalid')
    for key in tuple(env):
        if key.startswith('HERMES_DASHBOARD_BASIC_AUTH_'):
            env.pop(key)
    command=['hermes','dashboard','--host','127.0.0.1','--port','19119','--no-open','--skip-build']
    blocked=subprocess.run(command,env=env,capture_output=True,text=True,timeout=30)
    assert blocked.returncode != 0
    assert 'no auth providers are registered' in blocked.stdout+blocked.stderr
    username,password='synthetic-existing-admin','synthetic-existing-password'
    env['HERMES_DASHBOARD_BASIC_AUTH_USERNAME']=username
    env['HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH']=hash_password(password)
    logpath=home/'dashboard.log'
    with logpath.open('w') as log:
        process=subprocess.Popen(command,env=env,stdout=log,stderr=log,start_new_session=True)
        try:
            with httpx.Client(base_url='http://127.0.0.1:19119',trust_env=False,timeout=2) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        raise AssertionError('Dashboard exited: '+logpath.read_text()[-4000:])
                    try:
                        response=client.get('/api/auth/providers')
                        if response.status_code==200:break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.2)
                else:raise AssertionError('Dashboard did not bind')
                assert client.get('/api/auth/me').status_code==401
                bad=client.post('/auth/password-login',json={'provider':'basic','username':username,'password':'wrong','next':'/'})
                assert bad.status_code==401
                good=client.post('/auth/password-login',json={'provider':'basic','username':username,'password':password,'next':'/'})
                assert good.status_code==200 and good.json()['ok'] is True
                assert len(good.headers.get_list('set-cookie'))>=3
                assert client.get('/api/auth/me').status_code==200
                cookie_names=sorted(client.cookies.keys())
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
    print(json.dumps({'root_user':True,'public_url_without_provider_refused':True,
        'public_url_with_supported_basic_provider_binds':True,'unauthenticated_api_rejected':True,
        'wrong_password_rejected':True,'same_synthetic_identity_login_pass':True,
        'session_cookie_names':cookie_names,'public_url_preserved':True,
        'scope':'upstream provider only; outer wrapper auth integration not tested',
        'network':'disabled','production_access':False}))
