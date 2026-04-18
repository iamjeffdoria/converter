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
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
import subprocess
from django.http import FileResponse, Http404

from django.http import HttpResponse
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

# Max concurrent jobs a single user can have active at once
_MAX_JOBS_PER_USER = 2

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
    credits = 0
    free_remaining = 0
    if request.user.is_authenticated:
        account, _ = UserAccount.objects.get_or_create(
            user=request.user,
            defaults={'visitor_id': request.COOKIES.get('vc_visitor_id', '')}
        )
        credits = account.credits
        free_remaining = account.get_free_remaining()
    
    response = render(request, 'converter/index.html', {
        'credits': credits,
        'free_remaining': free_remaining,
        'user': request.user,
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
        'srt_ready':     bool(job.get('srt_path') and os.path.exists(job.get('srt_path', ''))),
    })


def register(request):
    if request.user.is_authenticated:
        return redirect('index')
    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email    = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        password2 = request.POST.get('password2', '')
        if not username or not password:
            error = 'Username and password are required.'
        elif password != password2:
            error = 'Passwords do not match.'
        elif User.objects.filter(username=username).exists():
            error = 'Username already taken.'
        else:
            user = User.objects.create_user(username=username, email=email, password=password)
            visitor_id = request.COOKIES.get('vc_visitor_id', uuid.uuid4().hex)
            account = UserAccount.objects.create(user=user, visitor_id=visitor_id)
            auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            return redirect('index')
    response = render(request, 'converter/register.html', {'error': error})
    return response

def login_view(request):
    if request.user.is_authenticated:
        return redirect('index')
    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user:
            auth_login(request, user)
            return redirect(request.POST.get('next', 'index'))
        error = 'Invalid username or password.'
    return render(request, 'converter/login.html', {'error': error})

def logout_view(request):
    auth_logout(request)
    return redirect('landing_page')

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
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Please log in to convert files.'}, status=401)

    account, _ = UserAccount.objects.get_or_create(
        user=request.user,
        defaults={'visitor_id': request.COOKIES.get('vc_visitor_id', uuid.uuid4().hex)}
    )
    visitor_id = account.visitor_id  # keep for CreditOrder/analytics compatibility
    allowed, reason, is_paid = account.can_convert(file.size)

    if not allowed:
        return JsonResponse({'error': reason}, status=403)

# ── Reject early if global queue is full OR user is hogging slots ─────────
    with JOBS_LOCK:
        queued_or_converting = sum(
            1 for j in JOBS.values()
            if j['status'] in ('queued', 'converting')
        )
        user_active = sum(
            1 for j in JOBS.values()
            if j['status'] in ('queued', 'converting')
            and j.get('user_id') == request.user.id
        )

    if queued_or_converting >= _MAX_QUEUE:
        return JsonResponse(
            {'error': 'Server busy. Please try again in a few minutes.'},
            status=503
        )
    if user_active >= _MAX_JOBS_PER_USER:
        return JsonResponse(
            {'error': f'You already have {_MAX_JOBS_PER_USER} exports running. Wait for one to finish.'},
            status=429
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
    captions   = request.POST.get('captions', 'off') == 'on'
    caption_style = request.POST.get('caption_style', 'soft')


    import re
    custom_name = request.POST.get('output_filename', '').strip()
    if custom_name:
        custom_name = re.sub(r'[\\/:*?"<>|]', '', custom_name)
    output_filename = (custom_name if custom_name else Path(file.name).stem) + out_ext

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
            'filename':      output_filename,
            'input_name':    file.name,
            'error':         None,
            'created_at':    time.time(),
            'resolution':    resolution,
            'quality':       quality,
            'codec_pref':    codec_pref,
            'captions':      captions,
            'caption_style': caption_style,
            'srt_path':      None,
            'user_id':       request.user.id,   # NEW
        }

    thread = threading.Thread(target=_convert, args=(job_id,), daemon=True)
    # Deduct credit or free usage
    if is_paid:
        cost = 1  # flat 1 credit regardless of size
        UserAccount.objects.filter(user=request.user).update(
            credits=F('credits') - cost
        )
    else:
        UserAccount.objects.filter(user=request.user).update(
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
    _save_job_record(
    job_id, job_snapshot, 'cancelled', 0,
    user=request.user if request.user.is_authenticated else None  # NEW
    )

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
def _transcribe_with_whisper(video_path: str, job_id: str) -> str | None:
    try:
        with JOBS_LOCK:
            JOBS[job_id]['strategy'] = '🎙 Transcribing captions…'
            JOBS[job_id]['caption_progress'] = 0
            JOBS[job_id]['caption_stage'] = 'transcribing'

        # Extract audio to a temp WAV — Whisper API is more reliable with audio-only
        audio_path = video_path + '_audio.m4a'
        extract_cmd = [
            'ffmpeg', '-y', '-i', video_path,
            '-vn',                    # no video
            '-acodec', 'aac',
            '-b:a', '128k',
            '-ac', '1',               # mono — halves file size, Whisper handles it fine
            audio_path
        ]
        result = subprocess.run(extract_cmd, capture_output=True, timeout=120)
        if result.returncode != 0 or not os.path.exists(audio_path):
            # Fall back to sending the video directly if extraction fails
            audio_path = video_path

        send_path = audio_path

        with open(send_path, 'rb') as f:
            file_bytes = f.read()

        # Clean up temp audio file if we created one
        if audio_path != video_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                pass

        import io
        audio_filename = Path(send_path).name
        mime = 'audio/m4a' if audio_path.endswith('.m4a') else 'video/mp4'

        resp = http_requests.post(
            'https://api.groq.com/openai/v1/audio/transcriptions',
            headers={'Authorization': f'Bearer {settings.GROQ_API_KEY}'},
            files={'file': (audio_filename, io.BytesIO(file_bytes), mime)},
            data={
                'model': 'whisper-large-v3',
                'response_format': 'verbose_json',
                'timestamp_granularities[]': 'segment',
            },
            timeout=300,
        )
        resp.raise_for_status()
        result = resp.json()

        with JOBS_LOCK:
            JOBS[job_id]['caption_progress'] = 100
            JOBS[job_id]['caption_stage'] = 'transcribed'

        srt_path = video_path + '.srt'
        segments = result.get('segments', [])

        def fmt_ts(s):
            h = int(s // 3600)
            m = int((s % 3600) // 60)
            sec = s % 60
            return f"{h:02d}:{m:02d}:{sec:06.3f}".replace('.', ',')

        lines = []
        for i, seg in enumerate(segments, 1):
            lines.append(str(i))
            lines.append(f"{fmt_ts(seg['start'])} --> {fmt_ts(seg['end'])}")
            lines.append(seg['text'].strip())
            lines.append('')

        with open(srt_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        with JOBS_LOCK:
            if JOBS[job_id].get('caption_style') == 'soft':
                JOBS[job_id]['caption_progress'] = 100
                JOBS[job_id]['caption_stage'] = 'done'

        return srt_path

    except Exception as e:
        with JOBS_LOCK:
            JOBS[job_id]['error'] = f'Caption error: {e}'
            JOBS[job_id]['caption_stage'] = 'done'   # unblock the frontend
        return None


def _save_job_record(job_id: str, job: dict, status: str, file_size: int, user=None):  # NEW: user param
    """Persist a completed/failed/cancelled job to SQLite."""
    try:
        inp     = job.get('input', '')
        inp_ext = Path(inp).suffix.lower().strip('.') if inp else '?'
        JobRecord.objects.update_or_create(
            job_id=job_id,
            defaults={
                'user':          user,                          # NEW
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
        pass

# ── CONVERSION THREAD ─────────────────────────────────────────────────────────
def _convert(job_id: str):
    with JOBS_LOCK:
        JOBS[job_id]['strategy'] = 'Waiting for slot…'

    with _CONVERSION_SEM:
        with JOBS_LOCK:
            job = JOBS[job_id]
            JOBS[job_id]['status'] = 'converting'

        input_path  = job['input']
        output_path = job['output']

        # NEW: resolve user object from stored user_id
        from django.contrib.auth.models import User as _User
        user_id  = job.get('user_id')
        user_obj = None
        if user_id:
            try: user_obj = _User.objects.get(pk=user_id)
            except: pass
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
# ── WHISPER CAPTIONS ──────────────────────────────────────────
            srt_path = None
            with JOBS_LOCK:
                captions_requested = JOBS[job_id].get('captions', False)
                caption_style      = JOBS[job_id].get('caption_style', 'soft')

            if captions_requested:
                def _run_transcription():
                    result = _transcribe_with_whisper(output_path, job_id)
                    with JOBS_LOCK:
                        JOBS[job_id]['srt_path'] = result

                    # ── HARDSUB: burn captions into video ─────────────────
                    if result and caption_style == 'hard':
                        try:
                            with JOBS_LOCK:
                                JOBS[job_id]['strategy'] = '🔥 Burning captions into video…'
                                JOBS[job_id]['caption_progress'] = 0
                                JOBS[job_id]['caption_stage'] = 'burning'

                            hardsubbed_path = output_path + '_hardsubbed' + Path(output_path).suffix

                            # On Windows, ffmpeg subtitles filter needs forward slashes
                            # and the colon in drive letter escaped as \:
                            srt_for_ffmpeg = result.replace('\\', '/').replace('C:/', 'C\\:/')

                            burn_cmd = [
                                'ffmpeg', '-y', '-i', output_path,
                                '-vf', f"subtitles=filename='{srt_for_ffmpeg}':force_style='FontSize=18,FontName=Arial,PrimaryColour=&Hffffff,OutlineColour=&H000000,Outline=2,Alignment=2'",
                                '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
                                '-c:a', 'copy',
                                hardsubbed_path
                            ]
                            proc = subprocess.run(burn_cmd, capture_output=True, timeout=600)

                            if proc.returncode == 0:
                                os.replace(hardsubbed_path, output_path)
                                new_size = os.path.getsize(output_path)
                                with JOBS_LOCK:
                                    JOBS[job_id]['file_size'] = new_size
                                    JOBS[job_id]['caption_progress'] = 100
                                    JOBS[job_id]['caption_stage'] = 'done'
                            else:
                                err_out = proc.stderr.decode('utf-8', errors='ignore')[-300:]
                                with JOBS_LOCK:
                                    JOBS[job_id]['error'] = f'Caption burn failed: {err_out}'
                        except Exception as e:
                            with JOBS_LOCK:
                                JOBS[job_id]['error'] = f'Hardsub error: {e}'

                t = threading.Thread(target=_run_transcription, daemon=True)
                t.start()

            with JOBS_LOCK:
                JOBS[job_id].update({
                    'status':    'done',
                    'progress':  100,
                    'speed':     '',
                    'eta':       '',
                    'file_size': file_size,
                    # srt_path intentionally omitted — set by _run_transcription thread
                })
                _save_job_record(job_id, JOBS[job_id], 'done', file_size, user=user_obj) 
        else:
            with JOBS_LOCK:
                JOBS[job_id]['status'] = 'error'
                _save_job_record(job_id, JOBS[job_id], 'error', 0, user=user_obj) 


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
        'status':           job['status'],
        'progress':         job['progress'],
        'strategy':         job.get('strategy', ''),
        'speed':            job.get('speed', ''),
        'eta':              job.get('eta', ''),
        'error':            job.get('error'),
        'filename':         job.get('filename'),
        'srt_ready':        bool(job.get('srt_path') and os.path.exists(job.get('srt_path', ''))),
        'caption_progress': job.get('caption_progress', 0),
        'caption_stage':    job.get('caption_stage', ''),
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


def download_srt(request, job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or job['status'] != 'done':
        raise Http404
    srt_path = job.get('srt_path')
    if not srt_path or not os.path.exists(srt_path):
        raise Http404
    base_name = Path(job['filename']).stem + '.srt'
    return FileResponse(
        open(srt_path, 'rb'),
        content_type='text/plain',
        as_attachment=True,
        filename=base_name,
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
        # ── Registered users ──────────────────────────────────────────────────
    total_users       = User.objects.count()
    new_users_7d      = User.objects.filter(date_joined__gte=datetime.datetime.fromtimestamp(now - 7 * 86400, tz=datetime.timezone.utc)).count()
    new_users_30d     = User.objects.filter(date_joined__gte=datetime.datetime.fromtimestamp(now - 30 * 86400, tz=datetime.timezone.utc)).count()
    users_with_credits = UserAccount.objects.filter(credits__gt=0).count()

    recent_users = []
    for u in User.objects.select_related('account').order_by('-date_joined')[:10]:
        age_s = int(now - u.date_joined.timestamp())
        if age_s < 60:      joined = f'{age_s}s ago'
        elif age_s < 3600:  joined = f'{age_s // 60}m ago'
        elif age_s < 86400: joined = f'{age_s // 3600}h ago'
        else:               joined = f'{age_s // 86400}d ago'
        try:
            acct     = u.account
            credits  = acct.credits
            free_used = acct.free_used_month
        except Exception:
            credits = free_used = 0
        recent_users.append({
            'username': u.username,
            'email':    u.email,
            'joined':   joined,
            'credits':  credits,
            'freeUsed': free_used,
            'jobs':     JobRecord.objects.filter(user=u).count(),
            'isPaid':   credits > 0,
        })
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

        # Users
        'totalUsers':  total_users,
        'newUsers7d':  new_users_7d,
        'newUsers30d': new_users_30d,
        'paidUsers':   users_with_credits,
        'recentUsers': recent_users,
    })


VISITOR_COOKIE = 'vc_visitor_id'
VISITOR_COOKIE_AGE = 365 * 24 * 60 * 60  # 1 year

def _track_visitor(request, response):
    visitor_id = request.COOKIES.get(VISITOR_COOKIE)
    is_new = False
    if not visitor_id:
        visitor_id = uuid.uuid4().hex
        is_new = True
        response.set_cookie(VISITOR_COOKIE, visitor_id, max_age=VISITOR_COOKIE_AGE, httponly=True, samesite='Lax')

    now = _time.time()
    try:
        if is_new:
            Visitor.objects.create(visitor_id=visitor_id, first_seen=now, last_seen=now, visit_count=1)
        else:
            Visitor.objects.filter(visitor_id=visitor_id).update(last_seen=now, visit_count=F('visit_count') + 1)
        
        # NEW: if user is logged in, keep their UserAccount visitor_id in sync
        if request.user.is_authenticated:
            UserAccount.objects.filter(user=request.user, visitor_id='').update(visitor_id=visitor_id)
    except Exception:
        pass
    return response

def pricing(request):
    credits = 0
    free_remaining = 0
    if request.user.is_authenticated:
        account, _ = UserAccount.objects.get_or_create(
            user=request.user,
            defaults={'visitor_id': request.COOKIES.get('vc_visitor_id', '')}
        )
        credits = account.credits
        free_remaining = account.get_free_remaining()
    return render(request, 'converter/pricing.html', {
        'credits': credits,
        'free_remaining': free_remaining,
        'user': request.user,
    })


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

    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Please log in to purchase credits.'}, status=401)
    visitor_id = ''
    try:
        visitor_id = request.user.account.visitor_id
    except Exception:
        pass

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
        user=request.user,  
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
        
        # New (handles both legacy visitor accounts and real user accounts):
        if order.user_id:
            models.UserAccount.objects.filter(user=order.user).update(credits=_F('credits') + order.credits)
        else:
            models.UserAccount.objects.filter(visitor_id=order.visitor_id).update(credits=_F('credits') + order.credits)

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


def credits_status(request):
    if not request.user.is_authenticated:
        return JsonResponse({'credits': 0, 'free_remaining': 0})
    account, _ = UserAccount.objects.get_or_create(
        user=request.user,
        defaults={'visitor_id': request.COOKIES.get('vc_visitor_id', '')}
    )
    return JsonResponse({
        'credits': account.credits,
        'free_remaining': account.get_free_remaining(),
    })


def google_register_start(request):
    request.session['google_from_register'] = True
    request.session.modified = True
    request.session.save()
    from allauth.socialaccount.providers.google.views import oauth2_login
    return oauth2_login(request)

def groq_chat(request):
    """Handles both smart suggestion and assistant chat."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Login required.'}, status=401)

    try:
        body = json.loads(request.body)
    except:
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)

    mode     = body.get('mode', 'chat')
    message  = body.get('message', '')
    fileinfo = body.get('fileinfo', {})

    if mode == 'suggest':
        SYSTEM_PROMPT = """You are an export assistant for content creators inside a video export tool.
        You help creators get the right settings for the platform they are posting to.

        You MUST always respond with JSON only. No markdown. No extra text.

        Output formats: mp4, mkv, avi, mov, webm, flv, wmv, ts, m4v, 3gp
        Resolutions: original, 1920x1080, 1280x720, 854x480, 640x360
        Quality: auto, high, medium, small
        Codecs: auto, h264, h265

        RULES:
        RULES:
        1. NEVER suggest the same format as the input file extension
        2. The "explanation" field is your reply — be warm, enthusiastic and conversational like a helpful creative friend. Use natural language, contractions and light energy. Occasionally use a relevant emoji (🎬 📱 ✨ 🚀). Never list settings dryly — always wrap them in a friendly sentence like "YouTube it is! I've set you up with 1080p high quality MP4 — your video's gonna look great 🎬". Keep it 1-3 sentences max.
        3. If user mentions a platform, apply its preset and confirm it in the explanation
        4. If user asks a question without changing settings, keep current settings and just answer
        5. If user asks to rename/change filename, put the new name (without extension) in "filename"
        6. Always warn in explanation if file size might exceed platform limits

        PLATFORM PRESETS:
        - YouTube: mp4 + 1920x1080 + high + h264
        - TikTok: mp4 + 1920x1080 + auto + h264
        - Instagram / Reels: mp4 + 1920x1080 + auto + h264
        - YouTube Shorts: mp4 + 1920x1080 + auto + h264
        - Twitter / X: mp4 + 1920x1080 + medium + h264
        - Discord: mp4 + 1280x720 + small + h264 (warn if file will exceed 10MB)
        - LinkedIn: mp4 + 1920x1080 + high + h264
        - Facebook: mp4 + 1920x1080 + auto + h264
        - compress / make smaller: quality=small + 1280x720
        - max quality: high + original + h265

        SIZE LIMITS TO WARN ABOUT:
        TikTok=287MB, Discord=10MB free / 50MB Nitro, Twitter=512MB, Instagram Reels=4GB

        Return ONLY this JSON:
        {"format":"mp4","resolution":"1920x1080","quality":"high","codec":"h264","filename":"","explanation":"Your conversational creator-friendly reply here."}"""

        current_format = fileinfo.get('current_format', 'mp4')
        input_ext = fileinfo.get('input_ext', '')
        active_platform = fileinfo.get('active_platform', '')
        user_message = f"""Suggest the best conversion settings for this file:
- Filename: {fileinfo.get('name', 'unknown')}
- Size: {fileinfo.get('size', 'unknown')}
- Duration: {fileinfo.get('duration', 'unknown')} seconds
- Video codec: {fileinfo.get('vcodec', 'unknown')}
- Audio codec: {fileinfo.get('acodec', 'unknown')}
- Input format: {input_ext}
- Currently selected output format: {current_format}
- Active platform preset: {active_platform if active_platform else 'none'}

IMPORTANT: Do NOT suggest {input_ext} as the output format.
{f"The user has already selected the {active_platform} preset — use its exact settings." if active_platform else ""}
Return only JSON."""

    else:
        SYSTEM_PROMPT = """You are ExportReady's friendly AI export assistant — think of yourself like a knowledgeable, upbeat creative tech friend who genuinely enjoys helping creators get their videos out into the world.

        You MUST always respond with JSON only. No markdown. No extra text.

        Output formats: mp4, mkv, avi, mov, webm, flv, wmv, ts, m4v, 3gp
        Resolutions: original, 1920x1080, 1280x720, 854x480, 640x360
        Quality: auto, high, medium, small
        Codecs: auto, h264, h265
        Captions: true or false

        PERSONALITY RULES for the "explanation" field:
        - Be warm, enthusiastic and conversational — like a helpful friend, not a robot
        - Use natural language, contractions, light energy (e.g. "Perfect!", "Great choice!", "You're all set!")
        - When applying a platform preset, show excitement: "YouTube it is! I've bumped you up to 1080p high quality so your video looks stunning on the platform 🎬"
        - When answering a settings question, be helpful and clear: "Right now you're set to MKV, original resolution, auto quality — solid lossless settings! Want me to tweak anything?"
        - When the user just asks a question without changing settings, answer it naturally and helpfully without being robotic
        - Keep replies concise but human — 1-3 sentences max
        - Occasionally use a relevant emoji to add warmth (🎬 📱 ✨ 🚀) but don't overdo it
        - NEVER say things like "Current settings: MKV, original resolution" in a dry list format — always wrap it in a friendly sentence

        TECHNICAL RULES:
        1. If the user asks for a specific format, USE THAT EXACT FORMAT
        2. If the user asks for mkv, set format to mkv. Not webm, not mp4. mkv.
        3. If user asks a question without changing settings, keep current settings and just answer warmly
        4. If WhatsApp: mp4 + 854x480 + small + h264
        5. If email: mp4 + 640x360 + small
        6. If compress / make smaller: quality=small
        7. If user asks to rename/change filename, put the new name (without extension) in "filename"
        8. If user mentions captions, subtitles, transcribe — set captions to true
        9. If user says no captions or turn off captions — set captions to false
        10. NEVER change a setting the user did not ask you to change

        Return ONLY this JSON:
        {"format":"mkv","resolution":"original","quality":"auto","codec":"auto","filename":"","captions":false,"explanation":"Your warm, friendly, human reply here."}"""

        input_ext = fileinfo.get('input_ext', '')
        current_fmt = fileinfo.get('current_format', '')
        user_message = f"""The user says: "{message}"
        Current file: {fileinfo.get('name', 'unknown')} ({input_ext} format, {fileinfo.get('size', 'unknown')})
        Current settings:
        - Format: {current_fmt}
        - Resolution: {fileinfo.get('current_resolution','original')}
        - Quality: {fileinfo.get('current_quality','auto')}
        - Codec: {fileinfo.get('current_codec','auto')}

        If the user asks what the current preset or settings are, describe the above in plain English. Example: "You're set to MP4, 720p, High quality, H.264 codec."
        If the user makes a vague follow-up like "i mean the media" or "what about the file", refer to the conversation history to understand context.
        RULE: Do NOT suggest {input_ext} as the output format.
        If the user mentions a platform (YouTube, TikTok, Reels, Shorts, Twitter, Discord, LinkedIn, Facebook), apply that platform's preset.
        Return only JSON."""

    try:
        history = body.get('history', [])
        messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]
        for turn in history[:-1]:
            messages.append({'role': turn['role'], 'content': turn['content']})
        messages.append({'role': 'user', 'content': user_message})

        resp = http_requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={
                'Authorization': f"Bearer {settings.GROQ_API_KEY}",
                'Content-Type': 'application/json',
            },
            json={
                'model': 'llama-3.1-8b-instant',
                'messages': messages,
                'max_tokens': 400,
                'temperature': 0.3,
            },
            timeout=10,
        )
        resp.raise_for_status()
        content = resp.json()['choices'][0]['message']['content'].strip()
        if content.startswith('```'):
            content = content.split('```')[1]
            if content.startswith('json'):
                content = content[4:]
        result = json.loads(content)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
    

def landing_page(request):
    if request.user.is_authenticated:
        return redirect('index')
    return render(request, 'converter/landing_page.html')


def thumbnail(request, job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)

    if not job:
        raise Http404

    video_path = job.get('output')
    if not video_path or not os.path.exists(video_path):
        raise Http404

    thumb_path = video_path + '_thumb.jpg'

    if not os.path.exists(thumb_path):
        try:
            subprocess.run([
                'ffmpeg', '-y',
                '-ss', '00:00:02',
                '-i', video_path,
                '-vframes', '1',
                '-q:v', '3',
                '-vf', 'scale=480:-1',
                thumb_path
            ], timeout=15, capture_output=True)
        except Exception:
            raise Http404

    if not os.path.exists(thumb_path):
        raise Http404

    return FileResponse(open(thumb_path, 'rb'), content_type='image/jpeg')

@login_required
def export_history(request):
    """Returns JSON data for the History Modal (no full HTML rendering)"""
    import datetime

    raw_records = JobRecord.objects.filter(user=request.user).order_by('-created_at')[:50]

    records = []
    for r in raw_records:
        records.append({
            'input_name':    r.input_name,
            'input_ext':     r.input_ext or 'unknown',
            'output_format': r.output_format.upper() if r.output_format else '—',
            'file_size':     r.file_size,
            'status':        r.status,
            'date':          datetime.datetime.fromtimestamp(r.created_at).strftime('%b %d, %Y') 
                             if r.created_at else '—',
        })

    return JsonResponse({'records': records})

@csrf_exempt
@require_POST
@login_required
def import_drive(request):
    import urllib.request
    import urllib.error

    try:
        body = json.loads(request.body)
    except:
        return JsonResponse({'error': 'Invalid request.'}, status=400)

    file_id      = body.get('file_id', '')
    output_format = body.get('output_format', 'mp4').lower().strip('.')

    if not file_id:
        return JsonResponse({'error': 'No file ID provided.'}, status=400)

    if output_format not in SUPPORTED_OUTPUT:
        return JsonResponse({'error': f'Unsupported output format.'}, status=400)

    # Google Drive direct download URL
    download_url = f'https://drive.google.com/uc?export=download&id={file_id}'

    try:
        req = urllib.request.Request(download_url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urllib.request.urlopen(req, timeout=30)

        # Try to get filename from headers
        content_disp = response.headers.get('Content-Disposition', '')
        filename = 'drive_file'
        if 'filename=' in content_disp:
            filename = content_disp.split('filename=')[-1].strip('"\'')
        if not filename or filename == 'drive_file':
            filename = f'drive_{file_id[:8]}.mp4'

        input_ext = Path(filename).suffix.lower()
        if input_ext not in SUPPORTED_INPUT:
            input_ext = '.mp4'

        # Check file size from headers
        content_length = response.headers.get('Content-Length')
        file_size_bytes = int(content_length) if content_length else 0

        # Tier check
        account, _ = UserAccount.objects.get_or_create(
            user=request.user,
            defaults={'visitor_id': ''}
        )
        if file_size_bytes > 0:
            allowed, reason, is_paid = account.can_convert(file_size_bytes)
            if not allowed:
                return JsonResponse({'error': reason}, status=403)

        # Save the downloaded file
        job_id     = uuid.uuid4().hex
        upload_dir = Path(settings.MEDIA_ROOT) / 'uploads'
        output_dir = Path(settings.MEDIA_ROOT) / 'converted'
        upload_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        out_ext     = SUPPORTED_OUTPUT[output_format]['ext']
        input_path  = upload_dir / f'{job_id}{input_ext}'
        output_path = output_dir / f'{job_id}{out_ext}'

        CHUNK = 4 * 1024 * 1024
        actual_size = 0
        with open(input_path, 'wb') as f:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                actual_size += len(chunk)

    except urllib.error.URLError as e:
        return JsonResponse({'error': f'Could not download file: {str(e)}'}, status=502)
    except Exception as e:
        return JsonResponse({'error': f'Drive import failed: {str(e)}'}, status=500)

    output_filename = Path(filename).stem + out_ext

    pause_event  = threading.Event()
    cancel_event = threading.Event()
    pause_event.set()

    with JOBS_LOCK:
        JOBS[job_id] = {
            'status':        'queued',
            'progress':      0,
            'strategy':      'Waiting for slot…',
            'speed':         '', 'eta':  '',
            'input':         str(input_path),
            'output':        str(output_path),
            'output_format': output_format,
            'filename':      output_filename,
            'input_name':    filename,
            'error':         None,
            'created_at':    time.time(),
            'resolution':    'original',
            'quality':       'auto',
            'codec_pref':    'auto',
            'captions':      False,
            'caption_style': 'soft',
            'srt_path':      None,
            'user_id':       request.user.id,
        }
        JOB_PAUSE[job_id]  = pause_event
        JOB_CANCEL[job_id] = cancel_event

    # Deduct credit/free usage
    allowed, reason, is_paid = account.can_convert(actual_size)
    if is_paid:
        UserAccount.objects.filter(user=request.user).update(credits=F('credits') - 1)
    else:
        UserAccount.objects.filter(user=request.user).update(free_used_month=F('free_used_month') + 1)

    thread = threading.Thread(target=_convert, args=(job_id,), daemon=True)
    thread.start()

    return JsonResponse({
        'job_id':    job_id,
        'filename':  output_filename,
        'file_size':_human_size(actual_size) if actual_size else '—',  # note: use _human_size
    })


def health(request):
    return HttpResponse("ok")