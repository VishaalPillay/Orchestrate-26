import sys;sys.path.insert(0,'code/tools/recon')
import ev,ids,csv,json,collections
R={r['case_id']:r for r in csv.DictReader(open('dataset/sample_deletion_requests.csv',encoding='utf-8'))}
S={r['case_id']:r for r in csv.DictReader(open('dataset/sample_output.csv',encoding='utf-8'))}
obs=list(ev.observations())
def brief(o):
    f=o['f']
    k={x:f.get(x) for x in('result','record_type','enabled','restore_allowed','status_code','event','_table','account_status','retained_until','retention_until','archive_present') if f.get(x) is not None}
    return k
for c in sorted(S,key=lambda x:int(x.split('_')[1])):
    r=R[c];st=json.loads(S[c]['steps_json']);gold={s['resource_id'] for s in st}
    uids=ids.resolve(r['email']);extra={}
    for uid in uids:
        I=ids.idset(uid);rows=collections.defaultdict(list)
        for o in obs:
            if o['rid'] and o['ts']<=r['audit_at'] and ids.matches(o['f'],I):rows[o['rid']].append(o)
        for rid,os_ in rows.items():
            if rid not in gold:
                os_.sort(key=lambda o:o['ts']);extra[rid]=(os_[-1]['file_id'],brief(os_[-1]))
    print(c,S[c]['deletion_status'],uids,"gold:",sorted(gold))
    for rid,v in sorted(extra.items()):print("     EXTRA matched-not-in-gold",rid,v)
