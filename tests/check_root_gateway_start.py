"""Real CLI root-guard/startup probe. Run in Docker with --network none.

Only disposable synthetic homes are used. No adapters, jobs, models or provider
credentials are configured. This validates boot, not external integrations.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

assert os.geteuid() == 0
assert os.environ.get('HERMES_ALLOW_ROOT_GATEWAY') == '1'
with tempfile.TemporaryDirectory(prefix='root-start-fixture-') as temporary:
    root = Path(temporary)
    env = dict(os.environ)
    env['HERMES_HOME'] = str(root/'blocked')
    env['HERMES_ALLOW_ROOT_GATEWAY'] = '0'
    blocked = subprocess.run(['hermes','gateway','run','--replace'],env=env,
                             capture_output=True,text=True,timeout=30)
    assert blocked.returncode == 1
    assert 'Refusing to run the Hermes gateway as root' in blocked.stdout+blocked.stderr
    assert not (Path(env['HERMES_HOME'])/'state.db').exists()

    home = root/'allowed'
    home.mkdir()
    (home/'config.yaml').write_text('gateway:\n  startup_watchdog_seconds: 0\nplatforms: {}\n')
    env['HERMES_HOME'] = str(home)
    env['HERMES_ALLOW_ROOT_GATEWAY'] = '1'
    logfile = root/'gateway.log'
    state = {}
    with logfile.open('w') as log:
        process = subprocess.Popen(['hermes','gateway','run','--replace','-v'],env=env,
                                   stdout=log,stderr=log,start_new_session=True)
        try:
            deadline = time.monotonic()+45
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise AssertionError('Gateway exited before ready: '+logfile.read_text()[-6000:])
                status = home/'gateway_state.json'
                if status.exists():
                    try:
                        state=json.loads(status.read_text())
                    except json.JSONDecodeError:
                        pass
                if state.get('gateway_state') == 'running' and (home/'state.db').exists():
                    break
                time.sleep(0.25)
            else:
                raise AssertionError('Gateway did not reach running: '+logfile.read_text()[-6000:])
            assert 'Refusing to run the Hermes gateway as root' not in logfile.read_text()
            assert (home/'state.db').stat().st_uid == 0
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL)
                    process.wait(timeout=5)
    print(json.dumps({'root_guard_reproduced_without_opt_in':True,
        'actual_gateway_cli_running_with_opt_in':True,'runtime_database_created_as_existing_root_user':True,
        'gateway_state':state.get('gateway_state'),'network':'disabled',
        'provider_credentials':False,'production_access':False}))
