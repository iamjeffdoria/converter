import os
import uuid
import subprocess
import threading
import time
import json
import math
import psutil
from pathlib import Path
from django.shortcuts import render, redirect
from django.conf import settings
from django.http import JsonResponse, FileResponse, Http404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import datetime
from collections import defaultdict, Counter
import time as _time
from . import models
from .models import JobRecord, Visitor, UserAccount
from django.db.models import F
import hmac
import hashlib
import requests as http_requests
from django.utils import timezone
import base64



# ── GLOBAL STATE ──────────────────────────────────────────────────────────────
JOBS      = {}
JOBS_LOCK = threading.Lock()

_CPU_THREADS = os.cpu_count() or 4

# Limits concurrent ffmpeg processes to avoid OOM under heavy load
_MAX_CONCURRENT  = max(1, math.floor(_CPU_THREADS / 2))
_CONVERSION_SEM  = threading.Semaphore(_MAX_CONCURRENT)

# How often (seconds) the stderr loop flushes progress to JOBS dict.
_PROGRESS_FLUSH_INTERVAL = 1.0  # increased from 0.5 — safer under load

# Jobs older than this (seconds) are reaped by the background thread.
_JOB_TTL = 3600  # 1 hour

# Max jobs allowed in queue+converting state before rejecting new uploads
_MAX_QUEUE = 20

# Supported formats
SUPPORTED_INPUT = {'.mkv', '.avi', '.mov', '.webm', '.mp4', '.flv', '.wmv', '.ts', '.m4v', '.3gp'}
SUPPORTED_OUTPUT = {
    'mp4':  {'ext': '.mp4',  'mime': 'video/mp4',        'vcodec': 'libx264',    'acodec': 'aac'},
    'mkv':  {'ext': '.mkv',  'mime': 'video/x-matroska', 'vcodec': 'libx264',    'acodec': 'aac'},
    'avi':  {'ext': '.avi',  'mime': 'video/x-msvideo',  'vcodec': 'libx264',    'acodec': 'mp3'},
    'mov':  {'ext': '.mov',  'mime': 'video/quicktime',  'vcodec': 'libx264',    'acodec': 'aac'},
    'webm': {'ext': '.webm', 'mime': 'video/webm',       'vcodec': 'libvpx-vp9', 'acodec': 'libopus'},
    'flv':  {'ext': '.flv',  'mime': 'video/x-flv',      'vcodec': 'libx264',    'acodec': 'aac'},
    'wmv':  {'ext': '.wmv',  'mime': 'video/x-ms-wmv',   'vcodec': 'wmv2',       'acodec': 'wmav2'},
    'ts':   {'ext': '.ts',   'mime': 'video/mp2t',        'vcodec': 'libx264',    'acodec': 'aac'},
    'm4v':  {'ext': '.m4v',  'mime': 'video/x-m4v',      'vcodec': 'libx264',    'acodec': 'aac'},
    '3gp':  {'ext': '.3gp',  'mime': 'video/3gpp',        'vcodec': 'libx264',    'acodec': 'aac'},
}

# Per-job process handles and control events
JOB_PROCS  = {}   # job_id -> subprocess.Popen
JOB_PAUSE  = {}   # job_id -> threading.Event  (set=running, clear=paused)
JOB_CANCEL = {}   # job_id -> threading.Event  (set=cancel requested)


# ── BACKGROUND REAPER ─────────────────────────────────────────────────────────
def _reaper():
    while True:
        time.sleep(300)
        now = time.time()
        to_delete = []
        with JOBS_LOCK:
            for jid, job in JOBS.items():
                age = now - job.get('created_at', now)
                if age > _JOB_TTL and job['status'] in ('done', 'error', 'cancelled'):
                    to_delete.append(jid)
        for jid in to_delete:
            _cleanup_job_files(jid)
            with JOBS_LOCK:
                JOBS.pop(jid, None)
                JOB_PAUSE.pop(jid, None)
                JOB_CANCEL.pop(jid, None)
                JOB_PROCS.pop(jid, None)

_reaper_thread = threading.Thread(target=_reaper, daemon=True)
_reaper_thread.start()


# ── VIEWS ─────────────────────────────────────────────────────────────────────
def index(request):
    visitor_id = request.COOKIES.get('vc_visitor_id', '')
    credits = 0
    free_remaining = 0
    if visitor_id:
        try:
            account = models.UserAccount.objects.get(visitor_id=visitor_id)
            credits = account.credits
            free_remaining = account.get_free_remaining()
        except models.UserAccount.DoesNotExist:
            pass
    response = render(request, 'converter/index.html', {
        'credits': credits,
        'free_remaining': free_remaining,
    })
    return _track_visitor(request, response)

def active_job(request, job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return JsonResponse({'error': 'not found'}, status=404)
    return JsonResponse({
        'status':        job['status'],
        'progress':      job['progress'],
        'strategy':      job.get('strategy', ''),
        'speed':         job.get('speed', ''),
        'eta':           job.get('eta', ''),
        'filename':      job.get('filename', ''),
        'input_name':    job.get('input_name', ''),
        'output_format': job.get('output_format', ''),
        'file_size':     _human_size(job.get('file_size', 0)) if job['status'] == 'done' else '',
        'error':         job.get('error'),
    })


@csrf_exempt
@require_POST
def upload(request):
    file          = request.FILES.get('file')
    output_format = request.POST.get('output_format', 'mp4').lower().strip('.')

    if not file:
        return JsonResponse({'error': 'No file provided.'}, status=400)

    input_ext = Path(file.name).suffix.lower()
    if input_ext not in SUPPORTED_INPUT:
        return JsonResponse({'error': f'Unsupported format: {input_ext}'}, status=400)

    if output_format not in SUPPORTED_OUTPUT:
        return JsonResponse({'error': f'Unsupported output format: {output_format}'}, status=400)

    # ── TIER CHECK ────────────────────────────────────────────────────────────────    
    visitor_id = request.COOKIES.get('vc_visitor_id', '')
    if not visitor_id:
        return JsonResponse({'error': 'Session not found. Please refresh and try again.'}, status=400)

    account, _ = UserAccount.objects.get_or_create(visitor_id=visitor_id)
    allowed, reason, is_paid = account.can_convert(file.size)

    if not allowed:
        return JsonResponse({'error': reason}, status=403)

    # ── MODIFIED: reject early if queue is full ───────────────────────────────
    with JOBS_LOCK:
        queued_or_converting = sum(
            1 for j in JOBS.values()
            if j['status'] in ('queued', 'converting')
        )
    if queued_or_converting >= _MAX_QUEUE:
        return JsonResponse(
            {'error': 'Server busy. Please try again in a few minutes.'},
            status=503
        )

    job_id     = uuid.uuid4().hex
    upload_dir = Path(settings.MEDIA_ROOT) / 'uploads'
    output_dir = Path(settings.MEDIA_ROOT) / 'converted'
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    out_ext     = SUPPORTED_OUTPUT[output_format]['ext']
    input_path  = upload_dir / f'{job_id}{input_ext}'
    output_path = output_dir / f'{job_id}{out_ext}'

    resolution = request.POST.get('resolution', 'original')
    quality    = request.POST.get('quality', 'auto')
    codec_pref = request.POST.get('codec', 'auto')

    CHUNK = 4 * 1024 * 1024
    with open(input_path, 'wb') as f:
        for chunk in file.chunks(chunk_size=CHUNK):
            f.write(chunk)

    pause_event  = threading.Event()
    cancel_event = threading.Event()
    pause_event.set()

    with JOBS_LOCK:
        JOBS[job_id] = {
            'status':        'queued',
            'progress':      0,
            'strategy':      'Waiting for slot…',
            'speed':         '',
            'eta':           '',
            'input':         str(input_path),
            'output':        str(output_path),
            'output_format': output_format,
            'filename':      Path(file.name).stem + out_ext,
            'input_name':    file.name,
            'error':         None,
            'created_at':    time.time(),
            'resolution':    resolution,
            'quality':       quality,
            'codec_pref':    codec_pref,
        }
        JOB_PAUSE[job_id]  = pause_event
        JOB_CANCEL[job_id] = cancel_event

    thread = threading.Thread(target=_convert, args=(job_id,), daemon=True)
    # Deduct credit or free usage
    if is_paid:
        cost = 1  # flat 1 credit regardless of size
        UserAccount.objects.filter(visitor_id=visitor_id).update(
            credits=F('credits') - cost
        )
    else:
        UserAccount.objects.filter(visitor_id=visitor_id).update(
            free_used_month=F('free_used_month') + 1
        )
    thread.start()

    return JsonResponse({'job_id': job_id})


@csrf_exempt
def pause_job(request, job_id: str):
    with JOBS_LOCK:
        job         = JOBS.get(job_id)
        pause_event = JOB_PAUSE.get(job_id)
        proc        = JOB_PROCS.get(job_id)

    if not job or not pause_event:
        return JsonResponse({'error': 'Job not found.'}, status=404)

    if job['status'] not in ('converting', 'paused'):
        return JsonResponse({'error': 'Job not pausable.'}, status=400)

    if pause_event.is_set():
        pause_event.clear()
        if proc:
            try:
                p = psutil.Process(proc.pid)
                p.suspend()
                for child in p.children(recursive=True):
                    try: child.suspend()
                    except Exception: pass
            except Exception:
                pass
        with JOBS_LOCK:
            JOBS[job_id]['status'] = 'paused'
        return JsonResponse({'paused': True})
    else:
        if proc:
            try:
                p = psutil.Process(proc.pid)
                for child in p.children(recursive=True):
                    try: child.resume()
                    except Exception: pass
                p.resume()
            except Exception:
                pass
        with JOBS_LOCK:
            JOBS[job_id]['status'] = 'converting'
        pause_event.set()
        return JsonResponse({'paused': False})

@csrf_exempt
def cancel_job(request, job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return JsonResponse({'error': 'Job not found.'}, status=404)
        cancel_event = JOB_CANCEL.get(job_id)
        pause_event  = JOB_PAUSE.get(job_id)

    if cancel_event:
        cancel_event.set()
    if pause_event:
        pause_event.set()

    proc = JOB_PROCS.get(job_id)
    if proc:
        try: proc.kill()
        except Exception: pass

    with JOBS_LOCK:
        JOBS[job_id]['status'] = 'cancelled'
        job_snapshot = dict(JOBS[job_id])  # snapshot after status update

    # ── ADD THIS ──────────────────────────────────────────────────────────
    _save_job_record(job_id, job_snapshot, 'cancelled', 0)

    _cleanup_job_files(job_id)
    return JsonResponse({'cancelled': True})


# ── PROBE ─────────────────────────────────────────────────────────────────────
def _probe_video(input_path: str) -> dict:
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams', '-show_format',
            input_path
        ], capture_output=True, text=True, timeout=10)

        data = json.loads(result.stdout)
        info = {'vcodec': None, 'acodec': None, 'duration': None}

        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'video' and not info['vcodec']:
                info['vcodec'] = stream.get('codec_name')
            if stream.get('codec_type') == 'audio' and not info['acodec']:
                info['acodec'] = stream.get('codec_name')

        duration = data.get('format', {}).get('duration')
        if duration:
            info['duration'] = float(duration)

        return info
    except Exception:
        return {'vcodec': None, 'acodec': None, 'duration': None}

def _save_job_record(job_id: str, job: dict, status: str, file_size: int):
    """Persist a completed/failed/cancelled job to SQLite."""
    try:
        inp     = job.get('input', '')
        inp_ext = Path(inp).suffix.lower().strip('.') if inp else '?'
        JobRecord.objects.update_or_create(
            job_id=job_id,
            defaults={
                'input_name':    job.get('input_name', 'unknown'),
                'input_ext':     inp_ext,
                'output_format': job.get('output_format', '?'),
                'strategy':      job.get('strategy', ''),
                'status':        status,
                'file_size':     file_size,
                'created_at':    job.get('created_at', _time.time()),
                'completed_at':  _time.time(),
            }
        )
    except Exception:
        pass  # never crash the conversion thread over analytics

# ── CONVERSION THREAD ─────────────────────────────────────────────────────────
def _convert(job_id: str):
    # ── MODIFIED: jobs queue here until a semaphore slot is free ─────────────
    with JOBS_LOCK:
        JOBS[job_id]['strategy'] = 'Waiting for slot…'

    with _CONVERSION_SEM:
        with JOBS_LOCK:
            job = JOBS[job_id]
            JOBS[job_id]['status'] = 'converting'

        input_path  = job['input']
        output_path = job['output']
        out_fmt     = job['output_format']
        fmt         = SUPPORTED_OUTPUT[out_fmt]
        acodec      = fmt['acodec']
        vcodec      = fmt['vcodec']

        def set_strategy(label: str):
            with JOBS_LOCK:
                JOBS[job_id]['strategy'] = label

        def clean_output():
            if os.path.exists(output_path):
                os.remove(output_path)

        def is_cancelled() -> bool:
            ev = JOB_CANCEL.get(job_id)
            return ev.is_set() if ev else False

        set_strategy('Probing file…')

        probe     = _probe_video(input_path)
        vcodec_in = probe.get('vcodec') or ''
        acodec_in = probe.get('acodec') or ''

        if probe.get('duration'):
            with JOBS_LOCK:
                JOBS[job_id]['duration'] = probe['duration']

        if is_cancelled():
            return

        H264_COMPAT = {'h264', 'avc', 'avc1'}
        HEVC_COMPAT = {'mkv', 'ts', 'mov'}
        AUDIO_SAFE  = {'aac', 'mp3', 'opus', 'ac3', 'eac3', 'flac', 'vorbis'}

        input_ext      = Path(input_path).suffix.lower().strip('.')
        can_copy_video = (
            vcodec_in in H264_COMPAT or
            (vcodec_in in {'hevc', 'h265'} and out_fmt in HEVC_COMPAT) or
            input_ext == out_fmt
        )
        can_copy_audio = acodec_in in AUDIO_SAFE

        # Strategy 1: Full stream copy
        if can_copy_video and can_copy_audio:
            set_strategy('⚡ Stream copy — instant remux')
            success = _run_ffmpeg(job_id, [
                'ffmpeg', '-y', '-i', input_path,
                '-c', 'copy', '-movflags', '+faststart', output_path,
            ])
        else:
            success = False

        if is_cancelled():
            return

        # Strategy 2: Copy video, re-encode audio
        if not success and can_copy_video and not can_copy_audio:
            clean_output()
            set_strategy('⚡ Video copy + audio re-encode')
            success = _run_ffmpeg(job_id, [
                'ffmpeg', '-y', '-i', input_path,
                '-c:v', 'copy', '-c:a', acodec, '-b:a', '192k',
                '-movflags', '+faststart', output_path,
            ])

        if is_cancelled():
            return

        # Strategy 3: NVENC GPU
        if not success and vcodec == 'libx264':
            clean_output()
            resolution = job.get('resolution', 'original')
            quality    = job.get('quality', 'auto')
            crf_map    = {'high': '16', 'auto': '18', 'medium': '23', 'small': '28'}
            cq         = str(int(crf_map.get(quality, '18')) + 1)

            nvenc_cmd = ['ffmpeg', '-y', '-i', input_path]
            if resolution != 'original':
                w, h = resolution.split('x')
                nvenc_cmd += ['-vf', f'scale={w}:{h}:force_original_aspect_ratio=decrease']
            nvenc_cmd += [
                '-c:v', 'h264_nvenc', '-preset', 'p1', '-tune', 'hq',
                '-rc', 'vbr', '-cq', cq, '-b:v', '0',
                '-c:a', acodec, '-b:a', '192k',
                '-movflags', '+faststart', output_path
            ]
            set_strategy('🚀 GPU encode via NVENC')
            success = _run_ffmpeg(job_id, nvenc_cmd)

        if is_cancelled():
            return

        # ── MODIFIED: Strategy 4 — divide CPU threads among active jobs ───────
        if not success:
            clean_output()

            with JOBS_LOCK:
                active_count = max(1, sum(
                    1 for j in JOBS.values()
                    if j['status'] == 'converting'
                ))
            threads_per_job = max(1, _CPU_THREADS // active_count)

            set_strategy(f'💻 CPU encode — {threads_per_job} threads ultrafast')

            extra = []
            if out_fmt in ('mp4', 'm4v', 'mov', 'ts'):
                extra = ['-movflags', '+faststart']
            elif out_fmt == 'webm':
                extra = ['-deadline', 'realtime', '-cpu-used', '8']
            elif out_fmt == 'avi':
                extra = ['-vtag', 'xvid']

            resolution = job.get('resolution', 'original')
            quality    = job.get('quality', 'auto')
            codec_pref = job.get('codec_pref', 'auto')

            if codec_pref == 'h265' and out_fmt in ('mp4', 'mkv', 'mov', 'ts'):
                vcodec = 'libx265'
            elif codec_pref == 'h264':
                vcodec = 'libx264'

            crf_map = {'high': '16', 'auto': '18', 'medium': '23', 'small': '28'}
            crf     = crf_map.get(quality, '18')

            cmd = ['ffmpeg', '-y', '-threads', str(threads_per_job), '-i', input_path]

            if resolution != 'original':
                w, h = resolution.split('x')
                cmd += ['-vf', f'scale={w}:{h}:force_original_aspect_ratio=decrease']

            cmd += ['-c:v', vcodec]
            if vcodec == 'libx264':
                cmd += [
                    '-preset', 'ultrafast', '-crf', crf,
                    '-threads', str(threads_per_job),
                    '-x264-params', f'threads={threads_per_job}',  # MODIFIED
                ]
            elif vcodec == 'libx265':
                cmd += ['-preset', 'ultrafast', '-crf', crf]
            elif vcodec == 'libvpx-vp9':
                cmd += ['-b:v', '2M', '-cpu-used', '8', '-row-mt', '1',
                        '-threads', str(threads_per_job)]  # MODIFIED
            elif vcodec == 'wmv2':
                cmd += ['-b:v', '2M']

            cmd += ['-c:a', acodec, '-b:a', '192k', *extra, output_path]
            success = _run_ffmpeg(job_id, cmd)

        try:
            os.remove(input_path)
        except OSError:
            pass

        if is_cancelled():
            return

       
        # AFTER:
        if success:
            file_size = os.path.getsize(output_path)
            with JOBS_LOCK:
                JOBS[job_id].update({
                    'status':    'done',
                    'progress':  100,
                    'speed':     '',
                    'eta':       '',
                    'file_size': file_size,
                })
                _save_job_record(job_id, JOBS[job_id], 'done', file_size)
        else:
            with JOBS_LOCK:
                JOBS[job_id]['status'] = 'error'
                _save_job_record(job_id, JOBS[job_id], 'error', 0)


# ── FFMPEG RUNNER ─────────────────────────────────────────────────────────────
def _run_ffmpeg(job_id: str, cmd: list) -> bool:
    with JOBS_LOCK:
        duration   = JOBS[job_id].get('duration')
        input_path = JOBS[job_id]['input']

    if not duration:
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', input_path],
                capture_output=True, text=True, timeout=10
            )
            duration = float(result.stdout.strip())
            with JOBS_LOCK:
                JOBS[job_id]['duration'] = duration
        except Exception:
            duration = None

    try:
        proc = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1,
        )

        with JOBS_LOCK:
            JOB_PROCS[job_id] = proc

        last_flush    = time.monotonic()
        last_progress = 0
        last_speed    = ''
        last_eta      = ''
        last_error    = None

        cancel_event = JOB_CANCEL.get(job_id)

        for line in proc.stderr:
            if cancel_event and cancel_event.is_set():
                proc.kill()
                return False

            if 'time=' in line:
                try:
                    parts = {}
                    for token in line.strip().split():
                        if '=' in token:
                            k, v = token.split('=', 1)
                            parts[k.strip()] = v.strip()

                    if 'time' in parts and duration:
                        current       = _parse_time(parts['time'])
                        last_progress = min(int((current / duration) * 100), 99)
                        speed_str     = parts.get('speed', '').replace('x', '')
                        last_speed    = parts.get('speed', '')
                        try:
                            if speed_str and float(speed_str) > 0:
                                last_eta = _fmt_eta((duration - current) / float(speed_str))
                        except (ValueError, ZeroDivisionError):
                            last_eta = ''
                except (IndexError, ValueError):
                    pass

            elif 'error' in line.lower() and 'non monotonous' not in line.lower():
                last_error = line.strip()

            now = time.monotonic()
            if now - last_flush >= _PROGRESS_FLUSH_INTERVAL:
                with JOBS_LOCK:
                    JOBS[job_id]['progress'] = last_progress
                    JOBS[job_id]['speed']    = last_speed
                    JOBS[job_id]['eta']      = last_eta
                    if last_error:
                        JOBS[job_id]['error'] = last_error
                last_flush = now

        with JOBS_LOCK:
            JOBS[job_id]['progress'] = last_progress
            JOBS[job_id]['speed']    = last_speed
            JOBS[job_id]['eta']      = last_eta
            if last_error:
                JOBS[job_id]['error'] = last_error

        proc.wait()

        with JOBS_LOCK:
            JOB_PROCS.pop(job_id, None)

        return proc.returncode == 0

    except Exception as e:
        with JOBS_LOCK:
            JOBS[job_id]['error'] = str(e)
            JOB_PROCS.pop(job_id, None)
        return False


# ── STATUS & DOWNLOAD ─────────────────────────────────────────────────────────
def status(request, job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return JsonResponse({'error': 'Job not found.'}, status=404)

    response = {
        'status':   job['status'],
        'progress': job['progress'],
        'strategy': job.get('strategy', ''),
        'speed':    job.get('speed', ''),
        'eta':      job.get('eta', ''),
        'error':    job.get('error'),
        'filename': job.get('filename'),
    }
    if job['status'] == 'done':
        response['file_size'] = _human_size(job.get('file_size', 0))
    return JsonResponse(response)


def download(request, job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or job['status'] != 'done':
        raise Http404
    if not os.path.exists(job['output']):
        raise Http404
    mime = SUPPORTED_OUTPUT.get(job['output_format'], {}).get('mime', 'video/mp4')
    return FileResponse(
        open(job['output'], 'rb'),
        content_type=mime,
        as_attachment=True,
        filename=job['filename'],
    )


@csrf_exempt
def cleanup(request, job_id: str):
    _cleanup_job_files(job_id)
    with JOBS_LOCK:
        JOBS.pop(job_id, None)
        JOB_PAUSE.pop(job_id, None)
        JOB_CANCEL.pop(job_id, None)
        JOB_PROCS.pop(job_id, None)
    return JsonResponse({'ok': True})


# ── HELPERS ───────────────────────────────────────────────────────────────────
def _cleanup_job_files(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job:
        for key in ('input', 'output'):
            try:
                p = job.get(key)
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


def _parse_time(time_str: str) -> float:
    try:
        parts = time_str.split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except Exception:
        return 0.0


def _fmt_eta(seconds: float) -> str:
    s = int(seconds)
    if s < 60:   return f'{s}s'
    if s < 3600: return f'{s // 60}m {s % 60}s'
    return f'{s // 3600}h {(s % 3600) // 60}m'


def _human_size(size: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024:
            return f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


# ── ANALYTICS ─────────────────────────────────────────────────────────────────
def analytics_login(request):
    if request.session.get('analytics_authed'):
        return redirect('analytics')

    error = None

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        if (username == settings.ANALYTICS_USERNAME and
                password == settings.ANALYTICS_PASSWORD):
            request.session['analytics_authed'] = True
            request.session.set_expiry(86400)
            return redirect('analytics')
        else:
            error = 'Invalid credentials.'

    return render(request, 'converter/analytics_login.html', {'error': error})


def analytics_logout(request):
    request.session.flush()
    return redirect('analytics_login')


def analytics_dashboard(request):
    if not request.session.get('analytics_authed'):
        return redirect('analytics_login')
    return render(request, 'converter/analytics_dashboard.html')

def analytics_api(request):
    if not request.session.get('analytics_authed'):
        return JsonResponse({'error': 'Unauthorised'}, status=401)

    now = time.time()

    # ── Live in-memory jobs (current session) ─────────────────────────────
    with JOBS_LOCK:
        live_jobs = list(JOBS.values())

    # AFTER:
    live_ids = {j.get('job_id') for j in live_jobs if j.get('job_id')}

    # Also exclude any DB record whose job_id appears in JOBS keys directly
    with JOBS_LOCK:
        all_live_ids = set(JOBS.keys()) | live_ids

    db_jobs = []
    for rec in JobRecord.objects.all():
        if rec.job_id not in all_live_ids:
            db_jobs.append({
                'job_id':        rec.job_id,
                'input_name':    rec.input_name,
                'input':         f'_.{rec.input_ext}',
                'output_format': rec.output_format,
                'strategy':      rec.strategy,
                'status':        rec.status,
                'file_size':     rec.file_size,
                'created_at':    rec.created_at,
                'progress':      100 if rec.status == 'done' else 0,
            })

    jobs_snapshot = live_jobs + db_jobs

    # ── Counters ──────────────────────────────────────────────────────────
    total_jobs     = len(jobs_snapshot)
    done_jobs      = [j for j in jobs_snapshot if j['status'] == 'done']
    error_jobs     = [j for j in jobs_snapshot if j['status'] == 'error']
    cancelled_jobs = [j for j in jobs_snapshot if j['status'] == 'cancelled']
    active_jobs    = [j for j in jobs_snapshot if j['status'] in ('converting', 'queued', 'paused')]

    success_rate = round(len(done_jobs) / total_jobs * 100, 1) if total_jobs else 0.0

    # ── Data volume ───────────────────────────────────────────────────────
    total_bytes_out = sum(j.get('file_size', 0) for j in done_jobs)

    # ── Format breakdown ──────────────────────────────────────────────────
    input_ext_counter  = Counter()
    output_fmt_counter = Counter()
    for j in jobs_snapshot:
        inp = j.get('input', '')
        if inp:
            input_ext_counter[Path(inp).suffix.lower().strip('.')] += 1
        out = j.get('output_format', '')
        if out:
            output_fmt_counter[out.lower()] += 1

    # ── Strategy breakdown ────────────────────────────────────────────────
    strategy_counter = Counter()
    for j in done_jobs:
        raw = j.get('strategy', '')
        if 'Stream copy' in raw:
            strategy_counter['Stream copy'] += 1
        elif 'audio re-encode' in raw or 'audio re-enc' in raw:
            strategy_counter['Copy + audio re-enc'] += 1
        elif 'GPU' in raw or 'NVENC' in raw:
            strategy_counter['GPU (NVENC)'] += 1
        else:
            strategy_counter['CPU encode'] += 1

    # ── Hourly distribution (last 24 h) ───────────────────────────────────
    hourly = [0] * 24
    cutoff_24h = now - 86400
    for j in done_jobs:
        ts = j.get('created_at', 0)
        if ts >= cutoff_24h:
            h = datetime.datetime.fromtimestamp(ts).hour
            hourly[h] += 1

    # ── Real heatmap: 7×24 grid ───────────────────────────────────────────
    heatmap_grid = [[0] * 24 for _ in range(7)]
    for j in done_jobs:
        ts = j.get('created_at', 0)
        dt = datetime.datetime.fromtimestamp(ts)
        heatmap_grid[dt.weekday()][dt.hour] += 1

    # ── Daily trend (last 30 days) ────────────────────────────────────────
    daily_convs = defaultdict(int)
    for j in done_jobs:
        ts = j.get('created_at', 0)
        day_str = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
        daily_convs[day_str] += 1

    today = datetime.date.today()
    trend_labels, trend_convs = [], []
    for i in range(29, -1, -1):
        d = today - datetime.timedelta(days=i)
        trend_labels.append(d.strftime('%b %d'))
        trend_convs.append(daily_convs.get(d.isoformat(), 0))

    # ── Recent jobs (last 20, newest first) ───────────────────────────────
    recent = sorted(jobs_snapshot, key=lambda j: j.get('created_at', 0), reverse=True)[:20]
    recent_out = []
    for j in recent:
        inp     = j.get('input', '')
        inp_ext = Path(inp).suffix.lower().strip('.') if inp else '?'
        age_s   = int(now - j.get('created_at', now))
        if age_s < 60:       when = f'{age_s}s ago'
        elif age_s < 3600:   when = f'{age_s // 60}m ago'
        elif age_s < 86400:  when = f'{age_s // 3600}h ago'
        else:                when = f'{age_s // 86400}d ago'

        recent_out.append({
            'name':     j.get('input_name', 'unknown'),
            'inFmt':    inp_ext,
            'outFmt':   j.get('output_format', '?'),
            'size':     _human_size(j.get('file_size', 0)) if j['status'] == 'done' else '—',
            'strategy': j.get('strategy', '—'),
            'status':   j['status'],
            'progress': j.get('progress', 0),
            'when':     when,
        })
    # ── Visitor retention ──────────────────────────────────────────────────────
    total_visitors   = Visitor.objects.count()
    returning        = Visitor.objects.filter(visit_count__gt=1).count()
    new_visitors     = total_visitors - returning
    retention_rate   = round(returning / total_visitors * 100, 1) if total_visitors else 0.0

    # Visitors seen in last 30 days
    cutoff_30d = now - (30 * 86400)
    active_30d = Visitor.objects.filter(last_seen__gte=cutoff_30d).count()

    # Daily new visitors trend (last 14 days)
    daily_new = defaultdict(int)
    for v in Visitor.objects.filter(first_seen__gte=now - 14 * 86400):
        day = datetime.datetime.fromtimestamp(v.first_seen).strftime('%Y-%m-%d')
        daily_new[day] += 1

    # ── Build response ────────────────────────────────────────────────────
    return JsonResponse({
        # KPIs
        'totalJobs':      total_jobs,
        'totalDone':      len(done_jobs),
        'totalErrors':    len(error_jobs),
        'totalCancelled': len(cancelled_jobs),
        'activeJobs':     len(active_jobs),
        'successRate':    success_rate,
        'dataBytesOut':   total_bytes_out,
        'dataHuman':      _human_size(total_bytes_out),

        # Charts
        'trendLabels':    trend_labels[-14:],
        'trendConvs':     trend_convs[-14:],
        'hourly':         hourly,
        'heatmapGrid':    heatmap_grid,

        # Breakdowns
        'inputFormats':  [{'name': k.upper(), 'val': v} for k, v in input_ext_counter.most_common(6)],
        'outputFormats': [{'name': k.upper(), 'val': v} for k, v in output_fmt_counter.most_common(6)],
        'strategies':    [{'name': k, 'val': v} for k, v in strategy_counter.most_common()],

        # Recent jobs
        'recentJobs':     recent_out,

        # Server info
        'serverTime':     datetime.datetime.now().isoformat(),
        'ffmpegActive':   len(active_jobs),
        'maxConcurrent':  _MAX_CONCURRENT,
        'maxQueue':       _MAX_QUEUE,

        # Retention
        'totalVisitors':   total_visitors,
        'returningVisitors': returning,
        'newVisitors':     new_visitors,
        'retentionRate':   retention_rate,
        'activeVisitors30d': active_30d,
    })


VISITOR_COOKIE = 'vc_visitor_id'
VISITOR_COOKIE_AGE = 365 * 24 * 60 * 60  # 1 year

def _track_visitor(request, response):
    """
    Assigns a visitor ID cookie on first visit and upserts the Visitor record.
    Call this at the end of any page-rendering view.
    """
    visitor_id = request.COOKIES.get(VISITOR_COOKIE)
    is_new = False

    if not visitor_id:
        visitor_id = uuid.uuid4().hex
        is_new = True
        response.set_cookie(
            VISITOR_COOKIE,
            visitor_id,
            max_age=VISITOR_COOKIE_AGE,
            httponly=True,
            samesite='Lax',
        )

    now = _time.time()
    try:
        if is_new:
            Visitor.objects.create(
                visitor_id=visitor_id,
                first_seen=now,
                last_seen=now,
                visit_count=1,
            )
        else:
            Visitor.objects.filter(visitor_id=visitor_id).update(
                last_seen=now,
                visit_count=F('visit_count') + 1,
            )
    except Exception:
        pass  # never crash a page visit over analytics

    return response

def pricing(request):
    return render(request, 'converter/pricing.html')


# ── PAYMENT ───────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def payment_create(request):
    """
    Called by the frontend when user clicks 'Proceed to Payment'.
    Creates a PayMongo Source and returns the checkout URL.
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid request body.'}, status=400)

    package_key = body.get('package', '')
    method      = body.get('method', 'gcash')   # 'gcash' or 'maya' or 'card'

    pack = settings.CREDIT_PACKS.get(package_key)
    if not pack:
        return JsonResponse({'error': 'Invalid package.'}, status=400)

    visitor_id = request.COOKIES.get('vc_visitor_id', '')
    if not visitor_id:
        return JsonResponse({'error': 'Session not found. Refresh and try again.'}, status=400)

    # PayMongo Source type mapping
    # QR Ph covers both GCash and Maya on PayMongo
    source_type_map = {
        'gcash': 'gcash',
        'maya':  'paymaya',
        'card':  'gcash',   # fallback — for card you'd use a Link or PaymentIntent instead
    }
    source_type = source_type_map.get(method, 'gcash')

    # Create a pending order record first
    order = models.CreditOrder.objects.create(
        visitor_id      = visitor_id,
        package_key     = package_key,
        credits         = pack['credits'],
        amount_centavos = pack['amount'],
        status          = 'pending',
    )

    # Build the PayMongo API request
    auth_string = base64.b64encode(
        f"{settings.PAYMONGO_SECRET_KEY}:".encode()
    ).decode()

    success_url = request.build_absolute_uri(f'/payment/success/?order_id={order.id}')
    failed_url  = request.build_absolute_uri(f'/payment/failed/?order_id={order.id}')

    payload = {
        'data': {
            'attributes': {
                'amount':      pack['amount'],   # in centavos
                'currency':    'PHP',
                'type':        source_type,
                'description': f"{pack['name']} — Video Converter",
                'redirect': {
                    'success': success_url,
                    'failed':  failed_url,
                }
            }
        }
    }

    try:
        resp = http_requests.post(
            'https://api.paymongo.com/v1/sources',
            json=payload,
            headers={
                'Authorization': f'Basic {auth_string}',
                'Content-Type':  'application/json',
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except http_requests.RequestException as e:
        order.status = 'failed'
        order.save(update_fields=['status'])
        return JsonResponse({'error': f'PayMongo error: {str(e)}'}, status=502)

    source_id   = data['data']['id']
    checkout_url = data['data']['attributes']['redirect']['checkout_url']

    # Save the PayMongo source ID to the order
    order.paymongo_source_id = source_id
    order.save(update_fields=['paymongo_source_id'])

    return JsonResponse({'checkout_url': checkout_url})

def payment_success(request):
    order_id = request.GET.get('order_id', '')
    return redirect('/pricing/?payment=success')

def payment_failed(request):
    order_id = request.GET.get('order_id', '')
    try:
        order = models.CreditOrder.objects.get(id=order_id)
        order.status = 'failed'
        order.save(update_fields=['status'])
    except models.CreditOrder.DoesNotExist:
        pass
    # Redirect back to pricing with a flag
    return redirect('/pricing/?payment=failed')


@csrf_exempt
def payment_webhook(request):
    """
    PayMongo posts payment events here.
    This is where you ACTUALLY credit the account after payment confirmation.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    # ── Verify webhook signature ──────────────────────────────────────────
    raw_body  = request.body
    signature = request.headers.get('Paymongo-Signature', '')

    if not _verify_webhook_signature(raw_body, signature):
        return JsonResponse({'error': 'Invalid signature.'}, status=400)

    # ── Parse event ───────────────────────────────────────────────────────
    try:
        event = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)

    event_type = event.get('data', {}).get('attributes', {}).get('type', '')

    # We only care about source.chargeable (QR Ph / GCash / Maya)
    if event_type == 'source.chargeable':
        source_data = event['data']['attributes']['data']
        source_id   = source_data['id']
        amount      = source_data['attributes']['amount']

        # Find the pending order
        try:
            order = models.CreditOrder.objects.get(
                paymongo_source_id=source_id,
                status='pending',
            )
        except models.CreditOrder.DoesNotExist:
            # Already processed or unknown — return 200 so PayMongo doesn't retry
            return JsonResponse({'received': True})

        # ── Create a PayMongo Payment to capture the charge ──────────────
        auth_string = base64.b64encode(
            f"{settings.PAYMONGO_SECRET_KEY}:".encode()
        ).decode()

        pay_payload = {
            'data': {
                'attributes': {
                    'amount':      amount,
                    'currency':    'PHP',
                    'description': f"Order #{order.id} — {order.package_key}",
                    'source': {
                        'id':   source_id,
                        'type': 'source',
                    }
                }
            }
        }

        try:
            pay_resp = http_requests.post(
                'https://api.paymongo.com/v1/payments',
                json=pay_payload,
                headers={
                    'Authorization': f'Basic {auth_string}',
                    'Content-Type':  'application/json',
                },
                timeout=15,
            )
            pay_resp.raise_for_status()
        except http_requests.RequestException:
            # Don't mark as failed — PayMongo will retry the webhook
            return JsonResponse({'error': 'Could not create payment.'}, status=500)

        # ── Credit the user's account ─────────────────────────────────────
        from django.db.models import F as _F
        models.UserAccount.objects.filter(
            visitor_id=order.visitor_id
        ).update(credits=_F('credits') + order.credits)

        # Mark order as paid
        order.status  = 'paid'
        order.paid_at = timezone.now()
        order.save(update_fields=['status', 'paid_at'])

    return JsonResponse({'received': True})


def _verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    PayMongo webhook signature format:
    t=<timestamp>,te=<test_sig>,li=<live_sig>
    """
    if not signature_header or not settings.PAYMONGO_WEBHOOK_SECRET:
        return False

    try:
        parts = {}
        for chunk in signature_header.split(','):
            k, v = chunk.split('=', 1)
            parts[k.strip()] = v.strip()

        timestamp = parts.get('t', '')
        signature = parts.get('li', '') or parts.get('te', '')

        message        = f"{timestamp}.{raw_body.decode('utf-8')}"
        expected       = hmac.new(
            settings.PAYMONGO_WEBHOOK_SECRET.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)
    except Exception:
        return False