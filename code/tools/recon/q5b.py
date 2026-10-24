import sys;sys.path.insert(0,'code/tools/recon')
import ev,ids,csv,json,collections
R={r['case_id']:r for r in csv.DictReader(open('dataset/sample_deletion_requests.csv',encoding='utf-8'))}
obs=list(ev.observations())
A=list(csv.DictReader(open('dataset/system_activity.csv',encoding='utf-8')))
for c in sys.argv[1:]:
    r=R[c];uids=ids.resolve(r['email']);print("#####",c,r['email'],uids,"audit",r['audit_at'])
    for uid in uids:
        I=ids.idset(uid);print("  emails",I['emails'],"phones",I['phones'])
        rows=collections.defaultdict(list)
        for o in obs:
            if o['rid'] and ids.matches(o['f'],I):rows[o['rid']].append(o)
        for rid,os_ in sorted(rows.items()):
            os_.sort(key=lambda o:o['ts'])
            last=[o for o in os_ if o['ts']<=r['audit_at']][-1:]
            fut=len([o for o in os_ if o['ts']>r['audit_at']])
            print("   ",rid,"n_obs",len(os_),"future",fut,"latest<=audit:",(last[0]['file_id'],last[0]['ts'],{k:v for k,v in last[0]['f'].items() if k in('result','record_type','enabled','restore_allowed','email','requester_email','owner_email','customer_email','status_code','message_text','document','user_id','subject_reference','customer_reference','event','_table')} ) if last else None)
