import sys;sys.path.insert(0,'code/tools/recon')
import textidx as T,csv,json,collections
R={r['case_id']:r for r in csv.DictReader(open('dataset/sample_deletion_requests.csv',encoding='utf-8'))}
S={r['case_id']:r for r in csv.DictReader(open('dataset/sample_output.csv',encoding='utf-8'))}
hits=misses=0;rows=[];byop=collections.Counter()
for c in sorted(S,key=lambda x:int(x.split('_')[1])):
    audit=R[c]['audit_at']
    for s in json.loads(S[c]['steps_json']):
        if not s['evidence_id']:continue
        cand=[f for f in T.CAP if T.CAP[f]<=audit and T.mentions(f,s['resource_id'])]
        cand.sort(key=lambda f:(T.CAP[f],int(f.split('_')[1])))
        pred=cand[-1] if cand else None
        ok=pred==s['evidence_id']
        gold_mentions=T.mentions(s['evidence_id'],s['resource_id']);gold_leq=T.CAP[s['evidence_id']]<=audit
        byop[(s['operation'],ok)]+=1
        rows.append((c,s['step_no'],s['operation'],s['resource_id'],s['evidence_id'],pred,ok,gold_mentions,gold_leq,cand))
        if ok:hits+=1
        else:misses+=1
print("steps with evidence_id:",hits+misses,"hits",hits,"misses",misses)
for k,v in sorted(byop.items()):print(v,k)
print("MISSES:")
for r in rows:
    if not r[6]:print(r[0],'step',r[1],r[2],r[3],'gold',r[4],'pred',r[5],'gold_mentions_rid',r[7],'gold_captured<=audit',r[8],'candidates',r[9])
print("--- discriminating power")
multi=[r for r in rows if len(r[9])>=2];print("steps with >=2 candidate files <=audit:",len(multi),"of which hit:",sum(1 for r in multi if r[6]))
fut=0;ties=0
for c in sorted(S,key=lambda x:int(x.split('_')[1])):
    audit=R[c]['audit_at']
    for s in json.loads(S[c]['steps_json']):
        if not s['evidence_id']:continue
        later=[f for f in T.CAP if T.CAP[f]>audit and T.mentions(f,s['resource_id'])]
        if later:fut+=1
        cand=[f for f in T.CAP if T.CAP[f]<=audit and T.mentions(f,s['resource_id'])]
        if cand:
            mx=max(T.CAP[f] for f in cand)
            if sum(1 for f in cand if T.CAP[f]==mx)>1:ties+=1
print("steps where a later-than-audit file also mentions the resource (decoy for time rule):",fut,"| ties at max captured_at:",ties)
ex=[r for r in multi if r[6]][:3]
for r in ex:print("  e.g.",r[0],r[3],"gold",r[4],"candidates",r[9])
