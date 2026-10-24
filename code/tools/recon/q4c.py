import sys;sys.path.insert(0,'code/tools/recon')
import ev,csv,collections,json
A=list(csv.DictReader(open('dataset/system_activity.csv',encoding='utf-8')))
obs=list(ev.observations())
def state(o):
    f=o['f'];rid=o['rid'] or ''
    if rid.startswith('data_flow'):
        v=f.get('enabled',None)
        if v is None:
            r=f.get('result')
            return {'ACTIVE':True,'DISABLED':False}.get(r)
        return str(v).lower() in('true','1')
    if rid.startswith('backup'):
        v=f.get('restore_allowed')
        if v is None:
            return {'ACTIVE':True,'BLOCKED':False}.get(f.get('result'))
        return str(v).lower() in('true','1')
by=collections.defaultdict(list)
for o in obs:
    if o['rid'] and (o['rid'].startswith('data_flow') or o['rid'].startswith('backup')) and state(o) is not None: by[o['rid']].append(o)
for et in ['CHECK_DATA_FLOW','DISABLE_DATA_FLOW','CHECK_RESTORE_ACCESS','BLOCK_RESTORE']:
    c=collections.Counter();ex=None
    for a in A:
        if a['event_type']!=et:continue
        obs_r=sorted(by.get(a['resource_id'],[]),key=lambda o:o['ts'])
        before=[o for o in obs_r if o['ts']<=a['event_time']];after=[o for o in obs_r if o['ts']>a['event_time']]
        key=('no-evidence' if not obs_r else '',
             'latest_before='+str(state(before[-1])) if before else 'nobefore',
             'earliest_after='+str(state(after[0])) if after else 'noafter')
        c[key]+=1
    print(et);[print("  ",v,k) for k,v in c.most_common()]
# concrete examples
shown=set()
for o in obs:
    f=o['f'];rid=o['rid'] or ''
    if rid[:4] in('data','back') and state(o) is not None:
        k=(o['fmt'],rid[:4],state(o))
        if k not in shown:
            shown.add(k);print(o['file_id'],o['fmt'],o['ts'],json.dumps({a:b for a,b in f.items() if a in('resource_id','enabled','mode','connector_name','destination','subject_selector','last_delivery_status','restore_allowed','archive_key','subject_reference','customer_reference','customer_email','result','record_type','event','archive_present','_table','_topo','system')})[:330])
