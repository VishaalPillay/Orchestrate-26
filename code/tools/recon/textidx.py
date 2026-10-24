import csv,re,subprocess,os
ROOT='dataset/'
M=list(csv.DictReader(open(ROOT+'evidence_files.csv',encoding='utf-8')))
TXT={}
for m in M:
    p=ROOT+m['file_path']
    if p.endswith('.pdf'):t=subprocess.run(['pdftotext','-layout',p,'-'],capture_output=True,text=True,encoding='utf-8').stdout
    elif p.endswith('.png'):t=''
    else:t=open(p,encoding='utf-8').read()
    TXT[m['file_id']]=t
CAP={m['file_id']:m['captured_at'] for m in M}
def mentions(fid,rid):
    return re.search(r'(?<![A-Za-z0-9_])'+re.escape(rid)+r'(?![A-Za-z0-9_])',TXT[fid]) is not None
