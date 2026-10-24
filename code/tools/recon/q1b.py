import csv,json,re,collections
S=list(csv.DictReader(open('dataset/sample_output.csv',encoding='utf-8')))
multi=0;ids=collections.defaultdict(set);opsorder=collections.Counter()
for r in S:
    st=json.loads(r['steps_json'])
    runs=collections.defaultdict(list)
    for s in st:
        runs[(s['phase'],s['operation'])].append((s['system_id'],s['resource_id']))
        ids[s['system_id']].add(s['resource_id'])
    for k,v in runs.items():
        if len(v)>1: multi+=1
    opsorder[tuple(dict.fromkeys((s['phase'],s['operation']) for s in st))]+=1
print("(phase,op) groups with >=2 steps:",multi)
for k,v in sorted(ids.items()):print(k,sorted(v)[:8],"lens",sorted({len(x) for x in v}))
A=list(csv.DictReader(open('dataset/system_activity.csv',encoding='utf-8')))
pat=collections.defaultdict(collections.Counter)
for a in A:
    m=re.match(r'([a-z_]+?)_?(\d+)$',a['resource_id'])
    pat[m.group(1) if m else a['resource_id']][len(m.group(2)) if m else 0]+=1
print({k:dict(v) for k,v in pat.items()})
print("distinct phase/op orderings in samples:")
for k,v in opsorder.most_common(12):print(v,[o for p,o in k])
# are there intra-system ordering counterexamples across the system dimension (system order alphabetical?)
for r in S:
    st=json.loads(r['steps_json'])
    ph=[(s['phase'],s['operation'],s['system_id'],s['resource_id']) for s in st]
    # find (phase,op) with >1 distinct systems
    g=collections.defaultdict(list)
    for p,o,sy,re_ in ph:g[(p,o)].append((sy,re_))
    for k,v in g.items():
        if len({x[0] for x in v})>1: print(r['case_id'],k,v);break
