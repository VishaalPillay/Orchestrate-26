import sys;sys.path.insert(0,'code/tools/recon')
import textidx as T,ev,csv,json,collections
R={r['case_id']:r for r in csv.DictReader(open('dataset/sample_deletion_requests.csv',encoding='utf-8'))}
S={r['case_id']:r for r in csv.DictReader(open('dataset/sample_output.csv',encoding='utf-8'))}
# which files contain account_service data / table coverage, by kind
cov=collections.defaultdict(set)   # file_id -> set of 'systems' covered
for o in ev.observations():
    f=o['f'];sysn=None
    rid=o['rid'] or ''
    if rid:cov[o['file_id']].add(rid.rsplit('_',1)[0])
# SQL: tables exist even if empty -> coverage by table schema
import re
tabcov=collections.defaultdict(set)
for fid,t in T.TXT.items():
    for m in re.finditer(r'CREATE TABLE "?(\w+)"?',t):tabcov[fid].add(m.group(1))
for c in sorted(S,key=lambda x:int(x.split('_')[1])):
    audit=R[c]['audit_at']
    for s in json.loads(S[c]['steps_json']):
        if s['operation']=='VERIFY_RECORD_ABSENT' and s['evidence_id'] and s['system_id']=='account_service' or (s['operation']=='VERIFY_RECORD_ABSENT' and s['evidence_id']):
            pre=s['resource_id'].rsplit('_',1)[0]
            files=[f for f in T.CAP if T.CAP[f]<=audit and (pre in cov[f] or any(('profile' in t and pre=='account') or (pre=='search' and 'search' in t) for t in tabcov[f]))]
            files.sort(key=lambda f:T.CAP[f],reverse=True)
            print(c,s['resource_id'],'audit',audit,'gold',s['evidence_id'],T.CAP[s['evidence_id']],'|top coverage files <=audit:',[(f,T.CAP[f][:10]) for f in files[:4]],'| gold mentions:',T.mentions(s['evidence_id'],s['resource_id']))
