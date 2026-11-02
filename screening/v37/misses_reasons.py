import pickle,re,sys,unicodedata,html
from collections import Counter
SP=sys.argv[1]
rows,sel=pickle.load(open(SP+'/rated.pkl','rb'))
ext={x['case']:x for x in pickle.load(open(SP+'/ext210.pkl','rb'))}
def strip(s): return ''.join(c for c in unicodedata.normalize('NFKD',s or '') if not unicodedata.combining(c))
def nt(s): return re.sub(r'[^a-z0-9]','',strip(html.unescape(re.sub(r'<[^>]+>','',s or ''))).lower().replace('&','and'))
def inits(g): return ''.join(w[0].upper() for w in re.split(r'[\s.\-]+',g or '') if w and w[0].isalpha())
def lastpage(pg):
    m=re.match(r'\s*([A-Za-z]?)(\d+)\s*[-–]\s*([A-Za-z]?)(\d+)',pg or '')
    if not m: return None
    a,b=m.group(2),m.group(4)
    if len(b)<len(a): b=a[:len(a)-len(b)]+b
    return (m.group(1)+a, b)
res={}
for r in sel:
    c=r['case_id']; x=ext[c]
    p=strip(re.sub(r'\s+',' ',str(r['printed citation text exactly as printed']))); pl=p.lower(); pn=nt(p)
    pt=re.sub(r'(doi:?\s*|https?://\S+|10\.\d{4,9}/\S+|PMID:?\s*\d+|PMCID:?\s*\S+|pmid:\d+)','',p,flags=re.I)
    toks=set(re.findall(r'[A-Za-z]?\d+',pt))|set(t.lower() for t in re.findall(r'[A-Za-z]?\d+',pt))
    recs=[cc for cc in x['crossref'] if not cc.get('missing')]
    pms=list(x['pubmed'].values())
    R=[]
    titles=[(cc['title']+(' '+cc['subtitle'] if cc['subtitle'] else '')) for cc in recs]+[m['title'] for m in pms if m['title']]
    vols={str(cc['volume']) for cc in recs if cc['volume']}|{str(m['volume']) for m in pms if m['volume']}
    pages=[cc['page'] for cc in recs if cc['page']]+[m['pages'] for m in pms if m['pages']]
    arts=[cc['artno'] for cc in recs if cc['artno']]+[m['elocation'] for m in pms if m.get('elocation')]
    years={y for cc in recs for y in cc['years']}|{int(m['year'][:4]) for m in pms if m['year'] and m['year'][:4].isdigit()}
    pyears={int(y) for y in re.findall(r'\b(1[89]\d\d|20[0-3]\d)\b',p)}
    if not recs and not pms: res[c]=['no external record']; continue
    # title subtitle
    tl=[nt(t.strip('[].')) for t in titles if t]
    if tl and not any(t in pn for t in tl):
        if any(pn.find(t[:40])>=0 for t in tl): R.append('title: incomplete or altered')
        else: R.append('title: different')
    elif tl and max(len(t) for t in tl) > min(len(t) for t in tl if t in pn)+8 :
        R.append('title: subtitle missing (matches short registry form)')
    if pyears and years and not pyears&years: R.append('year wrong')
    if not pyears: R.append('year missing')
    if vols and not any(v in toks for v in vols): R.append('volume missing/wrong')
    fps=set(); lps=set()
    for pg in pages:
        lp=lastpage(pg)
        if lp: fps.add(lp[0]); lps.add(lp[1])
        elif pg.strip('- '): fps.add(re.split(r'[-–,; ]',pg.strip())[0])
    if fps and not any(f in toks or f.lower() in toks for f in fps):
        R.append('first page/article no. missing or wrong')
    elif lps and not any(l in toks or l[-2:] in toks or l[-3:] in toks or l[-1:] in toks for l in lps):
        R.append('last page missing or wrong')
    if not fps and arts and not any(a.lower().replace('doi','').strip() in pt.lower() for a in arts): R.append('article no. missing')
    # authors
    etal=bool(re.search(r'et\s*al',p,re.I))
    auth_sets=[[(a,g) for a,g in cc['authors'] if g] for cc in recs if cc['authors']]
    pm_auth=[[(' '.join(n.split()[:-1]),n.split()[-1]) for n in m['authors'] if len(n.split())>1] for m in pms if m['authors']]
    allA=[a for a in auth_sets+pm_auth if a]
    if allA:
        longest=max(allA,key=len)
        miss=[a for a,g in longest if strip(a).lower().split()[-1] not in pl]
        if miss and not etal and len(miss)>=1: R.append(f'authors missing ({len(miss)}) without et al.')
        # initials: printed shorter than max initials across records
        maxi={}
        for A in allA:
            for a,g in A:
                k=strip(a).lower(); i=inits(g) if ' ' in g or '.' in g or len(g)>3 else g.upper() if g.isupper() else inits(g)
                if len(i)>len(maxi.get(k,'')): maxi[k]=i
        om=[]
        for k,i in maxi.items():
            j=pl.find(k)
            if j<0 or len(i)<2: continue
            after=p[j+len(k):j+len(k)+12]
            m=re.match(r"[\s,.]*((?:[A-Z][\s.\-]*){1,4})(?![a-z])",after)
            before=p[max(0,j-12):j]; m2=re.search(r"((?:[A-Z]\b[\s.\-]*){1,4})\s*$",before)
            cands=[re.sub(r'[^A-Z]','',m.group(1))] if m else []
            if m2: cands.append(re.sub(r'[^A-Z]','',m2.group(1)))
            if cands and not any(cc==i for cc in cands) and any(i.startswith(cc) and len(cc)<len(i) for cc in cands): om.append(f'{k} {max(cands,key=len)}/{i}')
        if om: R.append('middle initial omitted vs fullest record: '+'; '.join(om[:3]))
    res[c]=R or ['no machine-detectable difference']
pickle.dump(res,open(SP+'/reasons.pkl','wb'))
cnt=Counter(rr.split(':')[0].split(' (')[0] for v in res.values() for rr in v)
print(cnt.most_common())
print(sum(1 for v in res.values() if v==['no machine-detectable difference']))
