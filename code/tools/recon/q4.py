import csv,collections,re
A=list(csv.DictReader(open('dataset/system_activity.csv',encoding='utf-8')))
for et in ['CHECK_DATA_FLOW','DISABLE_DATA_FLOW','CHECK_RESTORE_ACCESS','BLOCK_RESTORE','RESTORE_BACKUP','SYNC_RECORD','LINK_RESOURCE_IDENTITY']:
    rows=[a for a in A if a['event_type']==et]
    c=collections.Counter((a['reported_result'],re.sub(r'(data_flow|backup|account|search|analytics|support|export)_\d+',r'\1_N',re.sub(r'user_\d+','user_N',re.sub(r'[\w.+-]+@[\w.-]+','EMAIL',a['details'])))) for a in rows)
    print("###",et,len(rows))
    for (res,d),n in c.most_common(8):print("  ",n,res,"|",d[:220])
    print("   e.g.",rows[0])
