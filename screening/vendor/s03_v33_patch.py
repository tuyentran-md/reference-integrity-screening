"""S03 candidate v33: independent fixes for representation-only author and acronym titles.
Builds on v32. A newly inspected r_003 QC draw is DEVELOPMENT, never a formal gate.
"""
import re

def install(S):
    import s03_v32_patch
    s03_v32_patch.install(S)
    old_compare=S.compare
    stop={'of','the','and','for','a','an','in','to','on'}

    def acronym_title_equivalent(title,cited,e):
        for ac in set(re.findall(r'\b[A-Z]{3,7}\b',title or '')):
            words=re.findall(r'[A-Za-zÀ-ÿ]+', cited or '')
            for i in range(len(words)):
                for j in range(i+2,min(len(words),i+9)+1):
                    segment=words[i:j]
                    key=''.join(w[0].upper() for w in segment if w.lower() not in stop)
                    if key!=ac:continue
                    expanded=re.sub(r'\b'+re.escape(ac)+r'\b',' '.join(segment),title, count=1)
                    if (S.title_agreement(expanded,e) or 0)>=.98:return True
        return False

    def repair_encoding(s):
        if not s or not re.search(r'Ã|Â|Ð',s):return s
        try:return s.encode('latin-1').decode('utf-8')
        except (UnicodeEncodeError,UnicodeDecodeError):return s

    def compare(rec,e):
        c=old_compare(rec,e)
        if (c.get('title_cmp') and c.get('ts') is not None and c['ts']<.98 and
                acronym_title_equivalent(rec.get('title') or '',e.get('title') or '',e)):
            c['ts']=1.0
        if c.get('au') is False and (c.get('ts') or 0)>=.9:
            fam,given=S.registry_name(rec)
            printed=(e.get('first_author') or '').strip()
            expected=S.key(fam)+(S.key(given)[:1] if given else '')
            # S02 sometimes joins surname with its initial without a delimiter,
            # e.g. "LiL". Both surname AND real first given initial must match.
            if (given and S.key(printed)==expected
                    and len(S.key(fam))>=2 and len(S.key(given))>=1
                    and re.search(r'\b'+re.escape(printed)+r'\s*[,;]',e.get('raw') or '',re.I)):
                c['au']=True
            elif (S.key(repair_encoding(printed))==S.key(fam)
                  and repair_encoding(printed)!=printed):
                # A UTF-8 mojibake sequence is extraction damage, not a citation typo.
                c['au']=True
        return c
    S.compare=compare
