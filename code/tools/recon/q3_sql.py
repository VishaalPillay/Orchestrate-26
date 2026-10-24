import sqlite3,glob,os,re,json,collections
for f in sorted(glob.glob('dataset/evidence/databases/*.sql')):
    txt=open(f,encoding='utf-8').read()
    hdr=[l for l in txt.splitlines() if l.startswith('--')]
    con=sqlite3.connect(':memory:')
    try: con.executescript(txt)
    except Exception as e: print("ERR",f,e)
    tabs=[r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    out=[]
    for t in tabs:
        cols=[r[1] for r in con.execute(f'pragma table_info("{t}")')]
        n=con.execute(f'select count(*) from "{t}"').fetchone()[0]
        out.append(f"{t}({n}) cols={cols}")
    print(os.path.basename(f));
    for h in hdr[1:5]:print("   ",h)
    for o in out:print("   ",o)
