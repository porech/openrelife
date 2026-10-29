"""Monitor global macOS memory and per-app attribution without reading app content.

Stops at an explicit wall-clock deadline; intended to run beside the OpenReLife
monitor. Samples are independent, so the existing process history is preserved.
"""
import argparse
import ctypes
from datetime import datetime
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Rome")
PRESSURE = {1: "normal", 2: "warning", 4: "critical"}


class Usage(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_ubyte * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "user", "system", "pkg", "interrupt", "pageins", "wired",
            "resident", "footprint", "start", "exit",
        )
    ]


def local_time(epoch):
    return datetime.fromtimestamp(epoch, TZ).isoformat(timespec="seconds")


def parse_vmstat(text):
    page_size = int(re.search(r"page size of (\d+) bytes", text).group(1))
    counts = {key.strip('"'): int(value) for key, value in
              re.findall(r'^(.+?):\s+(\d+)\.', text, re.MULTILINE)}
    mib = page_size / 2**20
    fields = {name: round(counts[source] * mib, 2) for name, source in (
        ("free_mib", "Pages free"), ("active_mib", "Pages active"),
        ("inactive_mib", "Pages inactive"), ("wired_mib", "Pages wired down"),
        ("file_backed_mib", "File-backed pages"),
        ("compressed_physical_mib", "Pages occupied by compressor"),
        ("compressed_logical_mib", "Pages stored in compressor"),
    )}
    fields.update(page_size=page_size, swapins_pages=counts["Swapins"],
                  swapouts_pages=counts["Swapouts"])
    return fields


def parse_sysctl(text):
    total = int(re.search(r"hw\.memsize:\s+(\d+)", text).group(1))
    used = float(re.search(r"vm\.swapusage:.*?used =\s*([\d.]+)M", text).group(1))
    pressure = re.search(r"kern\.memorystatus_vm_pressure_level:\s*(\d+)", text)
    code = int(pressure.group(1)) if pressure else None
    return dict(total_mib=round(total / 2**20, 2), swap_used_mib=used,
                pressure_code=code, pressure=PRESSURE.get(code, "unknown"))


def app_name(command):
    if "/openrelife/venv/" in command.lower():
        return "OpenReLife"  # Attribute the backend/OCR workers to their app.
    if ".app/" in command:
        return Path(command.split(".app/", 1)[0]).name
    return Path(command).name or command


def swap_delta(previous, current):
    if previous is None:
        return None
    before, after = previous["system"], current["system"]
    seconds = current["epoch"] - previous["epoch"]
    if (seconds <= 0 or before["page_size"] != after["page_size"]
            or any(after[key] < before[key] for key in ("swapins_pages", "swapouts_pages"))):
        return None  # Reboot/counter reset is not negative swap activity.
    multiplier = after["page_size"] / 2**20
    incoming = (after["swapins_pages"] - before["swapins_pages"]) * multiplier
    outgoing = (after["swapouts_pages"] - before["swapouts_pages"]) * multiplier
    return dict(seconds=round(seconds, 1), swapin_mib=round(incoming, 2),
                swapout_mib=round(outgoing, 2),
                swapout_mib_per_minute=round(outgoing * 60 / seconds, 2))


def collect(lib):
    def run(arguments):
        return subprocess.run(arguments, capture_output=True, text=True,
                              check=True, timeout=30).stdout
    system = parse_sysctl(run(["sysctl", "hw.memsize", "vm.swapusage",
                              "kern.memorystatus_vm_pressure_level"]))
    system.update(parse_vmstat(run(["vm_stat"])))
    output = run(["ps", "-axo", "pid=,ppid=,rss=,comm="])
    groups, processes = {}, []
    for line in output.splitlines():
        fields = line.strip().split(None, 3)
        if len(fields) != 4:
            continue
        pid, parent, rss, command = fields
        name = app_name(command)
        process = dict(pid=int(pid), parent=int(parent), app=name,
                       resident_mib=round(int(rss) / 1024, 2))
        usage = Usage()
        if not lib.proc_pid_rusage(int(pid), 0, ctypes.byref(usage)):
            process["footprint_mib"] = round(usage.footprint / 2**20, 2)
        group = groups.setdefault(name, dict(app=name, processes=0, resident_mib=0,
                                             footprint_mib=0, measured_footprints=0))
        group["processes"] += 1
        group["resident_mib"] += process["resident_mib"]
        if "footprint_mib" in process:
            group["footprint_mib"] += process["footprint_mib"]
            group["measured_footprints"] += 1
        processes.append(process)
    now = time.time()
    ranked = sorted(groups.values(), key=lambda row: row["footprint_mib"], reverse=True)
    for row in ranked:
        row["resident_mib"] = round(row["resident_mib"], 2)
        row["footprint_mib"] = round(row["footprint_mib"], 2)
    return dict(epoch=now, observed_at=local_time(now), system=system,
                top_apps=ranked[:20],
                top_resident_apps=sorted(ranked, key=lambda row: row["resident_mib"], reverse=True)[:20],
                top_processes=sorted(processes, key=lambda row: row.get("footprint_mib", 0),
                                     reverse=True)[:25], process_count=len(processes))


def write_report(output, state):
    temporary = output / "system-status.json.tmp"
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(output / "system-status.json")
    row = state.get("latest")
    lines = ["# RAM complessiva del Mac", "",
             f"Stato: **{state['state']}**. Ultimo aggiornamento: {state['updated_at']}.",
             f"Termine: {local_time(state['deadline_epoch'])}. Campioni: {state['samples']}.",
             f"Campioni con pressione elevata: {state['pressure_warnings']}. "
             f"Errori di misura: {state['sampling_errors']}.", ""]
    if row:
        system = row["system"]
        lines += [f"Pressione della memoria: **{system['pressure']}**.", "",
                  f"- RAM fisica: {system['total_mib']/1024:.1f} GiB",
                  f"- RAM libera: {system['free_mib']/1024:.2f} GiB",
                  f"- RAM cablata (wired): {system['wired_mib']/1024:.2f} GiB",
                  f"- RAM occupata dal compressore: {system['compressed_physical_mib']/1024:.2f} GiB",
                  f"- Pagine associate a file: {system['file_backed_mib']/1024:.2f} GiB",
                  f"- Swap occupato: {system['swap_used_mib']/1024:.2f} GiB"]
        if row.get("swap_delta"):
            delta = row["swap_delta"]
            lines.append(f"- Swap nel periodo ({delta['seconds']:.0f} s): "
                         f"{delta['swapout_mib']:.2f} MiB scritti, {delta['swapin_mib']:.2f} MiB letti")
        lines += ["", "## App con più memoria attribuita", "",
                  "| App/processo | Impronta GiB | RAM residente GiB | Processi misurati/totali |",
                  "| --- | ---: | ---: | ---: |"]
        for app in row["top_apps"][:12]:
            name = app["app"].replace("|", "\\|")
            lines.append(f"| {name} | {app['footprint_mib']/1024:.2f} | "
                         f"{app['resident_mib']/1024:.2f} | "
                         f"{app['measured_footprints']}/{app['processes']} |")
    lines += ["", "La sola RAM libera bassa non prova saturazione: macOS usa memoria anche per cache.",
              "La pressione di memoria e l'attività recente di swap sono i segnali principali.",
              "Lo swap già occupato non implica che il sistema stia scrivendo swap adesso.",
              "I gruppi sommano i processi dell'app: memorie condivise e attribuzioni parziali impediscono",
              "di considerarli una suddivisione esatta dei 32 GiB fisici.", "",
              "Dati: `system-samples.jsonl`, `system-events.jsonl`, `system-status.json`.",
              "Nessun contenuto di schermate, titoli, argomenti dei processi o OCR viene letto."]
    temporary = output / "system-report.md.tmp"
    temporary.write_text("\n".join(lines) + "\n")
    temporary.replace(output / "system-report.md")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deadline", type=float, required=True)
    parser.add_argument("--interval", type=float, default=300)
    options = parser.parse_args()
    if sys.platform != "darwin" or options.deadline <= time.time() or options.interval <= 0:
        parser.error("Requires macOS, a future deadline and positive interval")
    options.output.mkdir(parents=True, exist_ok=True)
    if (options.output / "system-samples.jsonl").exists():
        parser.error("Existing system samples must be preserved; choose a new output")
    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.proc_pid_rusage.restype = ctypes.c_int
    state = dict(state="running", monitor_pid=__import__("os").getpid(),
                 started_at=local_time(time.time()), deadline_epoch=options.deadline,
                 interval_seconds=options.interval, samples=0, pressure_warnings=0,
                 sampling_errors=0)
    previous, stopping = None, False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def event(kind, **details):
        with (options.output / "system-events.jsonl").open("a") as stream:
            stream.write(json.dumps(dict(at=local_time(time.time()), event=kind, **details)) + "\n")

    while True:
        began = time.time()
        try:
            row = collect(lib)
            row["swap_delta"] = swap_delta(previous, row)
            with (options.output / "system-samples.jsonl").open("a") as stream:
                stream.write(json.dumps(row) + "\n")
            state["samples"] += 1
            state["latest"] = row
            if row["system"]["pressure"] in ("warning", "critical"):
                state["pressure_warnings"] += 1
                event("system_memory_pressure", pressure=row["system"]["pressure"],
                      top_apps=row["top_apps"][:5], swap_delta=row["swap_delta"])
            if row["swap_delta"] and row["swap_delta"]["swapout_mib_per_minute"] >= 50:
                event("recent_swapout_activity", delta=row["swap_delta"])
            previous = row
        except Exception as error:
            state["sampling_errors"] += 1
            event("sampling_error", error=f"{type(error).__name__}: {error}")
        state["updated_at"] = local_time(time.time())
        if stopping or time.time() >= options.deadline:
            state["state"] = "interrupted" if stopping else "completed"
        write_report(options.output, state)
        if state["state"] != "running":
            break
        next_sample = min(began + options.interval, options.deadline)
        while not stopping and time.time() < next_sample:
            time.sleep(max(0, min(1, next_sample - time.time())))


if __name__ == "__main__":
    main()
