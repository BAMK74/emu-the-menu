# Python, FastAPI, SQLite and HTMX on a Raspberry Pi 5

Emu runs on a 2 GB Raspberry Pi 5 inside the home, reached over Tailscale. We chose a Python FastAPI backend with SQLite and server-rendered HTMX pages because they are light on a small device, a single database file is easy to back up, and Python suits the scoring engine and later import work. Node/TypeScript and Django were considered and rejected as, respectively, no better for this workload and heavier on a 2 GB Pi.
