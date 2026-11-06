"""Shared, conservative public-text normalization. No provider metadata is trusted."""
import json
import re
import unicodedata

VERSION='public-text-v0.4'

def normalize_public_text(raw):
    if not isinstance(raw,str):raise TypeError('public_text must be str')
    text=unicodedata.normalize('NFKC',raw).replace('\r\n','\n').strip()
    flags=[]
    fenced=re.fullmatch(r'```(?:json)?\s*\n?(.*?)\n?```',text,re.S)
    if fenced:text=fenced[1].strip()
    if text.startswith(('{','[')):
        def pairs(items):
            d={}
            for k,v in items:
                if k in d:raise ValueError('duplicate JSON key')
                d[k]=v
            return d
        try:
            obj=json.loads(text,object_pairs_hook=pairs)
            bodies=[k for k in ('message','speech','public_text') if isinstance(obj,dict) and k in obj]
            if len(bodies)==1 and isinstance(obj[bodies[0]],str) and set(obj)<=set(bodies+['role','intent','type']):
                text=obj[bodies[0]]
                if len(obj)>1:flags.append('untrusted_wrapper_metadata_ignored')
            else:
                flags.append('ambiguous_public_text_format')
        except (ValueError,TypeError,RecursionError):flags.append('malformed_json_text')
    # Preserve quoted attribution even when Markdown's > presentation marker is removed.
    text=re.sub(r'(?m)^\s*>\s*(.*)$',lambda m:m[1] if re.match(r'(?:10|[1-9])号.*(?:说|声称|表示)',m[1]) else '有人说“'+m[1]+'”',text)
    text=re.sub(r'(?m)^\s*#{1,6}\s+','',text)
    text=re.sub(r'(?m)^\s*[-+*]\s+','',text)
    text=re.sub(r'(?<!\d)(?:\b\d{1,2})[.)]\s+(?=[^\d])','',text)
    text=text.replace('**','').replace('__','').replace('`','')
    text=re.sub(r'[ \t]+',' ',text).strip()
    return text,flags
