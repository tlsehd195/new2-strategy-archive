# Independent downloader (written without reading the project's fetch code beyond its URL constants).
# Saves raw monthly zips of 1d klines and fundingRate for every USDT-M USDT symbol, DEV months only.
import re, sys, os, urllib.request, urllib.parse, concurrent.futures as cf, hashlib, time
S3="https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
OUT=sys.argv[1]
MONTHS=[f"{y}-{m:02d}" for y in range(2021,2026) for m in range(1,13)]
MONTHS=[m for m in MONTHS if ("2021-01"<=m<="2023-09") or ("2024-06"<=m<="2025-10")]
def get(url,tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url,timeout=60) as r: return r.read()
        except urllib.error.HTTPError as e:
            if e.code==404: return None
        except Exception: pass
        time.sleep(1+2*i)
    raise RuntimeError(url)
def listing(prefix):
    keys=[];marker=""
    while True:
        u=S3+"?"+urllib.parse.urlencode({"delimiter":"/","prefix":prefix,"marker":marker})
        x=get(u).decode()
        keys+=re.findall(r"<Key>([^<]+)</Key>",x)+re.findall(r"<Prefix>([^<]+)</Prefix>",x)
        if "<IsTruncated>true</IsTruncated>" not in x: return keys
        marker=re.findall(r"<(?:Key|Prefix)>([^<]+)</(?:Key|Prefix)>",x)[-1]
syms=sorted({k.split("/")[-2] for k in listing("data/futures/um/monthly/klines/") if k.endswith("/") and k.count("/")==6})
syms=[s for s in syms if s.endswith("USDT")]
print("symbols",len(syms),flush=True)
open(os.path.join(OUT,"symbols.txt"),"w").write("\n".join(syms))
def job(sym):
    n=0
    for kind,pre in (("1d",f"data/futures/um/monthly/klines/{sym}/1d/"),("fr",f"data/futures/um/monthly/fundingRate/{sym}/")):
        d=os.path.join(OUT,kind,sym); os.makedirs(d,exist_ok=True)
        keys=[k for k in listing(pre) if k.endswith(".zip")]
        for k in keys:
            ym=k[-11:-4]
            if ym not in MONTHS: continue
            fn=os.path.join(d,os.path.basename(k))
            if os.path.exists(fn): continue
            q=urllib.parse.quote(k); b=get(S3+"/"+q); c=get(S3+"/"+q+".CHECKSUM")
            if b is None: continue
            if c and c.decode().split()[0]!=hashlib.sha256(b).hexdigest(): raise RuntimeError("checksum "+k)
            open(fn,"wb").write(b); n+=1
    return sym,n
with cf.ThreadPoolExecutor(24) as ex:
    for i,(s,n) in enumerate(ex.map(job,syms)):
        if i%50==0: print(i,s,n,flush=True)
print("done")
