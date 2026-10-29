"""Read-only, bounded macOS process-memory monitoring; no app API calls or OCR.

Run: python tests/manual/monitor_macos_memory.py --output logs/memory-monitor --hours 8
The report is updated after every sample. Process identity includes its native
start time, so restarts and PID reuse cannot masquerade as memory reclamation.
"""
import argparse
import ctypes
from datetime import datetime
import json
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Rome")
APP = "/Applications/OpenReLife.app/Contents/MacOS/OpenReLife"


class Usage(ctypes.Structure):
    # rusage_info_v0, macOS SDK sys/resource.h.
    _fields_ = [("uuid", ctypes.c_ubyte * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "user", "system", "pkg", "interrupt", "pageins", "wired",
            "resident", "footprint", "start", "exit",
        )
    ]


def local_time(epoch):
    return datetime.fromtimestamp(epoch, TZ).isoformat(timespec="seconds")


def analyze(samples, interval, reference_mib=1554):
    valid = [row for row in samples if row.get("backend")]
    result = dict(assessment="insufficient_observation", alerts=[],
                  valid_samples=len(valid), samples=len(samples))
    if not valid:
        return result
    last = valid[-1]
    identity = (last["backend"]["pid"], last["backend"]["start"])
    segment = []
    for row in reversed(samples):
        backend = row.get("backend")
        if not backend or (backend["pid"], backend["start"]) != identity:
            break
        segment.append(row)
    segment.reverse()
    # If the app is currently absent, report that instead of a previous plateau.
    if not segment:
        result["assessment"] = "backend_not_observed"
        return result
    start, end = segment[0]["epoch"], segment[-1]["epoch"]
    elapsed = end - start
    covered = sum(min(b["epoch"] - a["epoch"], interval * 1.5)
                  for a, b in zip(segment, segment[1:]))
    result.update(backend_pid=identity[0], segment_seconds=round(elapsed, 1),
                  coverage=round(covered / elapsed, 3) if elapsed else 0,
                  current_mib=last["backend"]["footprint_mib"],
                  peak_sampled_mib=max(row["backend"]["footprint_mib"] for row in segment),
                  reference_mib=reference_mib)
    early = [row for row in segment if row["epoch"] <= start + 15 * 60]
    recent = [row for row in segment if row["epoch"] >= end - 15 * 60]
    if len(early) < 10 or len(recent) < 10 or elapsed < 15 * 60:
        return result
    baseline = statistics.median(row["backend"]["footprint_mib"] for row in early)
    current = statistics.median(row["backend"]["footprint_mib"] for row in recent)
    result.update(baseline_median_mib=round(baseline, 2), recent_median_mib=round(current, 2),
                  growth_mib=round(current - baseline, 2))
    if current > reference_mib + 1024:
        result["alerts"].append("above_post_fix_reference")
    if current >= 4096:
        result["alerts"].append("high_sustained_footprint")
    if elapsed >= 3600:
        span_hours = (statistics.median(row["epoch"] for row in recent)
                      - statistics.median(row["epoch"] for row in early)) / 3600
        slope = (current - baseline) / max(span_hours, 0.01)
        result["slope_mib_per_hour"] = round(slope, 2)
        if current - baseline >= 512 and slope >= 64:
            result["alerts"].append("sustained_growth")
        result["assessment"] = ("growth_requires_review" if result["alerts"] else
                                "no_sustained_growth_observed")
    elif result["alerts"]:
        result["assessment"] = "elevated_memory_requires_review"
    if result["coverage"] < 0.8:
        result["assessment"] = "incomplete_coverage"
    return result


def snapshot(lib):
    output = subprocess.run(["ps", "-axo", "pid=,ppid=,comm="],
                            capture_output=True, text=True, check=True, timeout=10).stdout
    processes = []
    for line in output.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) == 3:
            processes.append((int(fields[0]), int(fields[1]), fields[2]))
    roots = {pid for pid, _, command in processes if command == APP}
    family = set(roots)
    while True:
        children = {pid for pid, parent, _ in processes if parent in family}
        if children <= family:
            break
        family |= children
    backends = {pid for pid, parent, command in processes
                if parent in roots and "/OpenReLife/venv/bin/python" in command}
    measured = []
    for pid, parent, command in processes:
        if pid not in family:
            continue
        usage = Usage()
        if lib.proc_pid_rusage(pid, 0, ctypes.byref(usage)):
            continue  # Short-lived OCR workers can exit between enumeration and sampling.
        role = "backend" if pid in backends else "app" if pid in roots else "child"
        measured.append(dict(pid=pid, parent=parent, role=role, start=usage.start,
                             footprint_mib=round(usage.footprint / 2**20, 2),
                             resident_mib=round(usage.resident / 2**20, 2)))
    backend = next((row for row in measured if row["role"] == "backend"), None)
    now = time.time()
    return dict(epoch=now, observed_at=local_time(now), backend=backend, processes=measured)


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def report(output, state):
    write_json(output / "status.json", state)
    trend = state["trend"]
    lines = ["# Monitoraggio memoria OpenReLife", "",
             f"Stato: **{state['state']}**. Ultimo aggiornamento: {state['updated_at']}.",
             f"Avvio: {state['started_at']}. Termine previsto: {state['deadline_at']}.", "",
             f"Esito automatico: `{trend['assessment']}`.",
             "Le segnalazioni indicano dati da verificare, non una diagnosi automatica di leak.", "",
             f"Campioni validi del backend: {trend['valid_samples']} / {trend['samples']}.",
             f"Riavvii rilevati: {state['restarts']}. Interruzioni di campionamento: {state['gaps']}."]
    for key in ("backend_pid", "current_mib", "peak_sampled_mib", "baseline_median_mib",
                "recent_median_mib", "growth_mib", "slope_mib_per_hour", "coverage"):
        if key in trend:
            lines.append(f"- {key}: {trend[key]}")
    lines += ["", "Segnalazioni attuali: " + (", ".join(trend["alerts"]) or "nessuna"), "",
              "La stabilità non può essere conclusa prima di un'ora di osservazione continua.",
              "I riavvii iniziano una nuova serie; la sospensione del Mac crea intervalli senza dati.",
              "Il riferimento dopo la correzione è 1554 MiB. Le finestre di confronto usano mediane di 15 minuti.",
              "Soglie: +512 MiB con andamento positivo dopo un'ora; oltre 4 GiB persistenti;",
              "oppure oltre 1 GiB sopra il riferimento iniziale. I picchi isolati non bastano.", "",
              "Dettaglio: `samples.jsonl`, `events.jsonl`, `status.json`, riepiloghi `vmmap-*.txt`.",
              "Le metriche dei processi figli sono separate; nessun contenuto di schermate, titoli o OCR viene letto."]
    temporary = output / "report.md.tmp"
    temporary.write_text("\n".join(lines) + "\n")
    temporary.replace(output / "report.md")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hours", type=float, default=8)
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--vmmap-period", type=float, default=3600)
    options = parser.parse_args()
    if sys.platform != "darwin" or options.hours <= 0 or options.interval <= 0:
        parser.error("Requires macOS and positive duration/interval")
    options.output.mkdir(parents=True, exist_ok=True)
    if (options.output / "samples.jsonl").exists():
        parser.error("Use a new output directory to preserve previous evidence")
    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.proc_pid_rusage.restype = ctypes.c_int
    started = time.time()
    deadline = started + options.hours * 3600
    state = dict(state="running", monitor_pid=__import__("os").getpid(),
                 started_at=local_time(started), deadline_at=local_time(deadline),
                 deadline_epoch=deadline, interval_seconds=options.interval, restarts=0, gaps=0)
    samples, previous_identity, previous_alerts = [], None, set()
    last_vmmap = 0
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def event(kind, **details):
        with (options.output / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(dict(at=local_time(time.time()), event=kind, **details)) + "\n")

    while True:
        try:
            row = snapshot(lib)
        except Exception as error:
            row = dict(epoch=time.time(), observed_at=local_time(time.time()), backend=None,
                       processes=[], error=f"{type(error).__name__}: {error}")
            event("sampling_error", error=row["error"])
        if samples and row["epoch"] - samples[-1]["epoch"] > options.interval * 2.5:
            state["gaps"] += 1
            event("sampling_gap", seconds=round(row["epoch"] - samples[-1]["epoch"], 1))
        if row["backend"]:
            identity = (row["backend"]["pid"], row["backend"]["start"])
            if previous_identity is not None and identity != previous_identity:
                state["restarts"] += 1
                event("backend_restarted", previous_pid=previous_identity[0], pid=identity[0])
            previous_identity = identity
        elif samples and samples[-1].get("backend"):
            event("backend_absent")
        samples.append(row)
        with (options.output / "samples.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        trend = analyze(samples, options.interval)
        new_alerts = set(trend["alerts"]) - previous_alerts
        if new_alerts:
            event("memory_requires_review", reasons=sorted(new_alerts), trend=trend)
        previous_alerts = set(trend["alerts"])
        if (options.vmmap_period > 0 and row["backend"]
                and (row["epoch"] - last_vmmap >= options.vmmap_period or new_alerts)):
            last_vmmap = row["epoch"]
            stamp = datetime.fromtimestamp(last_vmmap, TZ).strftime("%Y%m%d-%H%M%S")
            try:
                with (options.output / f"vmmap-{stamp}.txt").open("w") as stream:
                    subprocess.run(["vmmap", "-summary", str(row["backend"]["pid"])],
                                   stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=30)
            except (OSError, subprocess.SubprocessError) as error:
                event("vmmap_error", error=str(error))
        state.update(updated_at=local_time(time.time()), trend=trend)
        if stopping or time.time() >= deadline:
            state["state"] = "interrupted" if stopping else "completed"
            state["completed_at"] = local_time(time.time())
        report(options.output, state)
        if state["state"] != "running":
            break
        # Short waits make termination responsive; the wall-clock deadline
        # includes sleep, and actual missing coverage is reported explicitly.
        next_sample = min(row["epoch"] + options.interval, deadline)
        while not stopping and time.time() < next_sample:
            time.sleep(max(0, min(1, next_sample - time.time())))


if __name__ == "__main__":
    main()
