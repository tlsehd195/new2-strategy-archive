#!/bin/bash
q() {
  local query="$1" name="$2" try=0 body=""
  while [ $try -lt 4 ]; do
    curl -sL "https://export.arxiv.org/api/query?search_query=$query&start=0&max_results=12&sortBy=relevance" -o "arxiv/$name.xml"
    body=$(head -c 30 "arxiv/$name.xml")
    if [ "$body" = "Rate exceeded." ]; then try=$((try+1)); sleep 30; else break; fi
  done
  python3 - "$name" <<'EOF'
import sys, re, xml.etree.ElementTree as ET
name=sys.argv[1]
try:
    t=ET.parse(f"arxiv/{name}.xml")
except Exception as e:
    print(f"[{name}] FAIL"); sys.exit()
ns={'a':'http://www.w3.org/2005/Atom'}
es=t.getroot().findall('a:entry', ns)
print(f"=== {name}: {len(es)} entries ===")
for e in es:
    aid=e.find('a:id',ns).text.strip().split('/abs/')[-1]
    ti=re.sub(r'\s+',' ',e.find('a:title',ns).text.strip())
    pu=e.find('{http://arxiv.org/schemas/atom}primary_category')
    cat=pu.get('term') if pu is not None else '?'
    dt=e.find('a:published',ns).text[:10]
    print(f"- {aid} [{cat}] {dt} | {ti[:130]}")
EOF
  sleep 15
}
