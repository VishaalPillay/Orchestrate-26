"""Shared recon helper: iterate normalized observations from every evidence file.
yield dict(file_id, fmt, kind, resource_id, system, ts, fields(dict))"""
import csv,json,re,sqlite3,glob,os
ROOT='dataset/'
M={r['file_path']:r for r in csv.DictReader(open(ROOT+'evidence_files.csv',encoding='utf-8'))}
def kv(s):
    d={}
    for m in re.finditer(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)',s):
        v=m.group(2)
        if v.startswith('"'):v=v[1:-1].replace('\\"','"')
        d[m.group(1)]=v
    return d
def observations():
    for p,m in sorted(M.items(),key=lambda x:int(x[1]['file_id'].split('_')[1])):
        fid=m['file_id'];f=ROOT+p;ext=p.rsplit('.',1)[1]
        if ext=='sql':
            txt=open(f,encoding='utf-8').read()
            con=sqlite3.connect(':memory:');con.executescript(txt)
            hdr=re.search(r'Database topology: (\w+)',txt);topo=hdr.group(1) if hdr else 'inventory'
            for (t,) in con.execute("select name from sqlite_master where type='table'").fetchall():
                cur=con.execute(f'select * from "{t}"');cols=[c[0] for c in cur.description]
                for row in cur:
                    r=dict(zip(cols,row))
                    if 'record_json' in r:
                        j=json.loads(r['record_json']);r2=dict(j);r2['_table']=t;r2['_origin']=r['origin_system'];r2['_coll']=r['collection_name'];r2['_updated']=r['updated_at'];r=r2
                    else:r['_table']=t
                    r['_topo']=topo
                    yield dict(file_id=fid,fmt='sql',rid=r.get('resource_id'),ts=r.get('_updated') or r.get('updated_at') or r.get('indexed_at'),f=r)
        elif ext=='json':
            d=json.load(open(f,encoding='utf-8'))
            if 'snapshots' in d:
                for s in d['snapshots']:
                    for r in s['records']:
                        r=dict(r);r['_store']=s['data_store'];r['_coll']=s['collection']
                        yield dict(file_id=fid,fmt='json',rid=r.get('resource_id'),ts=s['captured_at'],f=r)
            elif 'service_exports' in d:
                for svc,v in d['service_exports'].items():
                    for coll,recs in v.items():
                        for r in recs:
                            r=dict(r);r['_store']=svc;r['_coll']=coll
                            yield dict(file_id=fid,fmt='json',rid=r.get('resource_id'),ts=r.get('observed_at'),f=r)
            else:
                for r in d['records']:
                    r=dict(r);r['_coll']=d.get('collection');r['_store']=r.get('system')
                    yield dict(file_id=fid,fmt='json',rid=r.get('resource_id'),ts=r.get('recorded_at') or r.get('captured_at') or d.get('captured_at') or r.get('created_at'),f=r)
        elif ext=='log':
            for ln in open(f,encoding='utf-8'):
                ln=ln.rstrip('\n')
                if not ln.strip():continue
                ts=ln[:20]
                rest=ln[21:]
                if rest.startswith('{'):
                    r=json.loads(rest)
                elif ' | ' in rest and not rest.startswith('level='):
                    p_=rest.split(' | ');r=kv(' | '.join(p_[4:]));r.update(severity=p_[0],service=p_[1],event=p_[3] if len(p_)>3 else '')
                elif rest.startswith('<'):
                    r=kv(rest)
                elif rest.startswith('['):
                    m2=re.match(r'\[(\w+)\] \[(\w+)\] \[([^\]]+)\] (\w+): (.*)',rest);r=kv(m2.group(5)) if m2 else kv(rest);
                    if m2:r.update(level=m2.group(1),service=m2.group(2),event=m2.group(4))
                elif 'requestId=' in rest:
                    r=kv(rest);inner=r.get('message','').replace('\\"','"');r.update(kv(inner))
                else:r=kv(rest)
                yield dict(file_id=fid,fmt='log',rid=r.get('resource_id'),ts=ts,f=r)
        elif ext=='txt':
            for ln in open(f,encoding='utf-8'):
                p_=[x.strip() for x in ln.lstrip('- ').split('|')]
                if len(p_)==6 and p_[0].startswith('20'):
                    yield dict(file_id=fid,fmt='txt',rid=p_[2],ts=p_[0],f=dict(system=p_[1],record_type=p_[3],result=p_[4],subject=p_[5]))
