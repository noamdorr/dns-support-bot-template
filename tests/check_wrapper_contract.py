"""Offline wrapper/config checks with synthetic data and no service startup."""
import asyncio
import json
import os
from pathlib import Path
import runpy
import tempfile

with tempfile.TemporaryDirectory(prefix='wrapper-fixture-') as temporary:
    os.environ['HERMES_HOME'] = temporary
    os.environ['ADMIN_PASSWORD'] = 'synthetic-offline-test-only'
    os.environ['ADMIN_USERNAME'] = 'synthetic-test'
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
    module['write_config_yaml']({'LLM_MODEL': 'synthetic-model'})
    actual = yaml.safe_load(file.read_text())
    for key, value in config.items():
        assert actual[key] == value, key
    assert actual['data_dir'] == temporary
    assert Path('/opt/hermes-agent').resolve() == Path('/opt/hermes')
    assert Path('/opt/hermes/hermes_cli/web_dist/index.html').exists()
    assert Path('/opt/hermes/ui-tui/dist/entry.js').exists()
    print(json.dumps({'config_preserved': True, 'wrapper_imports': True,
                      'legacy_code_path_alias': True, 'compiled_frontends': True,
                      'network': 'disabled', 'production_access': False}))
