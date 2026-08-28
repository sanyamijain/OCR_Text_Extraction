#!/usr/bin/env python3
"""Dependency-free, same-Wi-Fi UI for the existing resumable Surya pipeline."""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
RUNNER = ROOT / "run_surya.py"
EXPORTER = ROOT / "export_documents.py"
JOBS_ROOT = ROOT / "runtime" / "lan_jobs"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
JOBS: dict[str, dict] = {}
LOCK = threading.RLock()
QUEUE: queue.Queue[str] = queue.Queue()


def safe_pdf_name(name: str) -> str:
    name = Path(name or "upload.pdf").name
    stem = re.sub(r"[^A-Za-z0-9._()\- ]+", "_", Path(name).stem).strip(" ._")
    return f"{stem or 'upload'}.pdf"


def local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def add_log(job_id: str, line: str) -> None:
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["log"] = (job["log"] + line)[-50000:]
        progress = re.search(r"Page\s+(\d+)\s*/\s*(\d+).*?([\d.]+)%.*?\|\s*(.*)$", line, re.IGNORECASE)
        if progress:
            current, total, percent, status = progress.groups()
            job.update(progress_current=int(current), progress_total=int(total), progress_percent=float(percent), page_status=status.strip())
        detail = re.search(r"Page\s+(\d+):\s*(.+)", line, re.IGNORECASE)
        if detail:
            job["page_status"] = f"Page {detail.group(1)}: {detail.group(2).strip()}"
        job["updated_at"] = time.time()


def run_command(job_id: str, command: list[str]) -> int:
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    with LOCK:
        JOBS[job_id]["process_id"] = process.pid
    assert process.stdout is not None
    for line in process.stdout:
        add_log(job_id, line)
    return process.wait()


def public_job(job: dict) -> dict:
    return {key: job.get(key) for key in (
        "id", "filename", "state", "message", "log", "outputs", "progress_current",
        "progress_total", "progress_percent", "page_status", "created_at", "updated_at"
    )}


def worker() -> None:
    while True:
        job_id = QUEUE.get()
        with LOCK:
            job = JOBS[job_id]
            job.update(state="ocr", message="Surya OCR is processing the PDF.", updated_at=time.time())
        try:
            code = run_command(job_id, [str(PYTHON), "-u", str(RUNNER), job["pdf_path"]])
            if code != 0:
                raise RuntimeError(f"Surya OCR exited with code {code}. Run can be resumed safely.")
            json_path = ROOT / "output" / "surya" / f"{Path(job['filename']).stem}_surya.json"
            if not json_path.is_file():
                raise FileNotFoundError(f"Expected OCR result was not created: {json_path.name}")
            with LOCK:
                job.update(state="exporting", message="OCR completed; creating Word and Excel files.", progress_percent=100, page_status="OCR complete", updated_at=time.time())
            code = run_command(job_id, [str(PYTHON), "-u", str(EXPORTER), str(json_path), "--destination", job["output_dir"]])
            if code != 0:
                raise RuntimeError(f"Document export exited with code {code}.")
            outputs = sorted(p.name for p in Path(job["output_dir"]).iterdir() if p.suffix.lower() in {".docx", ".xlsx"})
            if not outputs:
                raise RuntimeError("Document export finished without Word or Excel output.")
            with LOCK:
                job.update(state="completed", message="Surya OCR, Word, and Excel outputs are ready.", outputs=outputs, updated_at=time.time())
        except Exception as error:
            add_log(job_id, f"\nUI ERROR: {error}\n")
            with LOCK:
                job.update(state="failed", message=str(error), updated_at=time.time())
        finally:
            QUEUE.task_done()


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Surya OCR Studio</title><style>
:root{font-family:Inter,Segoe UI,sans-serif;color:#2d194d;background:linear-gradient(135deg,#fff4df,#f2eaff)}body{margin:0;min-height:100vh}.wrap{max-width:900px;margin:38px auto;padding:20px}
.brand{display:flex;align-items:center;gap:14px}.sun{width:48px;height:48px;border-radius:50%;background:#ff9d1d;box-shadow:0 0 0 9px #ffe0a6}.card{background:#fff;border-top:7px solid #7b3fb0;border-radius:18px;padding:30px;box-shadow:0 12px 35px #4a246224}h1{margin:0;color:#642993}.tag{color:#a45200;font-weight:700;margin-top:3px}.hint{color:#685b72}
label{display:block;font-weight:700;margin:20px 0 8px}input,button{font:inherit}input[type=file]{width:100%;box-sizing:border-box;padding:13px;border:1px solid #cbbadd;border-radius:9px;background:#fdfaff}button{margin-top:20px;background:#7b3fb0;color:#fff;border:0;border-radius:9px;padding:13px 24px;font-weight:750;cursor:pointer}button:disabled{opacity:.6}
#status{display:none;margin-top:24px;padding:17px;background:#fbf6ff;border:1px solid #e2d2f0;border-radius:11px}.track{height:20px;background:#e2d7eb;border-radius:20px;overflow:hidden;margin:14px 0 7px}.bar{height:100%;width:0;background:linear-gradient(90deg,#ff9d1d,#7b3fb0);transition:width .4s}.detail{color:#6b5879}.downloads a{display:inline-block;margin:10px 10px 0 0;padding:10px 14px;background:#ffe8bd;color:#6b3500;border-radius:8px;text-decoration:none;font-weight:700}pre{max-height:390px;overflow:auto;background:#261438;color:#f8ecff;padding:15px;border-radius:9px;white-space:pre-wrap}
</style></head><body><main class="wrap"><section class="card"><div class="brand"><div class="sun"></div><div><h1>Surya OCR Studio</h1><div class="tag">High-accuracy local document extraction</div></div></div>
<p class="hint">Upload one PDF. Existing resumable Surya OCR and the current Word/Excel formatting pipeline will be used unchanged.</p><form id="form"><label>PDF document</label><input name="pdf" type="file" accept="application/pdf,.pdf" required><button id="submit">Start Surya extraction</button></form>
<div id="status"><strong id="message"></strong><div class="track"><div class="bar" id="bar"></div></div><div class="detail" id="progressText">Waiting…</div><div class="downloads" id="downloads"></div><pre id="log"></pre></div></section></main><script>
const token=new URLSearchParams(location.search).get('token')||'';
document.getElementById('form').onsubmit=async e=>{e.preventDefault();const b=document.getElementById('submit');b.disabled=true;b.textContent='Uploading…';try{const r=await fetch('/api/jobs?token='+encodeURIComponent(token),{method:'POST',body:new FormData(e.target)});const d=await r.json();if(!r.ok)throw new Error(d.error||'Upload failed');document.getElementById('status').style.display='block';b.textContent='Surya OCR running…';poll(d.id)}catch(x){alert(x.message);b.disabled=false;b.textContent='Start Surya extraction'}};
async function poll(id){try{const r=await fetch('/api/jobs/'+id+'?token='+encodeURIComponent(token));const d=await r.json();if(!r.ok)throw new Error(d.error||'Status unavailable');document.getElementById('message').textContent=d.state.toUpperCase()+': '+d.message;document.getElementById('bar').style.width=(d.progress_percent||0)+'%';document.getElementById('progressText').textContent=d.progress_total?(d.progress_percent+'% — page '+d.progress_current+' of '+d.progress_total+' — '+(d.page_status||'')):(d.state==='queued'?'Waiting behind the current job…':'Loading Surya models and preparing the first page…');document.getElementById('log').textContent=d.log||'Waiting for terminal output…';const box=document.getElementById('downloads');box.innerHTML='';(d.outputs||[]).forEach(f=>{const a=document.createElement('a');a.textContent='Download '+f;a.href='/api/jobs/'+id+'/files/'+encodeURIComponent(f)+'?token='+encodeURIComponent(token);box.appendChild(a)});if(['queued','ocr','exporting'].includes(d.state)){setTimeout(()=>poll(id),2000)}else{const b=document.getElementById('submit');b.disabled=false;b.textContent='Start another extraction'}}catch(x){document.getElementById('message').textContent=x.message;setTimeout(()=>poll(id),4000)}}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "SuryaOCRLAN/1.0"

    def token_ok(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        return secrets.compare_digest(query.get("token", [""])[0], self.server.access_token)

    def send_bytes(self, data, content_type, status=HTTPStatus.OK, disposition=None):
        self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
        if disposition: self.send_header("Content-Disposition", disposition)
        self.end_headers(); self.wfile.write(data)

    def send_json(self, value, status=HTTPStatus.OK):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if path == "/" and self.token_ok(): return self.send_bytes(PAGE.encode(), "text/html; charset=utf-8")
        if not self.token_ok(): return self.send_json({"error":"Invalid or missing access token."}, HTTPStatus.FORBIDDEN)
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{16})", path)
        if match:
            with LOCK: response = public_job(JOBS[match.group(1)]) if match.group(1) in JOBS else None
            return self.send_json(response) if response else self.send_json({"error":"Job not found."}, HTTPStatus.NOT_FOUND)
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{16})/files/(.+)", path)
        if match:
            with LOCK: job = JOBS.get(match.group(1))
            if not job: return self.send_json({"error":"Job not found."}, HTTPStatus.NOT_FOUND)
            name = Path(match.group(2)).name; path_obj = Path(job["output_dir"]) / name
            if name not in job.get("outputs",[]) or not path_obj.is_file(): return self.send_json({"error":"File not found."}, HTTPStatus.NOT_FOUND)
            kind = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if path_obj.suffix.lower()==".docx" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            return self.send_bytes(path_obj.read_bytes(), kind, disposition=f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}")
        self.send_json({"error":"Not found."}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path != "/api/jobs" or not self.token_ok(): return self.send_json({"error":"Not found or unauthorized."}, HTTPStatus.FORBIDDEN)
        try:
            length=int(self.headers.get("Content-Length","0")); content_type=self.headers.get("Content-Type","")
            if length<=0 or length>MAX_UPLOAD_BYTES: return self.send_json({"error":"PDF must be smaller than 200 MB."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            if "multipart/form-data" not in content_type: return self.send_json({"error":"Expected a form upload."}, HTTPStatus.BAD_REQUEST)
            raw=self.rfile.read(length); message=BytesParser(policy=default).parsebytes((f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode()+raw); upload=None
            for part in message.iter_parts():
                if part.get_param("name",header="content-disposition")=="pdf" and part.get_filename(): upload=(part.get_filename(),part.get_payload(decode=True))
            if not upload or not upload[1].startswith(b"%PDF-"): return self.send_json({"error":"The selected file is not a valid PDF."}, HTTPStatus.BAD_REQUEST)
            filename=safe_pdf_name(upload[0]); job_id=secrets.token_hex(8); job_dir=JOBS_ROOT/job_id; input_dir=job_dir/"input"; output_dir=job_dir/"output"; input_dir.mkdir(parents=True); output_dir.mkdir(); pdf_path=input_dir/filename; pdf_path.write_bytes(upload[1]); now=time.time()
            job={"id":job_id,"filename":filename,"state":"queued","message":"Waiting for the Surya worker.","log":"","outputs":[],"progress_current":0,"progress_total":0,"progress_percent":0,"page_status":"","pdf_path":str(pdf_path),"output_dir":str(output_dir),"created_at":now,"updated_at":now}
            with LOCK: JOBS[job_id]=job
            QUEUE.put(job_id); self.send_json({"id":job_id},HTTPStatus.ACCEPTED)
        except Exception as error: self.send_json({"error":f"Upload could not be accepted: {error}"},HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt, *args): sys.stdout.write("Surya LAN UI: "+fmt%args+"\n")


def main():
    parser=argparse.ArgumentParser(description="Run Surya OCR UI for trusted devices on the same Wi-Fi."); parser.add_argument("--host",default="0.0.0.0"); parser.add_argument("--port",type=int,default=8502); parser.add_argument("--token",default=os.environ.get("SURYA_OCR_ACCESS_TOKEN") or secrets.token_urlsafe(18)); args=parser.parse_args()
    if not PYTHON.is_file(): raise SystemExit(f"Virtual environment Python not found: {PYTHON}")
    JOBS_ROOT.mkdir(parents=True,exist_ok=True); threading.Thread(target=worker,daemon=True,name="surya-lan-worker").start(); server=ThreadingHTTPServer((args.host,args.port),Handler); server.access_token=args.token
    url=f"http://{local_ip()}:{args.port}/?token={urllib.parse.quote(args.token)}"; print(f"Surya OCR LAN UI is running.\nOpen or share on the same Wi-Fi:\n{url}\nPress Ctrl+C to stop.")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nSurya OCR UI stopped.")
    finally: server.server_close()
    return 0


if __name__=="__main__": raise SystemExit(main())
