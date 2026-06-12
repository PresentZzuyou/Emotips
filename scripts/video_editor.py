#!/usr/bin/env python3
"""
이모팁스 영상 자동 편집 도구
- 무음 구간 삭제
- 더듬거림/재시작 구간 자동 감지 및 삭제
- 음성 자동 자막 생성 (SRT + 영상 임베드)

사용법:
  python3 scripts/video_editor.py 영상파일.mov
  python3 scripts/video_editor.py 영상파일.mov --no-subtitles
  python3 scripts/video_editor.py 영상파일.mov --silence-threshold -35 --min-silence 0.4
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


# ─── 설정 기본값 ───────────────────────────────────────────────
SILENCE_DB      = -40     # 무음으로 판단할 dB 임계값
MIN_SILENCE_SEC = 1.0     # 최소 무음 길이(초) — 이보다 짧으면 유지 (호흡/쉼표 보존)
KEEP_PADDING    = 0.4     # 말 끝에 남길 여유(초)
STUTTER_GAP     = 1.5     # 더듬거림 탐지 최대 간격(초)
STUTTER_OVERLAP = 0.6     # 반복 단어 유사도 임계값


def run(cmd, **kwargs):
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        print(f"  [ffmpeg 오류]\n{result.stderr[-2000:]}")
        raise subprocess.CalledProcessError(result.returncode, cmd)
    return result


# ─── 1. 무음 구간 감지 (ffmpeg silencedetect) ──────────────────
def detect_silence(video_path: str, db: float, min_sec: float) -> list[tuple[float, float]]:
    result = run([
        "ffmpeg", "-i", video_path,
        "-af", f"silencedetect=noise={db}dB:d={min_sec}",
        "-f", "null", "-"
    ])
    output = result.stderr
    silences = []
    starts = re.findall(r"silence_start: ([\d.]+)", output)
    ends   = re.findall(r"silence_end: ([\d.]+)", output)
    for s, e in zip(starts, ends):
        silences.append((float(s), float(e)))
    return silences


def get_duration(video_path: str) -> float:
    result = run(["ffprobe", "-v", "quiet", "-print_format", "json",
                  "-show_format", video_path])
    return float(json.loads(result.stdout)["format"]["duration"])


def silence_to_keep(silences: list, duration: float, padding: float) -> list[tuple[float, float]]:
    """무음 구간을 제외한 유지 구간 계산"""
    keep = []
    prev = 0.0
    for s, e in silences:
        end_with_pad = min(s + padding, e)
        if end_with_pad > prev + 0.05:
            keep.append((prev, end_with_pad))
        prev = max(prev, e - padding)
    if duration - prev > 0.05:
        keep.append((prev, duration))
    return keep


# ─── 2. Whisper 음성 인식 ──────────────────────────────────────
def transcribe(video_path: str, model: str = "small") -> list[dict]:
    """Whisper로 전사, 세그먼트 목록 반환"""
    try:
        import whisper
    except ImportError:
        print("whisper 미설치. pip3 install openai-whisper")
        sys.exit(1)

    print(f"  [Whisper] 모델 '{model}' 로딩 중...")
    m = whisper.load_model(model)
    print("  [Whisper] 음성 인식 중... (영상 길이에 따라 수분 소요)")
    result = m.transcribe(video_path, language="ko", word_timestamps=True)
    return result["segments"]


# ─── 3. 더듬거림 구간 감지 ────────────────────────────────────
def find_stutter_cuts(segments: list[dict], gap: float, overlap: float) -> list[tuple[float, float]]:
    """
    연속된 세그먼트에서 짧은 간격 내 비슷한 내용이 반복되면
    앞 발화를 잘라낼 구간으로 표시
    """
    cuts = []
    texts = [(s["start"], s["end"], s["text"].strip()) for s in segments]

    for i in range(len(texts) - 1):
        s1, e1, t1 = texts[i]
        s2, e2, t2 = texts[i + 1]

        if s2 - e1 > gap:
            continue

        # 단어 단위 유사도 비교
        words1 = set(re.sub(r"[^\w]", " ", t1).split())
        words2 = set(re.sub(r"[^\w]", " ", t2).split())
        if not words1 or not words2:
            continue
        common = words1 & words2
        sim = len(common) / max(len(words1), len(words2))

        if sim >= overlap:
            # 앞 발화(i)를 제거 대상으로
            cuts.append((s1, e1))

    return cuts


def remove_cuts_from_keep(keep: list, cuts: list) -> list:
    """더듬거림 제거 구간을 keep 목록에서 빼기"""
    result = []
    for ks, ke in keep:
        segs = [(ks, ke)]
        for cs, ce in cuts:
            new_segs = []
            for ss, se in segs:
                if ce <= ss or cs >= se:
                    new_segs.append((ss, se))
                elif cs <= ss and ce >= se:
                    pass  # 완전히 포함 → 제거
                elif cs <= ss:
                    new_segs.append((ce, se))
                elif ce >= se:
                    new_segs.append((ss, cs))
                else:
                    new_segs.append((ss, cs))
                    new_segs.append((ce, se))
            segs = new_segs
        result.extend(segs)
    return [(s, e) for s, e in result if e - s > 0.05]


# ─── 4. 자막(SRT) 생성 ────────────────────────────────────────
def format_ts(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int((sec % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def segments_to_srt(segments: list[dict], output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{format_ts(seg['start'])} --> {format_ts(seg['end'])}\n")
            f.write(seg["text"].strip() + "\n\n")
    print(f"  [자막] SRT 저장: {output_path}")


# ─── 5. ffmpeg 편집 (concat filter) ───────────────────────────
def cut_and_concat(video_path: str, keep: list,
                   output_path: str, srt_path: Optional[str] = None):
    if not keep:
        print("  [오류] 유지할 구간이 없습니다.")
        sys.exit(1)

    # concat demuxer 방식 — 품질 손실 없음
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        list_path = f.name
        for s, e in keep:
            f.write(f"file '{os.path.abspath(video_path)}'\n")
            f.write(f"inpoint {s:.6f}\n")
            f.write(f"outpoint {e:.6f}\n")

    # 임시 출력 경로 (자막 없는 버전)
    tmp_out = output_path.replace(".mp4", "_tmp.mp4") if srt_path else output_path

    try:
        # 1단계: concat (무음·더듬거림 제거)
        run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k",
            tmp_out
        ])
    finally:
        os.unlink(list_path)

    if srt_path:
        # 2단계: 자막 소프트 임베드 (mov_text 스트림으로 삽입 — libass 불필요)
        try:
            run([
                "ffmpeg", "-y",
                "-i", tmp_out,
                "-i", srt_path,
                "-c:v", "copy",
                "-c:a", "copy",
                "-c:s", "mov_text",
                "-metadata:s:s:0", "language=kor",
                output_path
            ])
            print("  [자막] 영상에 자막 스트림 삽입 완료 (플레이어에서 자막 켜기)")
        except subprocess.CalledProcessError:
            print("  [경고] 자막 스트림 삽입 실패 — 자막 없는 편집본으로 저장합니다.")
            import shutil
            shutil.move(tmp_out, output_path)
            return
        finally:
            if os.path.exists(tmp_out):
                os.unlink(tmp_out)


# ─── 메인 ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="이모팁스 영상 자동 편집")
    parser.add_argument("input", help="입력 영상 파일 경로")
    parser.add_argument("--no-subtitles", action="store_true", help="자막 생성 생략")
    parser.add_argument("--no-stutter",   action="store_true", help="더듬거림 감지 생략")
    parser.add_argument("--silence-threshold", type=float, default=SILENCE_DB,
                        help=f"무음 dB 임계값 (기본: {SILENCE_DB})")
    parser.add_argument("--min-silence", type=float, default=MIN_SILENCE_SEC,
                        help=f"최소 무음 길이 초 (기본: {MIN_SILENCE_SEC})")
    parser.add_argument("--model", default="small",
                        help="Whisper 모델: tiny/base/small/medium/large (기본: small)")
    parser.add_argument("--output", default=None, help="출력 영상 경로 (기본: 입력 파일 옆)")
    parser.add_argument("--srt",    default=None, help="자막 SRT 경로 (기본: 입력 파일 옆)")
    args = parser.parse_args()

    input_path = args.input
    if not os.path.exists(input_path):
        print(f"파일 없음: {input_path}")
        sys.exit(1)

    stem = Path(input_path).stem
    out_dir = Path(input_path).parent
    srt_path    = args.srt    or str(out_dir / f"{stem}_subtitles.srt")
    output_path = args.output or str(out_dir / f"{stem}_edited.mp4")

    print(f"\n📹 입력: {input_path}")
    print("=" * 50)

    # ① 전체 길이
    duration = get_duration(input_path)
    print(f"  [정보] 영상 길이: {duration:.1f}초")

    # ② 무음 감지
    print(f"\n① 무음 구간 감지 중 (임계값: {args.silence_threshold}dB, 최소: {args.min_silence}s)...")
    silences = detect_silence(input_path, args.silence_threshold, args.min_silence)
    print(f"  → 무음 구간 {len(silences)}개 발견")

    keep = silence_to_keep(silences, duration, KEEP_PADDING)
    removed_sec = duration - sum(e - s for s, e in keep)
    print(f"  → 무음 제거 후 유지 구간: {len(keep)}개 (약 {removed_sec:.1f}초 절약)")

    # ③ Whisper 음성 인식 + 더듬거림 감지
    segments = None
    if not args.no_subtitles or not args.no_stutter:
        print(f"\n② Whisper 음성 인식 중...")
        segments = transcribe(input_path, args.model)
        print(f"  → 세그먼트 {len(segments)}개 인식 완료")

    if segments and not args.no_stutter:
        print(f"\n③ 더듬거림/재시작 구간 감지 중...")
        cuts = find_stutter_cuts(segments, STUTTER_GAP, STUTTER_OVERLAP)
        if cuts:
            print(f"  → {len(cuts)}개 더듬거림 구간 발견:")
            for cs, ce in cuts:
                print(f"     {cs:.2f}s ~ {ce:.2f}s")
            keep = remove_cuts_from_keep(keep, cuts)
        else:
            print("  → 더듬거림 구간 없음")

    # ④ 자막 SRT 생성
    if segments and not args.no_subtitles:
        print(f"\n④ 자막(SRT) 생성 중...")
        segments_to_srt(segments, srt_path)

    # ⑤ 영상 출력
    print(f"\n⑤ 편집 영상 생성 중...")
    embed_srt = srt_path if (segments and not args.no_subtitles) else None
    cut_and_concat(input_path, keep, output_path, embed_srt)

    final_dur = get_duration(output_path)
    print(f"\n✅ 완료!")
    print(f"   원본: {duration:.1f}초  →  편집본: {final_dur:.1f}초 (약 {duration-final_dur:.1f}초 단축)")
    print(f"   저장: {output_path}")
    if embed_srt:
        print(f"   자막: {srt_path}")


if __name__ == "__main__":
    main()
