import json, urllib.request, urllib.error, hashlib, tempfile, os, shutil, zipfile, subprocess, sys
from pathlib import Path

USER_PRESERVE = {"config.json","data","logs","backups","publisher_release_channel.json"}

def _version_tuple(v):
    parts=[]
    for x in str(v).strip().lstrip("v").split("."):
        try: parts.append(int("".join(ch for ch in x if ch.isdigit()) or 0))
        except Exception: parts.append(0)
    return tuple((parts+[0,0,0])[:3])

class Updater:
    """
    Internet updater supporting:
      1) a static JSON manifest URL, or
      2) GitHub Releases API via github_repo='owner/repository'.

    Manifest example:
    {
      "version":"1.3.1",
      "download_url":"https://.../Forex_Trading_V1_v1.3.1.zip",
      "sha256":"...",
      "changelog":"..."
    }
    """
    def __init__(self,current_version,manifest_url="",github_repo=""):
        self.current_version=str(current_version)
        self.manifest_url=(manifest_url or "").strip()
        self.github_repo=(github_repo or "").strip()
        self.user_agent="Forex-Trading-V1-Updater/2.2.4"

    def _get_json(self,url):
        req=urllib.request.Request(url,headers={"User-Agent":self.user_agent,"Accept":"application/json"})
        with urllib.request.urlopen(req,timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))


    def validate_github_repo(self):
        if not self.github_repo:
            return {"ok":False,"message":"No GitHub repository configured."}
        try:
            url=f"https://api.github.com/repos/{self.github_repo}"
            d=self._get_json(url)
            return {"ok":True,"name":d.get("full_name",self.github_repo),"private":bool(d.get("private",False))}
        except urllib.error.HTTPError as e:
            if e.code==404:
                return {"ok":False,"message":"GitHub repository was not found or is not public."}
            return {"ok":False,"message":f"GitHub returned HTTP {e.code}."}
        except Exception as e:
            return {"ok":False,"message":f"Could not verify GitHub repository: {e}"}

    def _github_release(self):
        url=f"https://api.github.com/repos/{self.github_repo}/releases/latest"
        try:
            d=self._get_json(url)
        except urllib.error.HTTPError as e:
            if e.code==404:
                return {
                    "status":"no_releases",
                    "message":"Release channel is connected, but no GitHub Release has been published yet.",
                    "source":"GitHub Releases"
                }
            raise
        version=str(d.get("tag_name","")).lstrip("v")
        assets=d.get("assets",[])
        zips=[a for a in assets if str(a.get("name","")).lower().endswith(".zip")]
        if not zips:
            return {"status":"error","message":"Latest GitHub release has no ZIP update package."}
        asset=zips[0]
        return {
            "status":"available" if _version_tuple(version)>_version_tuple(self.current_version) else "current",
            "version":version,
            "download_url":asset.get("browser_download_url",""),
            "sha256":"",
            "changelog":d.get("body","") or "No changelog supplied.",
            "source":"GitHub Releases"
        }

    def check(self):
        try:
            if self.github_repo:
                return self._github_release()
            if self.manifest_url:
                d=self._get_json(self.manifest_url)
                version=str(d.get("version","0"))
                return {
                    "status":"available" if _version_tuple(version)>_version_tuple(self.current_version) else "current",
                    "version":version,
                    "download_url":d.get("download_url") or d.get("download") or "",
                    "sha256":str(d.get("sha256","")).lower(),
                    "changelog":d.get("changelog") or d.get("message") or "No changelog supplied.",
                    "source":"Release Feed"
                }
            return {
                "status":"setup_required",
                "message":"Updater is installed, but the publisher internet release channel is not activated in this build."
            }
        except urllib.error.URLError as e:
            return {"status":"error","message":f"Could not reach update server: {e}"}
        except Exception as e:
            return {"status":"error","message":f"Update check failed: {e}"}

    def download(self,info,progress=None):
        url=info.get("download_url","")
        if not url: raise RuntimeError("Release has no download URL.")
        req=urllib.request.Request(url,headers={"User-Agent":self.user_agent})
        fd,path=tempfile.mkstemp(prefix="fxai_update_",suffix=".zip"); os.close(fd)
        got=0
        with urllib.request.urlopen(req,timeout=30) as r, open(path,"wb") as f:
            total=int(r.headers.get("Content-Length") or 0)
            while True:
                chunk=r.read(1024*256)
                if not chunk: break
                f.write(chunk); got+=len(chunk)
                if progress: progress(got,total)
        expected=(info.get("sha256") or "").strip().lower()
        if expected:
            h=hashlib.sha256()
            with open(path,"rb") as f:
                for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
            actual=h.hexdigest()
            if actual!=expected:
                os.unlink(path)
                raise RuntimeError("SHA-256 verification failed. Update was not installed.")
        with zipfile.ZipFile(path,"r") as z:
            bad=z.testzip()
            if bad:
                os.unlink(path); raise RuntimeError(f"ZIP integrity check failed at {bad}.")
        return path

    @staticmethod
    def write_install_script(update_zip, app_dir, restart_file="main.py"):
        """
        Creates a one-shot Python installer outside the app directory.
        It backs up program files, preserves user data, overlays the update,
        and restarts the app. The current process should exit after launching it.
        """
        script=Path(tempfile.gettempdir())/"fxai_apply_update.py"
        payload = r"""
import os, sys, time, zipfile, shutil, tempfile, subprocess
from pathlib import Path
update_zip=Path(%r)
app_dir=Path(%r)
restart_file=%r
preserve={"config.json","data","logs","backups","publisher_release_channel.json"}
time.sleep(1.5)
backup=app_dir.parent/(app_dir.name+"_program_backup")
if backup.exists(): shutil.rmtree(backup,ignore_errors=True)
backup.mkdir(parents=True,exist_ok=True)
for p in app_dir.iterdir():
    if p.name in preserve: continue
    dst=backup/p.name
    try:
        if p.is_dir(): shutil.copytree(p,dst,dirs_exist_ok=True)
        else: shutil.copy2(p,dst)
    except Exception: pass
tmp=Path(tempfile.mkdtemp(prefix="fxai_extract_"))
try:
    with zipfile.ZipFile(update_zip,"r") as z:z.extractall(tmp)
    roots=[p for p in tmp.iterdir() if p.is_dir()]
    src=roots[0] if len(roots)==1 and (roots[0]/"main.py").exists() else tmp
    for p in src.iterdir():
        if p.name in preserve: continue
        dst=app_dir/p.name
        if dst.exists():
            if dst.is_dir(): shutil.rmtree(dst,ignore_errors=True)
            else:
                try: dst.unlink()
                except Exception: pass
        if p.is_dir(): shutil.copytree(p,dst)
        else: shutil.copy2(p,dst)
finally:
    shutil.rmtree(tmp,ignore_errors=True)
    try: os.unlink(update_zip)
    except Exception: pass
exe=sys.executable
if os.name=="nt":
    candidate=Path(sys.executable).with_name("pythonw.exe")
    if candidate.exists(): exe=str(candidate)
subprocess.Popen([exe,str(app_dir/restart_file)],cwd=str(app_dir),creationflags=(0x08000000 if os.name=="nt" else 0))
""" % (str(update_zip),str(app_dir),restart_file)
        script.write_text(payload,encoding="utf-8")
        return str(script)
