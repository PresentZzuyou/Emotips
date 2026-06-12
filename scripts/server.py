#!/usr/bin/env python3
"""
이모팁스 영상 편집 서버
실행: python3 scripts/server.py
브라우저: http://localhost:5001
"""

import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # 4GB

SCRIPT_DIR = Path(__file__).parent
EDITOR_SCRIPT = SCRIPT_DIR / "video_editor.py"
DESKTOP = Path.home() / "Desktop"

# 진행상황 스트림 큐 (job_id → queue)
_progress: dict[str, queue.Queue] = {}


def stream_edit(job_id: str, input_path: str, orig_name: str):
    q = _progress[job_id]

    def send(msg, type_="log"):
        q.put(json.dumps({"type": type_, "msg": msg}))

    try:
        send(f"📹 파일 수신: {orig_name}")
        stem = Path(orig_name).stem
        output_path = str(DESKTOP / f"{stem}_edited.mp4")
        srt_path    = str(DESKTOP / f"{stem}_subtitles.srt")

        cmd = [
            "python3", str(EDITOR_SCRIPT),
            input_path,
            "--output", output_path,
            "--srt", srt_path,
        ]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                send(line)
        proc.wait()

        if proc.returncode == 0:
            send(f"✅ 완료! 바탕화면에 저장: {stem}_edited.mp4", "done")
            send(output_path, "path")
        else:
            send("❌ 편집 중 오류가 발생했습니다.", "error")
    except Exception as e:
        send(f"❌ 오류: {e}", "error")
    finally:
        try:
            os.unlink(input_path)
        except Exception:
            pass
        q.put(None)  # sentinel


@app.route("/")
def index():
    return send_file(SCRIPT_DIR / "editor.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "video" not in request.files:
        return jsonify(error="파일 없음"), 400

    f = request.files["video"]
    suffix = Path(f.filename).suffix or ".mov"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    f.save(tmp.name)
    tmp.close()

    import uuid
    job_id = uuid.uuid4().hex
    _progress[job_id] = queue.Queue()

    t = threading.Thread(
        target=stream_edit,
        args=(job_id, tmp.name, f.filename),
        daemon=True
    )
    t.start()
    return jsonify(job_id=job_id)


@app.route("/progress/<job_id>")
def progress(job_id):
    if job_id not in _progress:
        return jsonify(error="없는 작업"), 404

    q = _progress[job_id]

    def generate():
        while True:
            item = q.get()
            if item is None:
                yield "data: {\"type\":\"end\"}\n\n"
                break
            yield f"data: {item}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    print("=" * 50)
    print("  이모팁스 영상 편집 서버 실행 중")
    print("  브라우저: http://localhost:5001")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)
