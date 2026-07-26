# host_monitor.py does not live in this repo

The host monitor that actually runs is:

    /mnt/archie_brain/scripts/host_monitor.py

It is tracked in the **archie-platform** repository, not this one, and is
executed on the hub by the `host_monitor.service` systemd unit:

    ExecStart=/usr/bin/python3 /mnt/archie_brain/scripts/host_monitor.py \
        --daemon --output /mnt/archie_brain/host_monitor_data.json

## Why this file exists

A copy of `host_monitor.py` used to sit in this repo. By 2026-07-26 it had
diverged into a stale April snapshot — 7,106 lines against the live 7,016,
different md5 — and it ran nowhere. Editing it was a silent no-op, which is the
dangerous part: this is the intuitive place to look, because KSO is the
*consumer* of the monitor's output.

It was removed after measuring both copies (task #5251):

| | live | this repo's copy |
|---|---|---|
| functions | 133 | 113 |
| functions found ONLY here | — | **0** |
| functions only in live | 20 | — |

Zero unique functions, so nothing was lost. The 539 lines that existed only in
the old copy were older implementations of *shared* functions — it was longer in
raw lines while having fewer functions, the signature of a pre-refactor snapshot.

## Changing the host monitor

Edit `scripts/host_monitor.py` in **archie-platform**, open a PR there, and after
merge fast-forward `/mnt/archie_brain` and restart the service:

    sudo systemctl restart host_monitor.service

KSO reads its output from `/mnt/archie_brain/host_monitor_data.json`; it never
imports this module.

⚠️ Related, still unresolved: `kytran_system_operations/system_service.py` and
`kytran_system_operations/services/system_service.py` are both present with
different md5s. Determine which one is imported before editing either.
