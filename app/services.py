import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import time
import wave
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

import psutil
from mutagen.flac import FLAC
from mutagen.id3 import APIC, ID3

from .config import DB_FILENAME, EXPORT_DIR, FFMPEG_BIN, REKORDBOX_ROOT
from .database import db

try:
    import soundfile as sf
    _HAS_SOUNDFILE = True
except ImportError:
    _HAS_SOUNDFILE = False
    logger_import = logging.getLogger(__name__)
    logger_import.warning("soundfile not available — FLAC export disabled")

try:
    import lameenc
    _HAS_LAMEENC = True
except ImportError:
    _HAS_LAMEENC = False
    logger_import = logging.getLogger(__name__)
    logger_import.warning("lameenc not available — MP3 export disabled")

logger = logging.getLogger(__name__)

class XMLProcessor:
    REMOVE_STRINGS = ["(Original Mix)", "(Extended Mix)", "Original Mix"]
    MIN_TRACKS_THRESHOLD = 5
    MISSING_ARTIST_NAME = "!!SERVICE!!"
    MISSING_ARTIST_COLOR_ID = "8"

    @staticmethod
    def clean_tag(text: str) -> str:
        if not text: return ""
        cleaned = text.strip()
        for bad_str in XMLProcessor.REMOVE_STRINGS:
            if bad_str in cleaned:
                cleaned = cleaned.replace(bad_str, "")
        return " ".join(cleaned.split())

    @staticmethod
    def create_node(parent, name, type_id="0"):
        node = ET.SubElement(parent, "NODE")
        node.set("Name", name)
        node.set("Type", type_id)
        node.set("Count", "0")
        if type_id == "1":
            node.set("KeyType", "0")
            node.set("Entries", "0")
        return node

    @staticmethod
    def get_or_create_path(root_node, path_str):
        current_node = root_node
        parts = [p for p in path_str.replace("\\", "/").split("/") if p]
        for part in parts:
            found = None
            for child in current_node.findall("NODE"):
                if child.get("Name") == part and child.get("Type") == "0":
                    found = child
                    break
            if found: current_node = found
            else: current_node = XMLProcessor.create_node(current_node, part, "0")
        return current_node

    @staticmethod
    def process(input_path, output_path, artist_folder, label_folder):
        tree = ET.parse(input_path)
        root = tree.getroot()
        if root.tag != "DJ_PLAYLISTS": raise ValueError("Invalid Rekordbox XML")

        collection = root.find("COLLECTION")
        playlists_root = root.find("PLAYLISTS")
        if collection is None or playlists_root is None: raise ValueError("Invalid XML Structure")

        tracks = list(collection.findall("TRACK"))
        artist_map = defaultdict(list)
        label_map = defaultdict(list)

        for track in tracks:
            tid = track.get("TrackID")

            # Cleaning
            for attr in ["Name", "Artist", "Album", "Label"]:
                track.set(attr, XMLProcessor.clean_tag(track.get(attr, "")))

            # Service Check
            artist_val = track.get("Artist", "").strip()
            if not artist_val:
                track.set("Artist", XMLProcessor.MISSING_ARTIST_NAME)
                track.set("ColorID", XMLProcessor.MISSING_ARTIST_COLOR_ID)
                artist_val = XMLProcessor.MISSING_ARTIST_NAME

            if artist_val: artist_map[artist_val].append(tid)
            label_val = track.get("Label", "").strip()
            if label_val: label_map[label_val].append(tid)

        # Playlist Gen
        root_node = playlists_root.find("NODE")
        if root_node is None: root_node = XMLProcessor.create_node(playlists_root, "ROOT", "0")

        # Artists
        target_art = XMLProcessor.get_or_create_path(root_node, artist_folder)
        for art, tids in artist_map.items():
            if len(tids) >= XMLProcessor.MIN_TRACKS_THRESHOLD:
                pl = XMLProcessor.create_node(target_art, art, "1")
                pl.set("Entries", str(len(tids)))
                for t in tids: ET.SubElement(pl, "TRACK").set("Key", t)

        # Labels
        target_lbl = XMLProcessor.get_or_create_path(root_node, label_folder)
        for lbl, tids in label_map.items():
            if len(tids) >= XMLProcessor.MIN_TRACKS_THRESHOLD:
                pl = XMLProcessor.create_node(target_lbl, lbl, "1")
                pl.set("Entries", str(len(tids)))
                for t in tids: ET.SubElement(pl, "TRACK").set("Key", t)

        tree.write(output_path, encoding="utf-8", xml_declaration=True)
        return output_path

class SystemGuard:
    @staticmethod
    def is_rekordbox_running() -> bool:
        for proc in psutil.process_iter(['name']):
            try:
                if "rekordbox" in proc.info['name'].lower(): return True
            except (AttributeError, KeyError, psutil.NoSuchProcess, psutil.AccessDenied) as e:
                logger.debug(
                    "services.is_rekordbox_running: skipped pid=%s (%s)", proc.pid, e,
                )
        return False
class AudioEngine:
    @staticmethod
    def check_ffmpeg() -> bool:
        try:
            subprocess.run([FFMPEG_BIN, "-version"], capture_output=True, check=True)
            return True
        except (FileNotFoundError, OSError, subprocess.SubprocessError) as e:
            logger.error("FFmpeg not available at %s (%s)", FFMPEG_BIN, e)
            return False

    @staticmethod
    def render_segment(source_path: str, cuts: list, output_filename: str, fade_in=False, fade_out=False):
        if not AudioEngine.check_ffmpeg():
             raise RuntimeError("FFmpeg not installed or not found in system PATH. Please install FFmpeg.")
        if not os.path.exists(source_path): raise FileNotFoundError(f"File not found: {source_path}")
        temp_files = []
        try:
            # ── Build timeline plan: (src_path, src_start, src_end) tuples ────
            # cuts may contain three types:
            #   - 'delete' / no type with start/end: remove that range from the source timeline
            #   - 'insert': paste an extra slice at insertAt (from src, range [start..end])
            # Anything not explicitly an insert/delete is treated as a literal slice
            # (legacy "make-section" mode — preserves backwards compatibility).

            try:
                src_dur = AudioEngine.get_duration(source_path) if hasattr(AudioEngine, "get_duration") else None
            except Exception:
                src_dur = None
            if src_dur is None:
                # ffprobe fallback
                try:
                    pr = subprocess.run(
                        [FFMPEG_BIN.replace("ffmpeg", "ffprobe"),
                         "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=noprint_wrappers=1:nokey=1", source_path],
                        capture_output=True, text=True, timeout=10,
                    )
                    src_dur = float(pr.stdout.strip()) if pr.returncode == 0 else 0
                except Exception:
                    src_dur = 0

            has_modes = any(c.get("type") in ("insert", "delete", "cut") for c in cuts)
            timeline = []   # ordered (src, t_start, t_end)

            if has_modes:
                # 1. Start from full track
                base_segments = [(source_path, 0.0, float(src_dur))]
                # 2. Apply deletes (type == 'delete' or 'cut')
                for c in [c for c in cuts if c.get("type") in ("delete", "cut")]:
                    cs, ce = float(c["start"]), float(c["end"])
                    if ce <= cs:
                        continue
                    new_segs = []
                    for s, ts, te in base_segments:
                        if ce <= ts or cs >= te:
                            new_segs.append((s, ts, te))
                            continue
                        if cs > ts:
                            new_segs.append((s, ts, cs))
                        if ce < te:
                            new_segs.append((s, ce, te))
                    base_segments = new_segs
                timeline = list(base_segments)
                # 3. Apply inserts at insertAt position on the (already-deleted) timeline
                for c in [c for c in cuts if c.get("type") == "insert"]:
                    insert_at = float(c.get("insertAt", 0))
                    seg_src = c.get("src", source_path)
                    if not os.path.exists(seg_src):
                        seg_src = source_path
                    seg_start, seg_end = float(c["start"]), float(c["end"])
                    if seg_end <= seg_start:
                        continue
                    # Walk current timeline, find the segment that contains insert_at
                    new_tl = []
                    cursor = 0.0
                    inserted = False
                    for s, ts, te in timeline:
                        seg_dur = te - ts
                        if not inserted and cursor + seg_dur >= insert_at:
                            split_offset = insert_at - cursor
                            split_point = ts + split_offset
                            if split_offset > 0:
                                new_tl.append((s, ts, split_point))
                            new_tl.append((seg_src, seg_start, seg_end))
                            if split_point < te:
                                new_tl.append((s, split_point, te))
                            inserted = True
                        else:
                            new_tl.append((s, ts, te))
                        cursor += seg_dur
                    if not inserted:
                        new_tl.append((seg_src, seg_start, seg_end))
                    timeline = new_tl
            else:
                # Legacy: literal cut list = ordered output segments
                for c in cuts:
                    s = c.get("src", source_path)
                    if not os.path.exists(s):
                        s = source_path
                    timeline.append((s, float(c["start"]), float(c["end"])))

            for i, (cut_src, start, end) in enumerate(timeline):
                duration = end - start

                # Skip zero-duration segments which cause FFmpeg to fail
                if duration <= 0:
                    logger.warning(f"Skipping zero-duration segment at index {i} ({start} - {end})")
                    continue

                logger.info(f"[Segment {i}] Checking source: {cut_src}")
                if not cut_src or not os.path.exists(cut_src):
                    logger.warning(f"Segment source not found: {cut_src}, falling back to master source")
                    cut_src = source_path

                # Use unique filename with microsecond precision
                unique_id = f"{int(time.time() * 1000)}_{i}"
                temp = EXPORT_DIR / f"temp_{unique_id}.wav"

                filters = []
                if fade_in and i == 0: filters.append("afade=t=in:st=0:d=1.0")
                if fade_out and i == len(timeline)-1: filters.append(f"afade=t=out:st={max(0, duration-1)}:d=1.0")
                f_arg = ["-af", ",".join(filters)] if filters else []

                # Sample-accurate seek: -ss AFTER -i (decode then trim) to avoid
                # keyframe drift that misaligns pasted regions.
                cmd = [FFMPEG_BIN, "-y", "-i", cut_src, "-ss", str(max(0, start)), "-t", str(duration)] + f_arg + ["-vn", "-map", "0:a", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(temp)]
                logger.info(f"[Segment {i}] FFmpeg command: {' '.join(cmd)}")
                logger.info(f"[Segment {i}] Cut details - start: {start}s, end: {end}s, duration: {duration}s")
                logger.info(f"[Segment {i}] Source file: {cut_src}")
                logger.info(f"[Segment {i}] Source exists: {os.path.exists(cut_src)}")
                logger.info(f"[Segment {i}] Output file: {temp}")

                _ffmpeg_t0 = time.monotonic()
                result = subprocess.run(cmd, capture_output=True, text=True)
                _ffmpeg_elapsed = time.monotonic() - _ffmpeg_t0
                if result.returncode != 0:
                    logger.error(f"[Segment {i}] FFmpeg FAILED (rc={result.returncode}, elapsed={_ffmpeg_elapsed:.2f}s)")
                    logger.error(f"[Segment {i}] STDERR: {result.stderr}")
                    logger.error(f"[Segment {i}] STDOUT: {result.stdout}")
                    raise RuntimeError(f"FFmpeg failed for segment {i}: {result.stderr}")

                logger.info(
                    f"[Segment {i}] FFmpeg ok: rc=0 elapsed=%.2fs out=%s"
                    % (_ffmpeg_elapsed, temp),
                )
                temp_files.append(temp)

            if not temp_files:
                raise ValueError("No valid segments to render")

            # Final output file path
            final_path = EXPORT_DIR / output_filename
            output_ext = Path(output_filename).suffix.lower()

            # Always concatenate as WAV first
            wav_path = EXPORT_DIR / (Path(output_filename).stem + "_concat.wav") if output_ext != '.wav' else final_path

            # Use Python's wave module to concatenate the standardized WAV files
            with wave.open(str(wav_path), 'wb') as outfile:
                with wave.open(str(temp_files[0]), 'rb') as infile:
                    outfile.setparams(infile.getparams())

                for tf in temp_files:
                    with wave.open(str(tf), 'rb') as infile:
                        outfile.writeframes(infile.readframes(infile.getnframes()))

            logger.info(f"Concatenated WAV to {wav_path}")

            # Convert to target format if not WAV
            if output_ext == '.mp3':
                if not _HAS_LAMEENC:
                    raise RuntimeError("MP3 export requires lameenc: pip install lameenc")
                # Read concatenated WAV as raw PCM samples, encode with lameenc (LAME)
                with wave.open(str(wav_path), 'rb') as wf:
                    channels = wf.getnchannels()
                    sample_rate = wf.getframerate()
                    n_frames = wf.getnframes()
                    raw_pcm = wf.readframes(n_frames)
                # lameenc expects int16 interleaved samples
                encoder = lameenc.Encoder()
                encoder.set_bit_rate(320)
                encoder.set_in_sample_rate(sample_rate)
                encoder.set_channels(channels)
                encoder.set_quality(2)  # 2 = near-best, 7 = fastest
                mp3_data = encoder.encode(raw_pcm)
                mp3_data += encoder.flush()
                with open(str(final_path), 'wb') as f:
                    f.write(mp3_data)
                try: os.remove(str(wav_path))
                except OSError: pass
                logger.info(f"Encoded MP3 via lameenc: {final_path} ({len(mp3_data)//1024} KB)")
            elif output_ext == '.flac':
                if not _HAS_SOUNDFILE:
                    raise RuntimeError("FLAC export requires soundfile: pip install soundfile")
                # Read WAV via soundfile and re-encode as FLAC (lossless)
                data, sample_rate = sf.read(str(wav_path), dtype='int16')
                sf.write(str(final_path), data, sample_rate, subtype='PCM_16', format='FLAC')
                try: os.remove(str(wav_path))
                except OSError: pass
                logger.info(f"Encoded FLAC via soundfile: {final_path}")
            else:
                logger.info(f"Exported WAV: {final_path}")

            # Generate Track Metadata for DB (optional, don't fail export if DB unavailable)
            new_tid = f"R_{int(time.time())}"
            try:
                orig_track = None
                for t in db.tracks.values():
                    if t.get('path') == source_path:
                        orig_track = t
                        break

                kind_map = {'.wav': 'WAV File', '.mp3': 'MP3 File', '.flac': 'FLAC File'}
                total_duration = sum([c['end'] - c['start'] for c in cuts])

                track_data = {
                    "TrackID": new_tid,
                    "Name": output_filename.rsplit('.', 1)[0],
                    "Artist": orig_track.get('Artist', 'LibraryManagementSystem') if orig_track else "LibraryManagementSystem",
                    "Album": "Edits",
                    "Genre": orig_track.get('Genre', '') if orig_track else "",
                    "Kind": kind_map.get(output_ext, 'Audio File'),
                    "Size": str(final_path.stat().st_size),
                    "TotalTime": str(int(total_duration)),
                    "DateAdded": datetime.datetime.now().strftime("%Y-%m-%d"),
                    "Bitrate": "320" if output_ext == '.mp3' else "2304",
                    "SampleRate": "44100",
                    "path": str(final_path),
                    "BPM": orig_track.get('BPM', orig_track.get('AverageBpm', '0')) if orig_track else "0",
                }

                db.add_track(track_data)
                db.save_xml()
            except Exception as db_err:
                logger.warning(f"Could not add exported track to DB (non-fatal): {db_err}")

            return new_tid
        except Exception as e:
            logger.error(f"Render failed: {e}")
            raise RuntimeError(f"Render failed: {e}")
        finally:
            # STABILITY: Always clean up temp files, even on error
            for tf in temp_files:
                try:
                    if os.path.exists(str(tf)):
                        os.remove(str(tf))
                except OSError as cleanup_err:
                    logger.warning(f"Could not remove temp file {tf}: {cleanup_err}")

    @staticmethod
    def slice_audio(source_path: str, start: float, end: float):
        if not AudioEngine.check_ffmpeg():
             raise RuntimeError("FFmpeg not found")

        if not os.path.exists(source_path): raise FileNotFoundError(f"Source not found: {source_path}")

        duration = end - start
        if duration <= 0: raise ValueError("Invalid duration")

        unique_id = f"slice_{int(time.time()*1000)}"
        temp_path = EXPORT_DIR / f"{unique_id}.wav"

        # Standardize to WAV PCM for frontend compatibility
        cmd = [
            FFMPEG_BIN, "-y",
            "-ss", str(float(start)),
            "-t", str(float(duration)),
            "-i", source_path,
            "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le",
            str(temp_path)
        ]

        try:
            logger.info("ffmpeg slice start: args=%s", " ".join(cmd))
            _t0 = time.monotonic()
            result = subprocess.run(cmd, capture_output=True, text=True)
            _elapsed = time.monotonic() - _t0
            if result.returncode != 0:
                logger.error(
                    "ffmpeg slice FAILED: rc=%s elapsed=%.2fs stderr=%s",
                    result.returncode, _elapsed, result.stderr,
                )
                raise RuntimeError(f"FFmpeg slice failed: {result.stderr}")
            logger.info(
                "ffmpeg slice done: rc=0 elapsed=%.2fs out=%s",
                _elapsed, temp_path,
            )
            return str(temp_path)
        except Exception as e:
            logger.error(f"Slice error: {e}")
            raise e

    @staticmethod
    def generate_multiband_waveform(path: str, pixels_per_second: int = 50):
        """Generates 3-band waveform data for professional visualization."""
        import librosa
        import numpy as np
        from scipy.signal import butter, lfilter

        logger.info(f"Generating multiband waveform for {path} at {pixels_per_second} pps")

        # 1. Load audio (Mono, downsampled for speed)
        y, sr = librosa.load(path, sr=22050, mono=True)
        duration = librosa.get_duration(y=y, sr=sr)
        points = int(duration * pixels_per_second)

        # 2. Filter Design
        def butter_lowpass(cut, fs, order=3):
            nyq = 0.5 * fs
            c = cut / nyq
            return butter(order, c, btype='low')

        def butter_bandpass(lowcut, highcut, fs, order=3):
            nyq = 0.5 * fs
            return butter(order, [lowcut/nyq, highcut/nyq], btype='band')

        def butter_highpass(cut, fs, order=3):
            nyq = 0.5 * fs
            return butter(order, cut/nyq, btype='high')

        # 3. Apply Filters (Low: <250Hz, Mid: 250-2500Hz, High: >2500Hz)
        b_l, a_l = butter_lowpass(250, sr)
        b_m, a_m = butter_bandpass(250, 2500, sr)
        b_h, a_h = butter_highpass(2500, sr)

        y_l = lfilter(b_l, a_l, y)
        y_m = lfilter(b_m, a_m, y)
        y_h = lfilter(b_h, a_h, y)

        # 4. Extract Envelope (RMS)
        hop = max(1, len(y) // points)

        def get_envelopes(data):
            # Pad to match points exactly
            env = librosa.feature.rms(y=data, frame_length=hop*2, hop_length=hop)[0]
            if len(env) > points: env = env[:points]
            elif len(env) < points: env = np.pad(env, (0, points - len(env)))
            return env

        low_env = get_envelopes(y_l)
        mid_env = get_envelopes(y_m)
        high_env = get_envelopes(y_h)

        # 5. Normalize (Global)
        glob_max = max(np.max(low_env), np.max(mid_env), np.max(high_env))
        if glob_max > 0:
            low_env = (low_env / glob_max).round(4)
            mid_env = (mid_env / glob_max).round(4)
            high_env = (high_env / glob_max).round(4)

        return {
            "low": low_env.tolist(),
            "mid": mid_env.tolist(),
            "high": high_env.tolist(),
            "duration": duration,
            "points": points
        }

class FileManager:
    @staticmethod
    def move_track(current, target_folder):
        src, dst_dir = Path(current), Path(target_folder)
        if not src.exists(): raise FileNotFoundError()
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        counter = 1
        while dst.exists():
            if src.resolve() == dst.resolve(): return str(dst)
            dst = dst_dir / f"{src.stem}_{counter}{src.suffix}"
            counter += 1
        shutil.move(str(src), str(dst))
        return str(dst)

class LibraryTools:
    @staticmethod
    def find_duplicates():
        if not db.tracks: return []
        # Basic duplicate detection by Name and Artist
        counts = defaultdict(list)
        for tid, track in db.tracks.items():
            key = (track.get("Title", "").lower(), track.get("Artist", "").lower())
            counts[key].append(tid)

        duplicates = []
        for key, ids in counts.items():
            if len(ids) > 1:
                duplicates.append({
                    "Title": db.tracks[ids[0]].get("Title"),
                    "Artist": db.tracks[ids[0]].get("Artist"),
                    "count": len(ids),
                    "ids": ids
                })
        return duplicates

    @staticmethod
    def clean_track_titles(track_ids):
        """Removes artist name from track title if redundant."""
        results = {"success": [], "errors": []}
        for tid in track_ids:
            track = db.get_track_details(tid)
            if not track: continue

            title = track.get("Title", "")
            artist = track.get("Artist", "")
            if not title or not artist: continue

            # Pattern 1: "Artist - Title"
            # Pattern 2: "Title (feat. Artist)" - maybe keep this?
            # User specifically mentioned "Artist in title field".

            new_title = title
            # Try some common delimiters
            for sep in [" - ", " – ", " — ", " | "]:
                if sep in title:
                    parts = title.split(sep)
                    # If the first part matches the artist (ignore case)
                    if parts[0].strip().lower() == artist.lower():
                        new_title = sep.join(parts[1:]).strip()
                        break
                    # Or second part matches
                    elif parts[-1].strip().lower() == artist.lower():
                        new_title = sep.join(parts[:-1]).strip()
                        break

            if new_title != title:
                db.update_track_title(tid, new_title)
                results["success"].append({"id": tid, "old": title, "new": new_title})
        return results

    @staticmethod
    def generate_smart_playlists(artist_threshold=5, label_threshold=5):
        """Generates intelligent playlists for Artists and Labels meeting the threshold."""
        # Note: XMLProcessor already does something similar, but this is for dynamic calling
        artist_map = defaultdict(list)
        label_map = defaultdict(list)

        for tid, track in db.tracks.items():
            artist = track.get("Artist")
            label = track.get("Label")
            if artist: artist_map[artist].append(tid)
            if label: label_map[label].append(tid)

        # 1. Ensure "Auto Playlists" folder
        auto_folder_node = next((p for p in db.playlists if p['Name'] == "Auto Playlists" and p['Type'] == "0"), None)
        if not auto_folder_node:
            auto_folder_node = db.create_playlist("Auto Playlists", parent_id="ROOT", is_folder=True)

        if not auto_folder_node: return False
        auto_folder_id = auto_folder_node['ID']

        # 2. Artists Folder
        art_folder_node = next((p for p in db.playlists if p['Name'] == "By Artist" and p['ParentID'] == auto_folder_id), None)
        if not art_folder_node:
            art_folder_node = db.create_playlist("By Artist", parent_id=auto_folder_id, is_folder=True)

        if art_folder_node:
            art_folder_id = art_folder_node['ID']
            for art, tids in artist_map.items():
                if len(tids) >= artist_threshold:
                    db.create_playlist(art, parent_id=art_folder_id, tracks=tids)

        # 3. Labels Folder
        lbl_folder_node = next((p for p in db.playlists if p['Name'] == "By Label" and p['ParentID'] == auto_folder_id), None)
        if not lbl_folder_node:
            lbl_folder_node = db.create_playlist("By Label", parent_id=auto_folder_id, is_folder=True)

        if lbl_folder_node:
            lbl_folder_id = lbl_folder_node['ID']
            for lbl, tids in label_map.items():
                if len(tids) >= label_threshold:
                    db.create_playlist(lbl, parent_id=lbl_folder_id, tracks=tids)

        db.save()
        return True

    @staticmethod
    def smart_rename(track_ids, pattern):
        results = {"success": [], "errors": []}
        for tid in track_ids:
            track = db.get_track_details(tid)
            if not track: continue
            new_name = pattern.replace("%Artist%", track.get('Artist','')).replace("%Title%", track.get('Title','')).replace("%BPM%", str(round(track.get('BPM',0)))).replace("%Key%", track.get('Key',''))
            new_name = re.sub(r'[<>:"/\\|?*]', '', new_name).strip()
            src = Path(track['path'])
            target = src.parent / (new_name + src.suffix)
            try:
                if target.exists() and target != src: target = src.parent / (f"{new_name}_1{src.suffix}")
                os.rename(src, target)
                db.update_track_path(tid, str(target))
                results["success"].append({"id": tid})
            except Exception as e: results["errors"].append(str(e))
        return results

class SettingsManager:
    CONFIG = Path("settings.json")
    DEFAULT = {
        "default_export_format": "wav",
        "default_export_dir": "",  # If empty, falls back to EXPORT_DIR (./exports). User can pick any folder.
        "theme": "dark",
        "auto_snap": True,
        "db_path": "",
        "artist_view_threshold": 0,
        "ranking_filter_mode": "all", # all, unrated, untagged
        "insights_playcount_threshold": 0,
        "insights_bitrate_threshold": 320,
        "hide_streaming": False,
        "remember_lib_mode": False,
        "last_lib_mode": "xml",
        "soundcloud_auth_token": "",
        "sc_sync_folder_id": None,   # local Rekordbox playlist ID to create SC playlists inside
        "sc_download_format": "auto",  # "auto" (keep source codec) | "aiff" (convert to PCM AIFF lossless)
        "legacy_pdb_stub": False,  # opt-in: write header-only export.pdb for CDJ-2000nxs2 (experimental — see app/usb_pdb.py)
        "scan_folders": [],          # absolute paths watched for new audio files (FolderWatcher)
    }
    # Caps mirror app/main.py SetReq validator. Load-path is intentionally
    # tolerant: legacy settings.json files predating the validator must keep
    # the app bootable. We strip silently (logger.info, not warning) so the
    # next user-driven Save round-trips a clean payload through SetReq.
    _LOAD_MAX_KEYS = 64
    _LOAD_MAX_KEY_LEN = 64
    _LOAD_MAX_VALUE_BYTES = 8 * 1024
    _LOAD_MAX_LIST_ITEMS = 256

    @classmethod
    def _sanitize_loaded(cls, data: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in data.items():
            if not isinstance(k, str) or len(k) > cls._LOAD_MAX_KEY_LEN:
                logger.info(
                    "settings: dropped key=%s reason=key_len>%d",
                    str(k)[:64], cls._LOAD_MAX_KEY_LEN,
                )
                continue
            if isinstance(v, list) and len(v) > cls._LOAD_MAX_LIST_ITEMS:
                logger.info(
                    "settings: dropped key=%s reason=list_items>%d",
                    k, cls._LOAD_MAX_LIST_ITEMS,
                )
                continue
            if isinstance(v, dict) and len(v) > cls._LOAD_MAX_KEYS:
                logger.info(
                    "settings: dropped key=%s reason=dict_keys>%d",
                    k, cls._LOAD_MAX_KEYS,
                )
                continue
            try:
                size = len(json.dumps(v, separators=(",", ":"), default=str).encode("utf-8"))
            except (TypeError, ValueError):
                logger.info("settings: dropped key=%s reason=unserializable", k)
                continue
            if size > cls._LOAD_MAX_VALUE_BYTES:
                logger.info(
                    "settings: dropped key=%s reason=value_bytes>%d",
                    k, cls._LOAD_MAX_VALUE_BYTES,
                )
                continue
            out[k] = v
        return out

    @classmethod
    def load(cls) -> dict[str, Any]:
        try:
            with open(cls.CONFIG, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                logger.warning(
                    "services.SettingsManager.load: non-dict payload, using defaults",
                )
                return dict(cls.DEFAULT)
            sanitized = cls._sanitize_loaded(raw)
            return {**cls.DEFAULT, **sanitized}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                "services.SettingsManager.load: falling back to defaults — %s", e,
            )
            return dict(cls.DEFAULT)

    @classmethod
    def save(cls, cfg: dict[str, Any]) -> None:
        """Write settings atomically: tmp file + os.replace.

        A crash mid-write would otherwise leave ``settings.json`` truncated
        and the next startup would silently fall back to defaults.
        """
        payload = json.dumps(cfg, indent=2)
        tmp_path = cls.CONFIG.with_suffix(cls.CONFIG.suffix + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_path, cls.CONFIG)
        except OSError as e:
            logger.error(
                "services.SettingsManager.save: write failed err=%s", e,
            )
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise

class MetadataManager:
    MAPPINGS_FILE = Path("metadata_mappings.json")

    @classmethod
    def load(cls) -> dict[str, dict[str, str]]:
        if not cls.MAPPINGS_FILE.exists():
            return {"artists": {}, "labels": {}, "albums": {}}
        try:
            with open(cls.MAPPINGS_FILE, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                "services.MetadataManager.load: returning empty mappings — %s", e,
            )
            return {"artists": {}, "labels": {}, "albums": {}}

    @classmethod
    def save(cls, data: dict[str, dict[str, str]]) -> None:
        with open(cls.MAPPINGS_FILE, "w", encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def add_mapping(cls, category: str, source_name: str, target_name: str) -> None:
        data = cls.load()
        if category not in data: data[category] = {}
        data[category][source_name] = target_name
        cls.save(data)

    @classmethod
    def get_mapped_name(cls, category: str, name: str) -> str:
        data = cls.load()
        return data.get(category, {}).get(name, name)

class BeatAnalyzer:
    """
    Track analyzer used by POST /api/track/{tid}/analyze.
    Now delegates to the production AnalysisEngine for BPM, Key, Phrases & LUFS,
    then enriches the result with drop detection and dynamic grid logic.
    """

    # Import the production engine once
    _engine_available = False
    _engine_checked = False

    @classmethod
    def _check_engine(cls):
        if cls._engine_checked:
            return cls._engine_available
        cls._engine_checked = True
        try:
            from .analysis_engine import run_full_analysis as _rfa
            cls._run_engine = staticmethod(_rfa)
            cls._engine_available = True
        except ImportError:
            cls._engine_available = False
        return cls._engine_available

    @staticmethod
    def analyze(path: str):
        """
        Full track analysis with Rekordbox-grade accuracy.
        Delegates to analysis_engine.py, then enriches with drop detection
        and dynamic grid for backward compatibility.
        """
        logger.info(f"Starting analysis for: {path}")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Audio file not found: {path}")

        # --- Try production engine first ---
        if BeatAnalyzer._check_engine():
            try:
                from .analysis_engine import run_full_analysis
                result = run_full_analysis(path)

                if result.get("status") == "ok":
                    logger.info(
                        f"Engine result: BPM={result['bpm']}, Key={result['key']}, "
                        f"Beats={result['beat_count']}, Phrases={len(result.get('phrases', []))}"
                    )

                    # Convert beats to legacy format for frontend
                    full_beat_list = []
                    for b in result.get("beats", []):
                        full_beat_list.append({
                            "time": round(b["time_ms"] / 1000.0, 4),
                            "bpm": b["tempo"] / 100.0,
                            "beat": b["beat_number"],
                            "metro": "4/4"
                        })

                    # Convert phrases to legacy format
                    phrases_legacy = []
                    for p in result.get("phrases", []):
                        phrases_legacy.append({
                            "name": p.get("label", "PHRASE"),
                            "start": p.get("start_time", 0),
                            "end": p.get("end_time", 0),
                            "color": "#3b82f6" if p.get("mood") != "high" else "#ef4444"
                        })

                    # Detect drop time from phrases
                    drop_time = 0.0
                    for p in result.get("phrases", []):
                        if p.get("label") == "Drop":
                            drop_time = p.get("start_time", 0.0)
                            break

                    return {
                        "bpm": result["bpm"],
                        "key": result["key"],
                        "camelot": result.get("camelot", ""),
                        "phrases": phrases_legacy,
                        "lufs": result.get("lufs", -100.0),
                        "peak": result.get("peak", 0.0),
                        "beats": full_beat_list,
                        "tempoAnchors": result.get("tempo_anchors", []),
                        "totalTime": result.get("duration", 0),
                        "dropTime": drop_time,
                        # New v2 fields
                        "key_confidence": result.get("key_confidence", 0),
                        "beat_method": result.get("beat_method", ""),
                        "key_method": result.get("key_method", ""),
                        "openkey": result.get("openkey", ""),
                    }

            except Exception as e:
                logger.warning(f"Production engine failed, falling back to legacy: {e}")

        # --- Legacy fallback (original implementation) ---
        return BeatAnalyzer._legacy_analyze(path)

    @staticmethod
    def _legacy_analyze(path: str):
        """Legacy analysis fallback using basic librosa."""
        try:
            import librosa
            import numpy as np
            from scipy.ndimage import gaussian_filter1d
        except ImportError:
            raise RuntimeError("Audio analysis dependencies missing (librosa, scipy).")

        try:
            y, sr = librosa.load(path, sr=None)
            duration = librosa.get_duration(y=y, sr=sr)

            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            hop_length = 512
            win_length_sec = 8.0
            win_length_frames = int(win_length_sec * sr / hop_length)

            candidates = [None, 140, 150, 160]
            best_bpm, best_beats, max_score = 0, [], -1

            for start_bpm in candidates:
                kw = {"onset_envelope": onset_env, "sr": sr}
                if start_bpm:
                    kw["start_bpm"] = start_bpm
                tempo, beats = librosa.beat.beat_track(**kw)
                tempo = float(tempo)
                if len(beats) > 2:
                    intervals = np.diff(beats)
                    score = 1.0 / (np.std(intervals) + 1e-6)
                    if tempo < 100.0: score *= 0.5
                    if 130.0 <= tempo <= 160.0: score *= 1.2
                    if score > max_score:
                        max_score = score
                        best_bpm = float(tempo)
                        best_beats = beats

            bpm_global = float(best_bpm) if best_bpm else 128.0
            if 125.0 <= bpm_global <= 165.0 and abs(bpm_global - round(bpm_global)) < 0.35:
                bpm_global = float(round(bpm_global))

            beat_times = librosa.frames_to_time(best_beats, sr=sr) if len(best_beats) > 0 else np.array([])
            beat_duration = 60.0 / bpm_global if bpm_global > 0 else 0.5

            full_beat_list = []
            curr_time = float(beat_times[0]) if len(beat_times) > 0 else 0.0
            while curr_time < duration:
                full_beat_list.append({
                    "time": round(curr_time, 4),
                    "bpm": bpm_global,
                    "beat": (len(full_beat_list) % 4) + 1,
                    "metro": "4/4"
                })
                curr_time += beat_duration

            key_info = BeatAnalyzer.detect_key(y, sr)
            lufs = BeatAnalyzer.calculate_lufs(y, sr)

            return {
                "bpm": round(bpm_global, 2),
                "key": key_info["key"],
                "camelot": key_info["camelot"],
                "phrases": [],
                "lufs": lufs,
                "peak": round(float(np.max(np.abs(y))), 4),
                "beats": full_beat_list,
                "tempoAnchors": [{"time": 0.0, "bpm": round(bpm_global, 3), "beat": 1, "metro": "4/4"}],
                "totalTime": duration,
                "dropTime": 0.0
            }
        except Exception as e:
            logger.error(f"Legacy analysis failed for {path}: {e}", exc_info=True)
            raise RuntimeError(f"Audio analysis failed: {e!s}")

    @staticmethod
    def detect_key(y, sr):
        """Key detection -- delegates to engine if available, else basic K-S."""
        try:
            from .analysis_engine import _ensure_libs
            from .analysis_engine import detect_key as engine_detect_key
            if _ensure_libs():
                result = engine_detect_key(y, sr)
                return {"key": result.get("key", "Unknown"), "camelot": result.get("camelot", "")}
        except ImportError:
            pass

        # Fallback: basic chroma correlation
        import librosa
        import numpy as np

        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = np.mean(chroma, axis=1)

        major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
        minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

        major_corr, minor_corr = [], []
        for i in range(12):
            major_corr.append(np.corrcoef(chroma_mean, np.roll(major_profile, i))[0, 1])
            minor_corr.append(np.corrcoef(chroma_mean, np.roll(minor_profile, i))[0, 1])

        maj_idx, maj_score = np.argmax(major_corr), max(major_corr)
        min_idx, min_score = np.argmax(minor_corr), max(minor_corr)

        keys = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        cam_maj = ['8B', '3B', '10B', '5B', '12B', '7B', '2B', '9B', '4B', '11B', '6B', '1B']
        cam_min = ['5A', '12A', '7A', '2A', '9A', '4A', '11A', '6A', '1A', '8A', '3A', '10A']

        if maj_score > min_score:
            return {"key": keys[maj_idx] + " Major", "camelot": cam_maj[maj_idx]}
        return {"key": keys[min_idx] + " Minor", "camelot": cam_min[min_idx]}

    @staticmethod
    def detect_phrases(y, sr, bpm, duration):
        """Phrase detection -- delegates to engine if available."""
        try:
            from .analysis_engine import _ensure_libs
            from .analysis_engine import detect_phrases as engine_detect_phrases
            if _ensure_libs():
                phrases = engine_detect_phrases(y, sr, bpm, duration)
                return [
                    {"name": p.get("label", "PHRASE"),
                     "start": p.get("start_time", 0), "end": p.get("end_time", 0),
                     "color": "#3b82f6"}
                    for p in phrases
                ]
        except ImportError:
            pass

        # Fallback: basic MFCC segmentation
        import librosa
        import numpy as np
        try:
            hop_length = 2048
            mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=hop_length)
            n_segments = min(12, max(4, int(duration / 20)))
            boundaries = librosa.segment.agglomerative(mfcc, n_segments)
            boundary_times = librosa.frames_to_time(boundaries, sr=sr, hop_length=hop_length)
            boundary_times = np.sort(np.unique(np.concatenate(([0], boundary_times, [duration]))))

            phrases = []
            for i in range(len(boundary_times) - 1):
                start, end = boundary_times[i], boundary_times[i + 1]
                if i == 0: label = "INTRO"
                elif i == len(boundary_times) - 2: label = "OUTRO"
                else: label = f"PHRASE {i}"
                phrases.append({"name": label, "start": round(float(start), 3),
                                "end": round(float(end), 3), "color": "#3b82f6"})
            return phrases
        except Exception as e:
            logger.warning(f"Phrase detection failed: {e}")
            return []

    @staticmethod
    def calculate_lufs(y, sr):
        """LUFS calculation -- delegates to engine if available."""
        try:
            from .analysis_engine import _ensure_libs
            from .analysis_engine import calculate_lufs as engine_lufs
            if _ensure_libs():
                return engine_lufs(y, sr)
        except ImportError:
            pass

        # Fallback: simple RMS-based approximation
        import numpy as np
        try:
            mean_sq = np.mean(y ** 2)
            return round(float(10 * np.log10(mean_sq + 1e-12) + 0.691), 2)
        except Exception:
            return -100.0

class ImportManager:
    @staticmethod
    def process_import(file_path: Path, analysis_result: dict | None = None):
        """Orchestrates analysis and library insertion for a new file.

        analysis_result: optional, full output of run_full_analysis. If passed,
        re-analysis is skipped and beatgrid/cues/waveform are taken from there.
        """
        logger.info(f"Processing new import: {file_path}")
        try:
            # 1. Analyze (skip if pre-computed result provided)
            if analysis_result:
                analysis = {
                    "bpm": analysis_result.get("bpm", 0),
                    "key": analysis_result.get("key", ""),
                    "camelot": analysis_result.get("camelot", ""),
                    "phrases": analysis_result.get("phrases", []),
                    "lufs": analysis_result.get("lufs", 0),
                    "peak": analysis_result.get("peak", 0),
                    "totalTime": analysis_result.get("duration", 0),
                    "beats": analysis_result.get("beats", []),
                    "hot_cues": analysis_result.get("hot_cues", []),
                    "memory_cues": analysis_result.get("memory_cues", []),
                }
            else:
                analysis = BeatAnalyzer.analyze(str(file_path))

            # 1.5 Extract Cover Art
            artwork_path = ""
            try:
                # Ensure covers dir exists
                COVERS_DIR = Path(REKORDBOX_ROOT).parent / "app" / "data" / "covers"
                COVERS_DIR.mkdir(parents=True, exist_ok=True)

                # Each import re-extracts cover art rather than hash-matching against existing
                # cover files; the disk hit is negligible vs. the cost of the surrounding
                # mutagen probe, and dedup would need a SHA index we don't currently maintain.
                has_art = False
                art_data = None

                if file_path.suffix.lower() == ".mp3":
                    try:
                        audio = ID3(file_path)
                        for tag in audio.values():
                            if isinstance(tag, APIC):
                                art_data = tag.data
                                break
                    except Exception as e:
                        logger.debug(
                            "services: ID3 artwork lookup failed for %s (%s)",
                            file_path, e,
                        )
                elif file_path.suffix.lower() == ".flac":
                    try:
                        audio = FLAC(file_path)
                        if audio.pictures:
                            art_data = audio.pictures[0].data
                    except Exception as e:
                        logger.debug(
                            "services: FLAC artwork lookup failed for %s (%s)",
                            file_path, e,
                        )

                if art_data:
                    # Save with unique name based on file stem
                    bg_name = f"{file_path.stem}_{int(time.time())}.jpg"
                    bg_path = COVERS_DIR / bg_name
                    with open(bg_path, "wb") as f:
                        f.write(art_data)
                    artwork_path = str(bg_path.relative_to(Path(REKORDBOX_ROOT).parent_path if hasattr(Path(REKORDBOX_ROOT), "parent_path") else Path(REKORDBOX_ROOT).parent))
                    # Fix path for frontend: absolute or relative strictly?
                    # Let's use absolute path for internally managed, but relative for portability?
                    # Rekordbox uses absolute. Let's stick to absolute for now to be safe with serving.
                    artwork_path = str(bg_path.resolve())
                    logger.info(f"Extracted artwork to {artwork_path}")
            except Exception as e:
                logger.warning(f"Failed to extract artwork: {e}")

            # 2. Read native tags (ID3/Vorbis/MP4/WAV-RIFF) before falling
            # back to filename-based defaults. Without this every imported
            # track ended up as Artist="New Import" / Album="Imported" which
            # then poisoned the USB folder layout (PIONEER/Contents/<Artist>
            # /<Album>/file). See audio_tags.read_tags for the probe order.
            try:
                from . import audio_tags
                tags = audio_tags.read_tags(file_path)
            except Exception as exc:
                logger.warning(f"Tag read failed for {file_path}: {exc}")
                tags = {}

            title = tags.get("title") or file_path.stem
            artist = tags.get("artist") or "Unknown Artist"
            album = tags.get("album") or ""
            genre = tags.get("genre") or ""
            comment = tags.get("comment") or ""
            try:
                year = int(tags.get("year") or 0)
            except (TypeError, ValueError):
                year = 0

            logger.info(
                "Import metadata resolved: title=%r artist=%r album=%r genre=%r year=%s",
                title, artist, album, genre, year,
            )

            # 3. Prepare track data
            track_data = {
                "Title": title,
                "Artist": artist,
                "Album": album,
                "Genre": genre,
                "Comment": comment,
                "ReleaseYear": year,
                "ISRC": tags.get("isrc") or "",
                "BPM": analysis["bpm"],
                "Key": analysis["key"],
                "Camelot": analysis["camelot"],
                "phrases": analysis.get("phrases", []),
                "Loudness": analysis.get("lufs", 0),
                "Peak": analysis.get("peak", 0),
                "path": str(file_path.absolute()),
                "TotalTime": analysis["totalTime"],
                "beatGrid": analysis["beats"],
                "Artwork": artwork_path,
                "positionMarks": []
            }

            # 3. Add to Collection
            tid = db.add_track(track_data)
            logger.info(f"Import successful! Track ID: {tid}")

            # 3.5 Mirror analysis into the audio file's native tags (best-effort).
            try:
                from . import audio_tags
                from .services import SettingsManager as _SM
                cfg = _SM.load() if hasattr(_SM, "load") else {}
                if cfg.get("write_tags_to_files", True):
                    audio_tags.write_tags(file_path, {
                        "BPM": track_data.get("BPM"),
                        "Key": track_data.get("Key"),
                        "Comment": track_data.get("Comment"),
                    })
            except Exception as e:
                logger.debug(f"ID3 sync after import skipped: {e}")

            # 4. Add to "Import" Playlist
            # Case-insensitive search
            import_playlists = [p for p in db.playlists if p['Name'].lower() == "import"]

            if not import_playlists:
                logger.info("Creating 'Import' playlist...")
                target_pl = db.create_playlist("Import")
            else:
                # If multiple found, use the first one (or arguably the one with most tracks, but first is stable)
                if len(import_playlists) > 1:
                    logger.warning(f"Found {len(import_playlists)} 'Import' playlists. Using the first one.")
                    # Optional: We could consolidate here, but that might be a heavy operation for an import.
                    # For now, just pick one.
                target_pl = import_playlists[0]

            if target_pl:
                pid = target_pl['ID']
                # Check if track is already in playlist to avoid duplicates
                current_tracks = db.get_playlist_tracks(pid)
                if not any(str(t.get('ID') or t.get('id')) == str(tid) for t in current_tracks):
                     if db.add_track_to_playlist(pid, tid):
                         logger.info(f"Added track {tid} to 'Import' playlist ({pid})")

                         # AUTO-EXPORT: Trigger Bridge XML generation so Rekordbox sees it
                         try:
                             from .rekordbox_bridge import RekordboxBridge
                             export_path = Path(REKORDBOX_ROOT).parent / "exports" / "rekordbox_export.xml"
                             RekordboxBridge.export_xml([str(tid)], export_path)
                             logger.info(f"Auto-export triggered for track {tid} to {export_path}")
                         except Exception as exp_err:
                             logger.warning(f"Auto-export failed: {exp_err}")
                else:
                     logger.info(f"Track {tid} already in 'Import' playlist.")

            # 5. Write ANLZ sidecar (DAT/EXT/2EX) so USB-sync can copy it onto
            # the stick — gives CDJ-3000 cues, beatgrid and waveform data.
            # Best-effort: never fail an import because of this.
            try:
                from .anlz_sidecar import write_companion_anlz
                full_result = analysis_result if analysis_result else None
                # If a tracker task is bound to this thread, surface ANLZ-stage progress
                try:
                    from . import import_tracker
                    if hasattr(threading.current_thread(), "_lms_import_tid"):
                        import_tracker.update(
                            threading.current_thread()._lms_import_tid,
                            status="ANLZ", progress=85,
                        )
                except Exception:
                    pass
                write_companion_anlz(file_path, full_result)
            except Exception as anlz_err:
                logger.debug(f"ANLZ sidecar skipped for {file_path}: {anlz_err}")

            return tid, analysis
        except Exception as e:
            logger.error(f"Import manager failed for {file_path}: {e}", exc_info=True)
            raise

class ProjectManager:
    PROJECTS_DIR = Path("PRJ")

    @staticmethod
    def ensure_dir() -> None:
        ProjectManager.PROJECTS_DIR.mkdir(exist_ok=True)

    @staticmethod
    def save_project(name: str, data: dict[str, Any]) -> str:
        ProjectManager.ensure_dir()
        filename = ProjectManager.PROJECTS_DIR / f"{name}.prj"
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Failed to save project {name}: {e}")
            raise e

    @staticmethod
    def load_project(name: str) -> dict[str, Any] | None:
        filename = ProjectManager.PROJECTS_DIR / f"{name}.prj"
        if not filename.exists():
             # Try without extension if passed
             filename = ProjectManager.PROJECTS_DIR / name
             if not filename.exists(): raise FileNotFoundError("Project not found")

        try:
            with open(filename, encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load project {name}: {e}")
            raise e

    @staticmethod
    def list_projects() -> list[str]:
        ProjectManager.ensure_dir()
        projects = []
        for f in ProjectManager.PROJECTS_DIR.glob("*.prj"):
            projects.append({
                "name": f.stem,
                "updated": os.path.getmtime(f)
            })
        # Sort by newest
        projects.sort(key=lambda x: x['updated'], reverse=True)
        return projects
