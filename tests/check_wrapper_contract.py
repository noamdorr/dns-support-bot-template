"""Offline wrapper/config checks with synthetic data and no service startup."""
import asyncio
import json
import os
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

with tempfile.TemporaryDirectory(prefix='wrapper-fixture-') as temporary:
    os.environ['HERMES_HOME'] = temporary
    os.environ['ADMIN_PASSWORD'] = 'synthetic-offline-test-only'
    os.environ['ADMIN_USERNAME'] = 'synthetic-test'
    os.environ['HERMES_DASHBOARD_PUBLIC_URL'] = 'https://synthetic.example.invalid'
    os.environ['HERMES_DASHBOARD_BASIC_AUTH_PASSWORD'] = 'synthetic-inherited-override'
    module = runpy.run_path('/app/server.py', run_name='wrapper_contract_check')
    import hermes_yaml as yaml
    config = {
        'model': {'default': 'synthetic-model', 'provider': 'azure'},
        'telegram': {'gateway_restart_notification': False, 'require_mention': False},
        'platform_toolsets': {'telegram': ['jira-read', 'wiki']},
        'plugins': {'entries': {'triage': {'enabled': True}}},
        'session_reset': {'mode': 'both', 'idle_minutes': 1440},
        'mcp_servers': {'synthetic': {'command': '/synthetic/read-only'}},
        'agent': {'max_iterations': 12},
    }
    file = Path(temporary) / 'config.yaml'
    file.write_text(yaml.safe_dump(config))
    (Path(temporary) / '.env').write_text('LLM_MODEL=synthetic-model\n')
    module['write_config_yaml']({'LLM_MODEL': 'synthetic-model'})
    actual = yaml.safe_load(file.read_text())
    for key, value in config.items():
        assert actual[key] == value, key
    assert actual['data_dir'] == temporary
    assert Path('/opt/hermes-agent').resolve() == Path('/opt/hermes')
    assert Path('/opt/hermes/hermes_cli/web_dist/index.html').exists()
    assert Path('/opt/hermes/ui-tui/dist/entry.js').exists()

    async def check_startup_hold():
        sentinel = Path(temporary) / '.startup-hold'
        sentinel.touch()
        before = file.read_bytes()
        # Use actual entrypoints; reject any spawn before asserting call counts.
        spawn = AsyncMock(side_effect=AssertionError('A held service must not spawn'))
        with patch.object(asyncio, 'create_subprocess_exec', spawn):
            for reset_budget in (True, False):
                gateway = module['Gateway']()
                gateway._recent_exits = [123.0]
                await gateway.start(reset_budget=reset_budget)
                assert gateway.proc is None and gateway.state == 'held'
                assert gateway._recent_exits == [123.0]
                assert '.startup-hold' in gateway.logs[-1]
            dashboard = module['Dashboard']()
            await dashboard.start()
            assert dashboard.proc is None and dashboard._drain_task is None
            assert '.startup-hold' in dashboard.logs[-1]
            # New manager objects still see the persistent hold.
            await module['Dashboard']().start()
            spawn.assert_not_awaited()
        assert sentinel.exists() and file.read_bytes() == before

        sentinel.unlink()
        # No real subprocesses in this test. Verify absence preserves the existing
        # argv/env, config write and running-process idempotence at both entrypoints.
        gateway = module['Gateway']()
        dashboard = module['Dashboard']()
        gateway_proc = SimpleNamespace(returncode=None, pid=12345)
        dashboard_proc = SimpleNamespace(returncode=None, pid=12346)
        spawn = AsyncMock(side_effect=[gateway_proc, dashboard_proc])
        with patch.object(asyncio, 'create_subprocess_exec', spawn), \
             patch.object(gateway, '_drain', AsyncMock()), \
             patch.object(dashboard, '_drain', AsyncMock()):
            await gateway.start()
            await dashboard.start()
            await asyncio.sleep(0)
            assert gateway.state == 'running' and gateway.proc is gateway_proc
            assert dashboard.proc is dashboard_proc
            assert spawn.await_count == 2
            gateway_call, dashboard_call = spawn.await_args_list
            assert gateway_call.args == ('hermes', 'gateway', 'run', '--replace')
            assert dashboard_call.args == ('hermes', 'dashboard', '--host',
                module['HERMES_DASHBOARD_HOST'], '--port',
                str(module['HERMES_DASHBOARD_PORT']), '--no-open', '--skip-build')
            for call in (gateway_call, dashboard_call):
                assert call.kwargs['env']['HERMES_HOME'] == temporary
                assert call.kwargs['env']['HERMES_ALLOW_ROOT_GATEWAY'] == '1'
            from plugins.dashboard_auth.basic import _verify_password
            dashboard_env = dashboard_call.kwargs['env']
            assert dashboard_env['HERMES_DASHBOARD_BASIC_AUTH_USERNAME'] == 'synthetic-test'
            assert _verify_password('synthetic-offline-test-only', dashboard_env['HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH'])
            assert 'HERMES_DASHBOARD_BASIC_AUTH_PASSWORD' not in dashboard_env
            assert dashboard_env['HERMES_DASHBOARD_PUBLIC_URL'] == 'https://synthetic.example.invalid'
            await gateway.start()
            await dashboard.start()
            assert spawn.await_count == 2
        after_start = yaml.safe_load(file.read_text())
        for key, value in config.items():
            assert after_start[key] == value, key
        assert after_start['data_dir'] == temporary

        # Exercise the actual WS proxy boundary with inert pumps: only backend
        # session cookies cross the hop, while query/subprotocol tickets survive.
        connection = SimpleNamespace(subprotocol='hermes-gateway-v1', close=AsyncMock())
        socket = SimpleNamespace(
            cookies={'hermes_auth': module['_make_auth_token'](),
                     'hermes_session_at': 'synthetic-access',
                     '__Host-hermes_session_provider': 'basic', 'unrelated': 'private'},
            url=SimpleNamespace(path='/api/events', query='ticket=synthetic-ticket'),
            scope={'subprotocols': ['hermes-gateway-v1', 'hermes-gateway-ticket.synthetic-ticket']},
            headers={'origin': 'https://synthetic.example.invalid'},
            accept=AsyncMock(), close=AsyncMock(), client_state=None,
        )
        connect = AsyncMock(return_value=connection)
        namespace = module['ws_proxy'].__globals__
        with patch.object(module['websockets'], 'connect', connect), patch.dict(namespace, {
            '_ws_pump_client_to_upstream': AsyncMock(), '_ws_pump_upstream_to_client': AsyncMock(),
        }):
            await module['ws_proxy'](socket)
        call = connect.await_args
        assert call.args[0].endswith('/api/events?ticket=synthetic-ticket')
        forwarded = call.kwargs['additional_headers']['Cookie']
        assert 'hermes_session_at=synthetic-access' in forwarded
        assert '__Host-hermes_session_provider=basic' in forwarded
        assert 'hermes_auth' not in forwarded and 'unrelated' not in forwarded
        assert call.kwargs['subprotocols'] == socket.scope['subprotocols']
        assert call.kwargs['origin'] == 'https://synthetic.example.invalid'
        socket.accept.assert_awaited_once_with(subprotocol='hermes-gateway-v1')

    asyncio.run(check_startup_hold())
    print(json.dumps({'config_preserved': True, 'wrapper_imports': True,
                      'legacy_code_path_alias': True, 'compiled_frontends': True,
                      'startup_hold_gateway_and_dashboard': True,
                      'hold_survives_manager_recreation': True,
                      'absence_preserves_start_argv_env_and_config': True,
                      'network': 'disabled', 'production_access': False}))
