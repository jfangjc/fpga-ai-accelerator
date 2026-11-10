import json, sys, re
def words(s): return s.split()
def ed(a,b):
    d=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        p,d[0]=d[0],i
        for j,y in enumerate(b,1): p,d[j]=d[j],min(d[j]+1,d[j-1]+1,p+(x!=y))
    return d[-1]
log, ref = sys.argv[1], sys.argv[2]
finals=[]
for l in open(log):
    e=json.loads(re.sub(r':(-?nan|-?inf)', ':null', l))
    if e.get('final') and e.get('state')=='Committed': finals.append((e['phrase'],e['time'],e['final']))
for f in finals: print(f"phrase {f[0]} t={f[1]:.2f}s: {f[2]}")
hyp=' '.join(f[2] for f in finals).split(); r=' '.join(c['text'] for c in json.load(open(ref))).split()
print(f"WER {ed(r,hyp)}/{len(r)} = {100*ed(r,hyp)/len(r):.2f}%")
