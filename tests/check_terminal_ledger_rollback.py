"""Synthetic-only old gateway reset/expiry and terminal-ledger downgrade probe.

Each phase runs in a separate real image, sharing only an isolated /fixture.
No platform adapter, transport, model, provider or background gateway is started.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import timedelta

ROOT = Path('/fixture')
HOME = ROOT/'home'
HOME.mkdir(exist_ok=True)
os.environ['HERMES_HOME'] = str(HOME)
DB = HOME/'state.db'
RESULT = ROOT/'results.json'
SEED = ROOT/'seed.json'
LEDGER = ('session_admissions','worker_executions','worker_receipts','session_turn_leases')

def record(key, value):
    data = json.loads(RESULT.read_text()) if RESULT.exists() else {}
    data[key] = value
    RESULT.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps({key:value}))

def ledger_hash():
    with sqlite3.connect(DB) as conn:
        data={t:sorted(conn.execute('SELECT * FROM '+t).fetchall()) for t in LEDGER}
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()

def old_store():
    from gateway.config import GatewayConfig, Platform, SessionResetPolicy
    from gateway.session import SessionStore, SessionSource
    cfg=GatewayConfig(default_reset_policy=SessionResetPolicy(mode='idle',idle_minutes=1))
    store=SessionStore(HOME/'sessions',cfg)
    assert store._db is not None
    sources={name:SessionSource(platform=Platform.TELEGRAM,chat_id='synthetic-'+name,user_id='synthetic-user')
             for name in ('manual','idle','finalized')}
    return store,sources

def seed():
    store,sources=old_store()
    ids={}
    for name,source in sources.items():
        entry=store.get_or_create_session(source)
        store.append_to_transcript(entry.session_id,{'role':'user','content':'Synthetic retained question'})
        store._db.update_system_prompt(entry.session_id,'Synthetic retained prompt')
        ids[name]=entry.session_id
    SEED.write_text(json.dumps(ids))
    store._db.close()
    record('old_seed',{'python':sys.version.split()[0],'sessions':3,'messages':3})

def migrate():
    from hermes_state import SessionDB
    store=SessionDB(DB); store.close()
    ids=json.loads(SEED.read_text())
    with sqlite3.connect(DB) as conn:
        conn.executescript((Path(__file__).parent/'rollback-current-prompts.sql').read_text())
        for name,sid in ids.items():
            conn.execute('''INSERT INTO session_admissions(admission_id,request_id,principal_id,
              target_session_id,lineage_json,payload_json,payload_digest,intent,status,outcome,owner_epoch,generation)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
              ('admission-'+name,'request-'+name,'synthetic-principal',sid,'{}','{}','synthetic-digest',
               'queue','terminal','synthetic-completed',1,1))
            conn.execute('''INSERT INTO worker_executions(execution_id,session_id,kind,owner_epoch,
              generation,status,adoption_digest,last_sequence) VALUES(?,?,?,?,?,?,?,?)''',
              ('execution-'+name,sid,'cron',1,1,'terminal','synthetic-adoption',1))
            conn.execute('''INSERT INTO worker_receipts(execution_id,sequence,payload_digest,result_json)
              VALUES(?,?,?,?)''',('execution-'+name,1,'synthetic-digest','{"ok":true}'))
    (ROOT/'ledger-digest.txt').write_text(ledger_hash())
    record('target_migrated_terminal_ledger',{'schema':32,'admissions':3,'executions':3,'receipts':3,'leases':0})

def exercise():
    store,sources=old_store()
    db=store._db
    ids=json.loads(SEED.read_text())
    resets={}
    for name,source in sources.items():
        entry=store.get_or_create_session(source)
        assert entry.session_id==ids[name]
        assert db.get_session(entry.session_id)['system_prompt']=='Synthetic retained prompt'
        transcript=store.load_transcript(entry.session_id)
        assert any(m.get('content')=='Synthetic retained question' for m in transcript)
        store.append_to_transcript(entry.session_id,{'role':'assistant','content':'Synthetic continuation'})
        if name=='manual':
            new=store.reset_session(entry.session_key)
        elif name=='idle':
            entry.updated_at-=timedelta(minutes=5)
            new=store.get_or_create_session(source)
            assert new.was_auto_reset and new.auto_reset_reason=='idle'
        else:
            store.set_expiry_finalized(entry)
            assert db.get_session(entry.session_id)['expiry_finalized']
            new=store.get_or_create_session(source)
        assert new.session_id!=ids[name]
        assert db.get_session(ids[name])['ended_at'] is not None
        assert db.get_session(new.session_id) is not None
        store.append_to_transcript(new.session_id,{'role':'user','content':'Synthetic after rotation'})
        assert len(store.load_transcript(ids[name]))==2
        resets[name]=True
    before=db._conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
    blocked=[]
    operations={
      'delete_session':lambda:db.delete_session(ids['manual']),
      'delete_sessions':lambda:db.delete_sessions([ids['manual'],ids['idle']]),
      'prune_sessions':lambda:db.prune_sessions(older_than_days=None,source='telegram'),
    }
    for name,operation in operations.items():
        try:
            operation()
        except sqlite3.IntegrityError as exc:
            assert 'gateway runtime history' in str(exc)
            blocked.append(name)
        else:
            raise AssertionError('Expected terminal-ledger delete guard')
        assert db._conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==before
        assert all(db.get_session(sid) is not None for sid in ids.values())
    assert ledger_hash()==(ROOT/'ledger-digest.txt').read_text()
    assert db._conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    db.close()
    record('old_runtime',{'python':sys.version.split()[0],'read_append_pass':True,
        'manual_reset_pass':resets['manual'],'idle_expiry_pass':resets['idle'],
        'expiry_finalization_pass':resets['finalized'],'new_session_append_pass':True,
        'explicit_deletion_blocked':blocked,'blocked_delete_transactions_preserve_messages':True,
        'all_terminal_ledger_rows_unchanged':True,'integrity':'ok'})

def reopen():
    from hermes_state import SessionDB
    store=SessionDB(DB)
    ids=json.loads(SEED.read_text())
    assert all(store.get_session(sid) is not None for sid in ids.values())
    assert ledger_hash()==(ROOT/'ledger-digest.txt').read_text()
    with sqlite3.connect(DB) as conn:
        assert conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==6
        assert conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==9
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    store.close()
    record('target_reopen',{'python':sys.version.split()[0],'sessions':6,'messages':9,
        'all_terminal_ledger_rows_unchanged':True,'integrity':'ok'})

{'seed':seed,'migrate':migrate,'exercise':exercise,'reopen':reopen}[sys.argv[1]]()
