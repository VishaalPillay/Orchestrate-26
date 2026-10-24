import json,glob,collections
D='dataset/evidence/records/'
for f in ['connector_configurations_20260812.json','customer_export_manifest_20260409.json','security_audit_ledger_20261001.json']:
    d=json.load(open(D+f,encoding='utf-8'))
    c=collections.Counter((r['system'],r['record_type'],r['result']) for r in d['records'])
    print(f,d['record_count'],len(d['records']))
    for k,v in sorted(c.items()):print("  ",v,k)
    print("   keys",sorted({k for r in d['records'] for k in r}))
    print("   subject_reference samples",[r['subject_reference'] for r in d['records']][:3], [r['subject_reference'] for r in d['records'] if r['system']!='account_service'][:6])
sh=collections.defaultdict(lambda: collections.defaultdict(set))
for f in sorted(glob.glob(D+'service_resource_export_*.json')):
    d=json.load(open(f,encoding='utf-8'))
    for svc,v in d['service_exports'].items():
        for coll,recs in v.items():
            for r in recs: sh[svc][coll]|=set(r.keys())
for s,v in sh.items():
    for c,k in v.items():print(s,c,sorted(k))
print(json.dumps(json.load(open(D+'service_resource_export_20260915_114455.json'))['service_exports']['backup_service' if 'backup_service' in json.load(open(D+'service_resource_export_20260915_114455.json'))['service_exports'] else 'integration_service'],indent=1)[:1500])
