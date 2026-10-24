import sys;sys.path.insert(0,'code/tools/recon')
import textidx as T,csv,re,collections
cats=[r['data_category'] for r in csv.DictReader(open('dataset/deletion_rules.csv'))]
act=open('dataset/system_activity.csv',encoding='utf-8').read()
print("literal category token occurrences")
for c in cats:
    files=[f for f,t in T.TXT.items() if c in t]
    print(f"  {c:20s} evidence files:{len(files):3d} {files[:6]}  activity.csv hits:{act.count(c)}")
print("resource-id prefixes across evidence text / activity")
pre=collections.defaultdict(set)
for f,t in T.TXT.items():
    for m in re.finditer(r'(?<![A-Za-z0-9_])([a-z]+(?:_[a-z]+)*)_(\d+)(?![A-Za-z0-9])',t):pre[m.group(1)].add(f)
apre=collections.Counter(re.match(r'(.*)_\d+$',r['resource_id']).group(1) for r in csv.DictReader(open('dataset/system_activity.csv',encoding='utf-8')) if re.match(r'(.*)_\d+$',r['resource_id']))
for p in sorted(set(pre)|set(apre)):print(f"  {p:18s} in {len(pre[p]):2d} evidence files; activity rows {apre[p]}")
