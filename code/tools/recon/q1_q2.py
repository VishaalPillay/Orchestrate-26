import csv,json,hashlib,re,collections
S=list(csv.DictReader(open('dataset/sample_output.csv',encoding='utf-8')))
print("sample rows",len(S),collections.Counter(r['deletion_status'] for r in S))
# Q1
tot=0;viol_str=0;viol_num=0;ex=[]
def num(s):
    m=re.search(r'(\d+)$',s);return (s[:m.start()] if m else s,int(m.group(1)) if m else -1)
for r in S:
    st=json.loads(r['steps_json'])
    # check phase order and step_no
    for i,s in enumerate(st):
        assert s['step_no']==i+1
    # group consecutive runs by (phase,operation)
    runs=[];
    for s in st:
        k=(s['phase'],s['operation'])
        if runs and runs[-1][0]==k: runs[-1][1].append(s)
        else: runs.append((k,[s]))
    ks=[k for k,_ in runs]
    for k,ss in runs:
        keys_s=[(x['system_id'],x['resource_id']) for x in ss]
        keys_n=[(x['system_id'],)+num(x['resource_id']) for x in ss]
        tot+=1
        if keys_s!=sorted(keys_s): viol_str+=1; ex.append((r['case_id'],'string-unsorted',k,keys_s))
        if keys_n!=sorted(keys_n): viol_num+=1; ex.append((r['case_id'],'numeric-unsorted',k,keys_s))
    # does same (phase,op) appear in >1 run?
    c=collections.Counter(ks)
    if any(v>1 for v in c.values()): print("non-contiguous op runs",r['case_id'],[k for k,v in c.items() if v>1])
print("runs",tot,"violations string",viol_str,"numeric",viol_num)
for e in ex[:10]:print(e)
# show runs where numeric vs string order differ in a way that is informative: any run with resource ids of differing digit lengths
for r in S:
    st=json.loads(r['steps_json'])
    runs=collections.defaultdict(list)
    for s in st: runs[(s['phase'],s['operation'],s['system_id'])].append(s['resource_id'])
    for k,v in runs.items():
        if len(v)>1 and len({len(x) for x in v})>1: print("mixed-length ids",r['case_id'],k,v)
# Q2
U=list(csv.DictReader(open('dataset/user_identities.csv',encoding='utf-8')))
bad=[];ex_variants=collections.Counter()
for u in U:
    h=hashlib.sha256(u['email'].strip().lower().encode()).hexdigest()
    if h!=u['email_hash']:
        bad.append(u)
        for name,f in [('raw',lambda e:e),('strip',lambda e:e.strip()),('lower',lambda e:e.lower())]:
            if hashlib.sha256(f(u['email']).encode()).hexdigest()==u['email_hash']: ex_variants[name]+=1
print("users",len(U),"mismatch",len(bad),ex_variants)
for b in bad[:5]:print(b)
print("emails not lowercase/trimmed:",sum(1 for u in U if u['email']!=u['email'].strip().lower()))
print("dup emails",len(U)-len({u['email'].lower() for u in U}),"dup user_id",len(U)-len({u['user_id'] for u in U}))
