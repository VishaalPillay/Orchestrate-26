import sys;sys.path.insert(0,'code/tools/recon')
import ev,collections
c=collections.Counter()
for o in ev.observations():
    f=o['f'];rid=o['rid'] or ''
    if rid.startswith('data_flow'):
        st=f.get('enabled',f.get('result'))
        c[('data_flow',o['fmt'],ev.M and o['file_id'] and 'enabled/result='+str(st)+' status='+str(f.get('last_delivery_status')) )]+=1
    if rid.startswith('backup'):
        c[('backup',o['fmt'],'restore_allowed/result='+str(f.get('restore_allowed',f.get('result'))))]+=1
for k,v in sorted(c.items(),key=str):print(v,k)
