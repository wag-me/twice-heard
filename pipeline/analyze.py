"""Twice Heard pipeline: turboSETI ON/OFF cadence search on one work unit.

Usage:
    python analyze.py <archive.tar> <parametri.json> <work_dir> [expected_sha256]

Writes a JSON list of candidates {frequenza_hz, drift_hz_s, snr} to <work_dir>/candidati.json.
Deterministic; requires Python 3.11, numpy 1.26.4, turbo-seti 2.3.2, blimpy 2.1.4.

Safety: the archive is treated as data only. Only regular members named exactly
scan_0.fil ... scan_5.fil are extracted (no paths, no links); nothing else is read.
"""
import hashlib
import json
import os
import sys
import tarfile

N_SCANS = 6
ALLOWED = {f"scan_{i}.fil" for i in range(N_SCANS)}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_extract(archive, dest):
    os.makedirs(dest, exist_ok=True)
    found = {}
    with tarfile.open(archive, "r:*") as tar:
        for m in tar.getmembers():
            if m.name in ALLOWED and m.isreg() and m.name not in found:
                src = tar.extractfile(m)
                out = os.path.join(dest, m.name)
                with open(out, "wb") as f:
                    while chunk := src.read(1 << 20):
                        f.write(chunk)
                found[m.name] = out
    missing = ALLOWED - found.keys()
    if missing:
        raise SystemExit(f"archive is missing {sorted(missing)}")
    return [found[f"scan_{i}.fil"] for i in range(N_SCANS)]


def check_cadence(parametri):
    """find_event_pipeline assumes ON at even indices, OFF at odd ones."""
    scans = parametri.get("scansioni")
    if not scans:
        return
    labels = []
    for s in scans:
        txt = json.dumps(s).upper()
        labels.append("ON" if '"ON"' in txt else "OFF" if '"OFF"' in txt else "?")
    expected = ["ON" if i % 2 == 0 else "OFF" for i in range(N_SCANS)]
    if labels != expected:
        raise SystemExit(f"unexpected cadence {labels}, pipeline assumes {expected}")


def run(archive, parametri, work_dir, expected_sha=None):
    if expected_sha and sha256(archive).lower() != expected_sha.lower():
        raise SystemExit("SHA-256 mismatch: refusing to analyze")
    check_cadence(parametri)

    from turbo_seti.find_doppler.find_doppler import FindDoppler
    from turbo_seti.find_event.find_event_pipeline import find_event_pipeline

    # turboSETI splits paths on "/" only: use absolute forward-slash paths (works on Windows too).
    work_dir = os.path.abspath(work_dir).replace("\\", "/")
    fils = [p.replace("\\", "/") for p in safe_extract(archive, work_dir + "/fil")]
    out_dir = work_dir + "/out/"
    os.makedirs(out_dir, exist_ok=True)

    dats, h5s = [], []
    for fil in fils:
        FindDoppler(fil, max_drift=float(parametri["max_drift_hz_s"]), min_drift=0,
                    snr=float(parametri["snr_min"]), out_dir=out_dir, n_coarse_chan=1,
                    blank_dc=False).search()
        base = os.path.splitext(os.path.basename(fil))[0]
        dats.append(out_dir + base + ".dat")
        h5s.append(out_dir + base + ".h5")

    dat_lst = os.path.join(work_dir, "dat.lst")
    h5_lst = os.path.join(work_dir, "h5.lst")
    with open(dat_lst, "w") as f:
        f.write("\n".join(dats) + "\n")
    with open(h5_lst, "w") as f:
        f.write("\n".join(h5s) + "\n")

    df = find_event_pipeline(dat_lst, h5_lst, filter_threshold=3, number_in_cadence=6,
                             csv_name=os.path.join(work_dir, "events.csv"), saving=True)
    cands = []
    if df is not None and len(df):
        # One candidate per event: values as reported for the first ON scan.
        first = df[df["status"] == "on_table_1"] if "status" in df.columns else df
        for _, r in first.iterrows():
            cands.append({"frequenza_hz": float(r["Freq"]) * 1e6,
                          "drift_hz_s": float(r["DriftRate"]),
                          "snr": float(r["SNR"])})
    return cands


if __name__ == "__main__":
    archive, par_path, work_dir = sys.argv[1:4]
    sha = sys.argv[4] if len(sys.argv) > 4 else None
    with open(par_path) as f:
        parametri = json.load(f)
    cands = run(archive, parametri, work_dir, sha)
    # turboSETI prints to stdout, so the result goes to a file.
    with open(os.path.join(work_dir, "candidati.json"), "w") as f:
        json.dump(cands, f)
    print(f"{len(cands)} candidate(s) -> {os.path.join(work_dir, 'candidati.json')}")
