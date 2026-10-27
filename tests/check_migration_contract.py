"""Run only against synthetic SQLite in an isolated container, without credentials.

This is a schema-22 fixture, not a production-data compatibility assertion.
SQL trace includes only the synthetic fixture. It is a review artifact, not a
migration script to apply manually to a live database.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

fixture = Path(__file__).parent / 'fixtures/v0.19-schema.sql'
out = Path(os.environ.get('MIGRATION_EVIDENCE_DIR', '/tmp/migration-evidence'))
out.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='schema22-fixture-') as temporary:
    root = Path(temporary)
    os.environ['HERMES_HOME'] = str(root / 'isolated-home')
    dbfile = root / 'fixture.db'
    with sqlite3.connect(dbfile) as db:
        db.executescript(fixture.read_text())
        db.execute('INSERT INTO schema_version(version) VALUES (22)')
        for sid in ['synthetic-a', 'synthetic-b']:
            db.execute('INSERT INTO sessions(id, source, started_at, system_prompt) VALUES (?, ?, ?, ?)',
                       (sid, 'telegram', 1793000000, 'Synthetic retained prompt.'))
            db.execute('INSERT INTO messages(session_id, role, content, timestamp) VALUES (?, ?, ?, ?)',
                       (sid, 'user', 'Synthetic support question.', 1793000001))
        before = {name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                  for name in ['sessions', 'messages']}
    statements = []
    real_connect = sqlite3.connect
    def traced_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    sqlite3.connect = traced_connect
    from hermes_state import SessionDB
    store = SessionDB(dbfile)
    new_prompt = store.get_session('synthetic-a')['system_prompt']
    store.close()
    sqlite3.connect = real_connect
    with real_connect(dbfile) as db:
        after = {name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                 for name in ['sessions', 'messages']}
        version = db.execute('SELECT version FROM schema_version').fetchone()[0]
        # v0.19 get_session is SELECT *; it cannot resolve the new prompt hash.
        old_prompt = db.execute('SELECT system_prompt FROM sessions WHERE id=?', ('synthetic-a',)).fetchone()[0]
        prompt_count = db.execute('SELECT COUNT(*) FROM system_prompts').fetchone()[0]
        unknown_uids = db.execute('SELECT COUNT(*) FROM messages WHERE message_uid IS NULL').fetchone()[0]
        integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
    assert before == after
    assert new_prompt == 'Synthetic retained prompt.'
    assert old_prompt is None, 'Rollback assumption changed; inspect old reader'
    assert prompt_count == 1 and unknown_uids == 0 and version == 32 and integrity == 'ok'
    rollback_sql = (Path(__file__).parent / 'rollback-current-prompts.sql').read_text()
    with real_connect(dbfile) as db:
        missing = db.execute("SELECT COUNT(*) FROM sessions s WHERE s.system_prompt_hash IS NOT NULL AND NOT EXISTS (SELECT 1 FROM system_prompts p WHERE p.hash=s.system_prompt_hash)").fetchone()[0]
        assert missing == 0, 'Missing current prompt references forbid rollback'
        db.executescript(rollback_sql)
    # Run the actual old SessionDB implementation in another interpreter so
    # old/new module caches cannot mix. The fixture path and source are explicit.
    legacy_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, '/checks/legacy-source')
from hermes_state import SessionDB, SCHEMA_VERSION
assert SCHEMA_VERSION == 22
store = SessionDB(Path(sys.argv[1]))
assert store.get_session('synthetic-a')['system_prompt'] == 'Synthetic retained prompt.'
store.append_message('synthetic-a', 'assistant', 'Synthetic old-runtime continuation.')
store.update_system_prompt('synthetic-a', 'Synthetic prompt updated by old runtime.')
store.create_session('synthetic-after-rollback', 'telegram', system_prompt='Synthetic new session.')
store.append_message('synthetic-after-rollback', 'user', 'Synthetic new question.')
store.close()
print('old_reader_append_pass')
"""
    result = subprocess.run([sys.executable, '-c', legacy_code, str(dbfile)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Legacy fixture failed: '+result.stderr)
    store = SessionDB(dbfile)
    assert store.get_session('synthetic-a')['system_prompt'] == 'Synthetic prompt updated by old runtime.'
    assert store.get_session('synthetic-after-rollback')['system_prompt'] == 'Synthetic new session.'
    store.close()
    with real_connect(dbfile) as db:
        final_counts = {name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in ['sessions','messages']}
        assert final_counts == {'sessions': 3, 'messages': 4}, final_counts
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    summary = {'current_row_rollback_old_read_append_new_reopen': True,
               'post_rollback_counts': final_counts,
               'old_runtime_test_interpreter': 'candidate Python 3.14; actual old SessionDB source',
               'before_counts':  before, 'after_counts': after, 'schema_version': version,
               'target_resolves_prompt': True, 'old_reader_loses_prompt_snapshot': True,
               'deduplicated_prompt_count': prompt_count, 'message_uids_backfilled': True,
               'integrity_check': integrity, 'production_access': False,
               'coverage': 'Synthetic schema-22 migration with inline FTS, two sessions and two messages'}
    (out/'migration-result.json').write_text(json.dumps(summary, indent=2)+'\n')
    unique = list(dict.fromkeys(statements))
    (out/'migration-statements.sql').write_text('-- Exact SQL observed against a synthetic fixture only.\n\n'+';\n\n'.join(unique)+';\n')
    print(json.dumps(summary))
