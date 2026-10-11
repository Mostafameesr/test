#!/usr/bin/env python3
"""Download a Telegram video, create codec comparisons and standalone 7z Ultra artifacts."""
import json, os, re, subprocess, sys, time, shutil
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urljoin
import requests

ROOT=Path("output"); ROOT.mkdir(exist_ok=True); (ROOT/"logs").mkdir(exist_ok=True)
WORK=Path("work"); WORK.mkdir(exist_ok=True)
URL=os.environ.get("VIDEO_URL","https://t.me/DB_Fans/29").strip()
PROFILES=[
 ("h264_1080","mp4",1080,24,96),("h264_720","mp4",720,28,64),
 ("hevc_1080","mp4",1080,27,80),("hevc_720","mp4",720,31,64),
 ("hevc_480","mp4",480,34,48),("av1_1080","mp4",1080,32,80),
 ("av1_720","webm",720,38,48),("vp9_480","webm",480,40,48)
]
QUICK={"h264_720","hevc_720","av1_720"}
def probe(path):
    p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration:stream=codec_type,width,height","-of","json",str(path)],capture_output=True,text=True,timeout=60,check=True)
    meta=json.loads(p.stdout)
    vs=[s for s in meta.get("streams",[]) if s.get("codec_type")=="video"]
    if not vs: raise RuntimeError("No video stream")
    return vs[0]
def locate():
    candidates=sorted(WORK.rglob("*"),key=lambda x:x.stat().st_size if x.is_file() else 0,reverse=True)
    for p in candidates:
        if not p.is_file() or p.stat().st_size<100 or p.suffix in (".part",".json",".jpg",".webp"): continue
        try: probe(p); return p
        except Exception: pass
    return None
def download():
    match=re.match(r"^https?://(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]+)/(\d+)",URL)
    if match and all(os.getenv(k) for k in ("TELEGRAM_API_ID","TELEGRAM_API_HASH","TELEGRAM_SESSION")):
        try:
            from telethon.sync import TelegramClient
            from telethon.sessions import StringSession
            with TelegramClient(StringSession(os.environ["TELEGRAM_SESSION"]),int(os.environ["TELEGRAM_API_ID"]),os.environ["TELEGRAM_API_HASH"]) as client:
                msg=client.get_messages(client.get_entity(match[1]),ids=int(match[2]))
                if not msg or not msg.media: raise RuntimeError("No accessible media in Telegram message")
                p=client.download_media(msg,file=str(WORK.absolute())+"/")
            if p and probe(Path(p)): return Path(p)
        except Exception as e: print("Authenticated Telegram download failed:",type(e).__name__,str(e)[:200],flush=True)
    try:
        subprocess.run([sys.executable,"-m","yt_dlp","--no-playlist","--retries","4","--socket-timeout","30","-o",str(WORK/"video.%(ext)s"),URL],check=True,timeout=1800)
        p=locate()
        if p: return p
    except Exception as e: print("yt-dlp failed:",type(e).__name__,str(e)[:200],flush=True)
    if match:
        class Tags(HTMLParser):
            def __init__(self): super().__init__(); self.urls=[]
            def handle_starttag(self,tag,attrs):
                d=dict(attrs)
                if tag in ("video","source") and d.get("src"): self.urls.append(d["src"])
                if tag=="meta" and d.get("property") in ("og:video","og:video:url","og:video:secure_url") and d.get("content"): self.urls.append(d["content"])
        page=f"https://t.me/{match[1]}/{match[2]}?embed=1&mode=tme"
        session=requests.Session()
        session.headers.update({"User-Agent":"Mozilla/5.0","Referer":page})
        html=session.get(page,timeout=45); html.raise_for_status()
        tags=Tags(); tags.feed(html.text)
        for i,u in enumerate(tags.urls):
            try:
                video_url=urljoin(page,u)
                if not video_url.startswith("https://"): continue
                p=WORK/f"preview_{i}.mp4"
                with session.get(video_url,stream=True,timeout=(25,120)) as r:
                    r.raise_for_status()
                    if "text/html" in r.headers.get("content-type",""): continue
                    with p.open("wb") as f:
                        for block in r.iter_content(1024*1024):
                            if block: f.write(block)
                probe(p); return p
            except Exception as e: print("Preview candidate failed:",str(e)[:150],flush=True)
    raise RuntimeError("Unable to download Telegram video. For restricted public previews configure TELEGRAM_API_ID, TELEGRAM_API_HASH and TELEGRAM_SESSION GitHub secrets.")
def scale(height):
    ratio=f"min(1,{height}/ih)"
    return f"scale=w='max(2,trunc(iw*{ratio}/2)*2)':h='max(2,trunc(ih*{ratio}/2)*2)':flags=lanczos"
def encode(src,profile,mode):
    key,ext,height,crf,audio=profile
    dst=WORK/f"{key}.{ext}"
    cmd=["ffmpeg","-hide_banner","-nostdin","-y","-loglevel","warning","-i",str(src),"-map","0:v:0","-map","0:a:0?","-sn","-dn","-map_metadata","-1","-vf",scale(height),"-fpsmax","30","-pix_fmt","yuv420p"]
    ultra=mode=="ultra"
    if key.startswith("h264"): cmd+=["-c:v","libx264","-preset","veryslow" if ultra else "medium","-crf",str(crf)]
    elif key.startswith("hevc"): cmd+=["-c:v","libx265","-preset","slower" if ultra else "medium","-crf",str(crf),"-tag:v","hvc1","-x265-params","log-level=error"]
    elif key.startswith("av1"): cmd+=["-c:v","libsvtav1","-preset","4" if ultra else "8","-crf",str(crf),"-svtav1-params","tune=0"]
    else: cmd+=["-c:v","libvpx-vp9","-deadline","good","-cpu-used","2" if ultra else "5","-crf",str(crf),"-b:v","0","-row-mt","1"]
    cmd+=["-c:a","aac" if ext=="mp4" else "libopus","-b:a",f"{audio}k","-ac","2"]
    if ext=="mp4": cmd+=["-movflags","+faststart"]
    cmd.append(str(dst))
    with (ROOT/"logs"/f"{key}.log").open("w") as log:
        subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=35*60)
    probe(dst)
    return dst
def archive(video,key):
    dst=ROOT/f"{key}.7z"
    cmd=["7z","a","-t7z","-mx=9","-m0=lzma2","-md=256m","-mfb=273","-mmt=2","-ms=on",str(dst.absolute()),video.name]
    subprocess.run(cmd,cwd=video.parent,check=True,stdout=subprocess.DEVNULL,timeout=1800)
    if not dst.is_file() or dst.stat().st_size==0: raise RuntimeError("7z archive was not created")
    return dst
def mb(n): return f"{n/1000000:.2f} MB" if n is not None else "—"
def report(original,rows):
    lines=["# Telegram Ultra 7z — size comparison","","Original: **"+mb(original)+"**","",
           "| Variant | Codec | Actual resolution | Encoded video | Final 7z Ultra | Saving vs original | Status |",
           "|---|---|---|---:|---:|---:|---|"]
    for row in rows:
        saved=(f"{(1-row['archive_bytes']/original)*100:+.1f}%" if row.get("archive_bytes") is not None else "—")
        lines.append(f"| {row['name']} | {row['codec']} | {row.get('resolution','—')} | {mb(row.get('video_bytes'))} | **{mb(row.get('archive_bytes'))}** | {saved} | {row['status']} |")
    lines+=["","Every successful variant is published as a separately downloadable artifact containing a .7z Ultra file.","7z alone generally does not significantly shrink already-compressed video; re-encoding changes size and picture quality."]
    (ROOT/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
def main():
    print("Downloading",URL,flush=True)
    source=download()
    original=source.stat().st_size
    print("Original:",mb(original),probe(source),flush=True)
    profiles=PROFILES if os.getenv("VARIANTS","all")=="all" else [p for p in PROFILES if p[0] in QUICK]
    rows=[]
    for profile in profiles:
        key,ext,height,crf,audio=profile
        row={"name":key,"codec":ext,"status":"failed"}
        dst=None
        try:
            print("Encoding",key,"ultra",flush=True)
            dst=encode(source,profile,os.getenv("MODE","ultra"))
            row["video_bytes"]=dst.stat().st_size
            dims=probe(dst)
            row["resolution"]=f"{dims.get('width','?')}x{dims.get('height','?')}"
            packed=archive(dst,key)
            row["archive_bytes"]=packed.stat().st_size
            row["status"]="OK"
            print(key,mb(row["video_bytes"]),mb(row["archive_bytes"]),flush=True)
        except Exception as e:
            row["status"]=f"ERROR: {type(e).__name__}: {str(e)[:100]}"
            print(row["status"],flush=True)
        finally:
            if dst and dst!=source: dst.unlink(missing_ok=True)
            rows.append(row); report(original,rows)
            (ROOT/"results.json").write_text(json.dumps(rows,indent=2),encoding="utf-8")
    if not any(x["status"]=="OK" for x in rows): raise RuntimeError("All variants failed; inspect artifact logs")
if __name__=="__main__":
    main()
