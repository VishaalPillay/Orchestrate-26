import csv,json,sys
S={r['case_id']:r for r in csv.DictReader(open('dataset/sample_output.csv',encoding='utf-8'))}
R={r['case_id']:r for r in csv.DictReader(open('dataset/sample_deletion_requests.csv',encoding='utf-8'))}
for c in sys.argv[1:]:
    r=R[c];print("#####",c,S[c]['deletion_status'],r['email'],"req",r['requested_at'],"rep",r['reported_status'],r['reported_at'],"AUDIT",r['audit_at']);print("   ",r['request_text'][:160])
    for s in json.loads(S[c]['steps_json']):print("  ",s['step_no'],s['phase'][:4],s['system_id'],s['resource_id'],s['operation'],s['state'],s['evidence_id'])
