"""Run the existing synthetic suites and DNS-117 process checks, no live calls."""
from pathlib import Path
import json,os,re,subprocess,sys
root=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).parents[2]/'brain'
checks={'scripts':['test_us_issuer_reply.py','test_draft_replies.py','test_timeout_recovery.py','test_ticket_fields.py','test_draft_work_queue.py','test_september_feedback.py','test_diagnostic_preview.py','test_failure_diagnostics.py','test_draft_parser.py','test_commit_draft.py','test_pending_status.py','test_category_requirements.py','test_daily_support.py','test_drain_queue.py','test_case_feedback.py','test_check_draft_prompt.py','test_support_drafting.py','test_saved_reply.py'], 'plugins/wiki':['test_support_answer.py','test_wiki.py'], 'plugins/triage':['test_pilot_case_logging.py','test_case_logging.py','test_triage.py','test_draft_requests.py','test_reply_feedback.py','test_ticket_offers.py','test_ticket_information.py','test_manual_followup.py','test_failure_context.py'], 'plugins/jira':['test_jira.py','test_sandbox.py','test_sandbox_edit.py']}
results=[]
for folder,tests in checks.items():
 for test in tests:
  p=subprocess.run([sys.executable,test],cwd=root/folder,capture_output=True,text=True,timeout=60,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
  text=p.stdout+p.stderr
  match=re.search(r'Ran (\d+) tests?',text)
  count=int(match[1]) if match else len(re.findall(r'^ok test_',text,re.M))
  results.append({'suite':folder+'/'+test,'exit':p.returncode,'tests':count,'output':text})
  print(folder+'/'+test,p.returncode,count,flush=True)
Path(__file__).with_name('checks.json').write_text(json.dumps({'checks':results,'tests':sum(r['tests'] for r in results),'failed':[r['suite'] for r in results if r['exit']]},indent=2))
assert all(r['exit']==0 for r in results),'Failing suites recorded in checks.json'
