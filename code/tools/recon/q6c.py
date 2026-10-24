import sys;sys.path.insert(0,'code/tools/recon')
import textidx as T,ev,csv,json,collections,sqlite3,re,glob
R={r['case_id']:r for r in csv.DictReader(open('dataset/sample_deletion_requests.csv',encoding='utf-8'))}
# scope rows for inventory snapshots
for f in sorted(glob.glob('dataset/evidence/databases/customer_data_inventory_*.sql')):
    con=sqlite3.connect(':memory:');con.executescript(open(f,encoding='utf-8').read())
    print(f.split('/')[-1],con.execute('select * from snapshot_scope order by 1').fetchall())
# account-table coverage: files with any account_service data
acc=collections.defaultdict(lambda:collections.Counter())
for o in ev.observations():
    f=o['f']
    if o['fmt']=='sql' and (f.get('_table','').startswith('customer_profiles') or (f.get('_origin')=='account_service')):acc[o['file_id']]['sql_acct_rows']+=1
    if o['fmt']=='json' and f.get('_store')=='account_service':acc[o['file_id']]['json_acct']+=1
# empty SQL account tables (schema only)
for fid,t in T.TXT.items():
    if re.search(r'CREATE TABLE "?customer_profiles',t) and fid not in acc:acc[fid]['sql_acct_table_empty']+=1
for c in ['case_01','case_02','case_04','case_31','case_21','case_03']:
    a=R[c]['audit_at']
    ok=sorted([f for f in acc if T.CAP[f]<=a],key=lambda f:T.CAP[f],reverse=True)
    print(c,a,[(f,T.CAP[f][:16],dict(acc[f])) for f in ok[:4]])
