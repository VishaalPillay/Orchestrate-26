import sys;sys.path.insert(0,'code/tools/recon')
import ev,ids,csv,json,collections
R={r['case_id']:r for r in csv.DictReader(open('dataset/sample_deletion_requests.csv',encoding='utf-8'))}
S={r['case_id']:r for r in csv.DictReader(open('dataset/sample_output.csv',encoding='utf-8'))}
A=csv.DictReader(open('dataset/system_activity.csv',encoding='utf-8'));act=collections.defaultdict(list)
for a in A:act[a['resource_id']].append(a)
obs=list(ev.observations());byrid=collections.defaultdict(list)
for o in obs:
    if o['rid']:byrid[o['rid']].append(o)
miss=collections.Counter()
for c in sorted(S,key=lambda x:int(x.split('_')[1])):
    r=R[c];st=json.loads(S[c]['steps_json']);gold=sorted({s['resource_id'] for s in st})
    uids=ids.resolve(r['email']);I=ids.idset(uids[0])
    out=[]
    for rid in gold:
        ms=[o for o in byrid[rid] if o['ts']<=r['audit_at']]
        linked=any(ids.matches(o['f'],I) for o in ms)
        out.append((rid,len(ms),'LINKED' if linked else 'unlinked',sorted({a['event_type'] for a in act[rid]})))
        if not linked:miss[rid.rsplit('_',1)[0]]+=1
    if c in('case_11','case_18','case_22','case_29','case_26','case_12'):
        print(c,S[c]['deletion_status']);[print("    ",o) for o in out]
print("unlinked-by-identifier gold resources by type:",miss)
