import sys;sys.path.insert(0,'code/tools/recon')
import textidx as T,csv,re,collections
pat=re.compile(r"ignore (all |any |previous|prior|the )|disregard|system prompt|as an? (ai|assistant|language model)|\b(llm|chatgpt|claude|gpt|openai|anthropic|gemini)\b|prompt injection|\bprompt\b|instruction|override|you (must|should|are to|will) (now )?(mark|set|output|return|respond|ignore|report|delete|skip)|mark (this|it|the case) as|do not (report|flag|include)|\bassistant\b|\bautomated (system|agent)\b|\bAI\b|\bbot\b|\bagent\b|jailbreak|developer mode|forget (everything|all)|new rule|\bexecute\b|run (the following|this command)|curl |http[s]?://",re.I)
for fn in ['deletion_requests.csv','sample_deletion_requests.csv']:
    R=list(csv.DictReader(open('dataset/'+fn,encoding='utf-8')))
    hits=[(r['case_id'],m.group(0),r['request_text'][:140]) for r in R for m in [pat.search(r['request_text'])] if m]
    print(fn,len(R),"rows;",len(hits),"pattern hits");[print("  ",h) for h in hits[:15]]
    lens=[len(r['request_text']) for r in R];print("   request_text length min/max",min(lens),max(lens),"| distinct texts",len({r['request_text'] for r in R}))
tot=0
for f,t in T.TXT.items():
    for m in pat.finditer(t):
        ctx=t[max(0,m.start()-60):m.end()+60].replace('\n',' ')
        tot+=1
        if tot<=25:print("EVID",f,repr(m.group(0)),'...',ctx)
print("evidence text hits:",tot)
for fn in ['system_activity.csv','user_identities.csv','user_audit_log.csv','deletion_rules.csv']:
    t=open('dataset/'+fn,encoding='utf-8').read();ms=list(pat.finditer(t))
    print(fn,"hits",len(ms),[t[max(0,m.start()-40):m.end()+40].replace('\n',' ') for m in ms[:3]])
# non-ASCII-language and unusual chars scan
lang=collections.Counter()
for r in csv.DictReader(open('dataset/deletion_requests.csv',encoding='utf-8')):
    t=r['request_text']
    lang['non_ascii' if re.search(r'[^\x00-\x7f]',t) else 'ascii']+=1
print(lang)
