import csv,hashlib,json,re,collections
U=list(csv.DictReader(open('dataset/user_identities.csv',encoding='utf-8')))
L=list(csv.DictReader(open('dataset/user_audit_log.csv',encoding='utf-8')))
byuid={u['user_id']:u for u in U}
hist=collections.defaultdict(list)
for l in L:hist[l['user_id']].append(l)
def idset(uid):
    u=byuid[uid];emails={u['email'].lower()};phones={u['phone']};names={u['name']}
    for l in hist[uid]:
        for v in (l['old_value'],l['new_value']):
            if l['field_name']=='email':emails.add(v.lower())
            if l['field_name']=='phone':phones.add(v)
            if l['field_name']=='name':names.add(v)
    hashes={hashlib.sha256(e.encode()).hexdigest() for e in emails}
    return dict(user_id=uid,emails=emails,phones=phones,names=names,hashes=hashes)
email2uid={}
for uid in byuid:
    for e in idset(uid)['emails']:email2uid.setdefault(e,set()).add(uid)
def resolve(email):return sorted(email2uid.get(email.lower(),[]))
def vals(x):
    if isinstance(x,dict):
        for v in x.values():yield from vals(v)
    elif isinstance(x,list):
        for v in x:yield from vals(v)
    elif isinstance(x,str):yield x
def matches(f,ids):
    for v in vals(f):
        lv=v.lower()
        if lv in ids['emails'] or v in ids['phones'] or lv in ids['hashes'] or v==ids['user_id']:return True
        # emails embedded in free text
        for m in re.findall(r'[\w.+-]+@[\w.-]+\.\w+',lv):
            if m in ids['emails']:return True
    return False
