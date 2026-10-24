import json,glob,collections
D='dataset/evidence/records/'
def show(f,n=1400):
    print("=====",f);print(open(D+f,encoding='utf-8').read()[:n])
# bundle: data_store/collection combos across all
c=collections.Counter();keys=collections.defaultdict(set)
for f in sorted(glob.glob(D+'customer_data_snapshot_bundle_*.json')):
    d=json.load(open(f,encoding='utf-8'))
    for s in d['snapshots']:
        c[(s['data_store'],s['collection'])]+=1
        for r in s['records']:keys[(s['data_store'],s['collection'])]|=set(r.keys())
for k,v in sorted(c.items()):print(v,k,sorted(keys[k]))
for f in ['connector_configurations_20260812.json','customer_analytics_report_20260907.json','customer_export_jobs_20260914.json','customer_export_manifest_20260409.json','security_audit_ledger_20261001.json','service_resource_export_20260915_114455.json']:
    show(f,1500)
