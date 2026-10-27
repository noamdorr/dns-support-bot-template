"""Offline copied-state compatibility fixture. Never point at a live database.

Run each phase in a separate image with /fixture mounted to private scratch.
No row contents, identifiers, prompt hashes, or SQL parameters are printed.
Only synthetic statement tracing belongs in check_migration_contract.py.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

os.environ['HERMES_HOME'] = '/tmp/isolated-rollback-home'
ROOT = Path('/fixture')
DB = ROOT / 'copy.db'
META = ROOT / 'private-contract.json'
RESULT = ROOT / 'safe-results.json'
SYNTH_TARGET = 'compatibility-fixture-target-created'
SYNTH_OLD = 'compatibility-fixture-old-created'

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                     default=lambda b: {'bytes': b.hex()}).encode()).hexdigest()

def quote(name):
    return '"' + name.replace('"', '""') + '"'

def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db

def shape(db):
    return {r['name']: {'type': r['type'], 'sql': r['sql']} for r in db.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}

def counts(db):
    return {t: db.execute(f'SELECT COUNT(*) FROM {quote(t)}').fetchone()[0]
            for t in ('sessions', 'messages')}

def result(key, value):
    data = json.loads(RESULT.read_text()) if RESULT.exists() else {}
    data[key] = value
    RESULT.write_text(json.dumps(data, indent=2)+'\n')
    print(json.dumps({key: value}))

def baseline():
    with connect() as db:
        assert db.execute('SELECT version FROM schema_version').fetchone()[0] == 22
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        tables = {}
        for name, info in shape(db).items():
            if info['type'] != 'table' or name.startswith('messages_fts'):
                continue
            columns = [r['name'] for r in db.execute(f'PRAGMA table_info({quote(name)})')]
            rows = [dict(r) for r in db.execute(f'SELECT * FROM {quote(name)}')]
            tables[name] = {'columns': columns, 'rows': rows}
        contract = {'tables': tables, 'schema': shape(db), 'counts': counts(db)}
        META.write_text(json.dumps(contract, ensure_ascii=True))
        os.chmod(META, 0o600)
        result('baseline', {'schema_version': 22, 'counts': counts(db), 'integrity': 'ok'})

def original_rows_preserved(db, prompt_resolver=False):
    contract = json.loads(META.read_text())
    for table in ('sessions', 'messages'):
        rows = contract['tables'][table]['rows']
        for expected in rows:
            actual = dict(db.execute(f'SELECT * FROM {table} WHERE id=?', (expected['id'],)).fetchone())
            if prompt_resolver and table == 'sessions' and actual.get('system_prompt_hash'):
                actual['system_prompt'] = db.execute('SELECT prompt FROM system_prompts WHERE hash=?',
                    (actual['system_prompt_hash'],)).fetchone()[0]
            # All original column values must remain identical, including messages,
            # origin metadata, usage, status and every original system prompt.
            assert digest({c: actual[c] for c in expected}) == digest(expected), 'Original row changed'

def migrate():
    contract = json.loads(META.read_text())
    from hermes_state import SessionDB
    store = SessionDB(DB)
    store.close()
    with connect() as db:
        assert counts(db) == contract['counts']
        original_rows_preserved(db, prompt_resolver=True)
        old, new = contract['schema'], shape(db)
        added = sorted(set(new)-set(old))
        changed = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
        removed = sorted(set(old)-set(new))
        added_columns = {}
        changed_columns = {}
        unchanged_tables = []
        for table, saved in contract['tables'].items():
            cols = [r['name'] for r in db.execute(f'PRAGMA table_info({quote(table)})')]
            added_columns[table] = sorted(set(cols)-set(saved['columns']))
            after = [dict(r) for r in db.execute(f'SELECT * FROM {quote(table)}')]
            if 'id' in saved['columns']:
                index = {r['id']: r for r in after}
                changes = {c: sum(r[c] != index[r['id']][c] for r in saved['rows'])
                           for c in saved['columns']}
                changed_columns[table] = {c:n for c,n in changes.items() if n}
            if digest(sorted([digest(r) for r in saved['rows']])) == digest(sorted(
                [digest({c:r[c] for c in saved['columns']}) for r in after])):
                unchanged_tables.append(table)
        safe = {'schema_version': db.execute('SELECT version FROM schema_version').fetchone()[0],
            'counts': counts(db), 'integrity': db.execute('PRAGMA integrity_check').fetchone()[0],
            'original_sessions_messages_all_original_fields_preserved': True,
            'added_schema_objects': added, 'changed_schema_objects': changed, 'removed_schema_objects': removed,
            'added_columns': {t:c for t,c in added_columns.items() if c},
            'changed_original_columns': {t:c for t,c in changed_columns.items() if c},
            'original_table_rows_unchanged': unchanged_tables,
            'null_message_uids': db.execute('SELECT COUNT(*) FROM messages WHERE message_uid IS NULL').fetchone()[0],
            'prompt_references': db.execute('SELECT COUNT(*) FROM sessions WHERE system_prompt_hash IS NOT NULL').fetchone()[0]}
        assert safe['schema_version'] == 32 and safe['integrity'] == 'ok'
        result('migration', safe)
    store = SessionDB(DB)
    assert store.get_session(SYNTH_TARGET) is None
    store.create_session(SYNTH_TARGET, 'telegram', system_prompt='Synthetic target prompt')
    store.append_message(SYNTH_TARGET, 'user', 'Synthetic target question')
    store.close()

def restore():
    with connect() as db:
        assert db.execute('SELECT COUNT(*) FROM sessions s WHERE s.system_prompt_hash IS NOT NULL AND NOT EXISTS '
                          '(SELECT 1 FROM system_prompts p WHERE p.hash=s.system_prompt_hash)').fetchone()[0] == 0
        before = counts(db)
        db.executescript((Path(__file__).parent/'rollback-current-prompts.sql').read_text())
        assert counts(db) == before
        original_rows_preserved(db)
        assert db.execute('SELECT COUNT(*) FROM sessions WHERE system_prompt_hash IS NOT NULL').fetchone()[0] == 0
        result('current_row_restore', {'all_original_fields_preserved': True, 'counts': counts(db),
                                     'new_target_rows_preserved': True, 'all_prompt_refs_expanded': True})

def legacy():
    from hermes_state import SessionDB, SCHEMA_VERSION
    assert SCHEMA_VERSION == 22
    store = SessionDB(DB)
    contract = json.loads(META.read_text())
    for expected in contract['tables']['sessions']['rows']:
        assert digest(store.get_session(expected['id'])['system_prompt']) == digest(expected['system_prompt'])
    assert store.get_session(SYNTH_TARGET)['system_prompt'] == 'Synthetic target prompt'
    store.append_message(SYNTH_TARGET, 'assistant', 'Synthetic old continuation')
    store.update_system_prompt(SYNTH_TARGET, 'Synthetic updated old prompt')
    store.create_session(SYNTH_OLD, 'telegram', system_prompt='Synthetic new old session')
    store.append_message(SYNTH_OLD, 'user', 'Synthetic old question')
    store.close()
    with connect() as db:
        original_rows_preserved(db)
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        result('legacy_runtime', {'python': sys.version.split()[0], 'schema_implementation': SCHEMA_VERSION,
              'all_original_prompt_reads_pass': True, 'append_prompt_update_new_session_pass': True,
              'all_original_fields_preserved': True, 'counts': counts(db), 'integrity': 'ok'})

def reopen():
    from hermes_state import SessionDB
    store = SessionDB(DB)
    assert store.get_session(SYNTH_TARGET)['system_prompt'] == 'Synthetic updated old prompt'
    assert store.get_session(SYNTH_OLD)['system_prompt'] == 'Synthetic new old session'
    store.close()
    with connect() as db:
        original_rows_preserved(db, prompt_resolver=True)
        original = json.loads(META.read_text())['counts']
        assert counts(db) == {'sessions': original['sessions']+2, 'messages': original['messages']+3}
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        result('target_reopen', {'python': sys.version.split()[0], 'all_original_fields_preserved': True,
               'old_updated_prompt_visible': True, 'counts': counts(db), 'integrity': 'ok'})

assert DB.is_file(), 'Fixture DB must be prepared in isolated scratch first'
{'baseline': baseline, 'migrate': migrate, 'restore': restore, 'legacy': legacy, 'reopen': reopen}[sys.argv[1]]()
