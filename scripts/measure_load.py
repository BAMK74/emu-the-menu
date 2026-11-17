"""Put a realistic load on a running emu container and report its peak memory and CPU.

For sizing the Server's quota (issue #4). Start emu and a Postgres with data first (see
scripts/smoke-container.sh for how), then:

    uv run python scripts/measure_load.py --base http://127.0.0.1:18080 --container emu-app

It samples `docker stats` all along, while it
  1. idles for a few seconds (the baseline),
  2. runs a household's pace: a few people at once opening the Menu, generating a week and saving
     it, importing a recipe from a web page and looking up a barcode, for --minutes,
  3. runs a burst: --burst people generating a week at the same moment, the worst a household does.
Importing and looking up reach the real recipe site and Open Food Facts, as they would on the
Server. docker stats reports CPU as a percentage of one core (100% = one core busy).
"""

import argparse
import concurrent.futures as cf
import re
import statistics
import subprocess
import threading
import time
from html import unescape

import httpx

RECIPE_URL = "https://www.bbcgoodfood.com/recipes/easy-chicken-curry"
BARCODE = "3017620422003"  # a common product, not in the test data's barcode cache
MENU_QUERY = "/menu?days=7&meal_types=breakfast&meal_types=lunch&meal_types=dinner&seed={seed}"


class Sampler(threading.Thread):
    """Reads docker stats for one container about twice a second."""

    def __init__(self, container: str):
        super().__init__(daemon=True)
        self.container = container
        self.samples: list[tuple[float, str, float, float]] = []  # time, phase, MiB, CPU %
        self.phase = "start"
        self._done = threading.Event()

    def run(self) -> None:
        while not self._done.is_set():
            out = subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.MemUsage}}|{{.CPUPerc}}",
                    self.container,
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            mem, cpu = out.split("|")
            self.samples.append(
                (time.time(), self.phase, _mib(mem.split("/")[0]), float(cpu.rstrip("%")))
            )
            self._done.wait(0.3)

    def stop(self) -> None:
        self._done.set()
        self.join()


def _mib(text: str) -> float:
    number, unit = re.fullmatch(r"\s*([\d.]+)\s*([KMG]i?B)\s*", text).groups()
    return (
        float(number)
        * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "kB": 1 / 1024, "MB": 1, "GB": 1024}[unit]
    )


def save_week(client: httpx.Client, seed: int) -> None:
    """Generate a week on the Menu page and save it with the form the page offers."""
    page = client.get(MENU_QUERY.format(seed=seed))
    page.raise_for_status()
    form = re.search(r'<form[^>]*action="/menu/save".*?</form>', page.text, re.S)
    if not form:
        return
    fields: dict[str, list[str]] = {}  # a name can repeat, such as one per meal
    for name, value in re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form.group(0)):
        fields.setdefault(unescape(name), []).append(unescape(value))
    client.post("/menu/save", data=fields).raise_for_status()


def household_round(base: str, seed: int, timings: dict) -> None:
    with httpx.Client(base_url=base, timeout=60, follow_redirects=True) as client:
        for name, step in (
            ("open stock", lambda: client.get("/").raise_for_status()),
            ("open menu", lambda: client.get("/menu").raise_for_status()),
            ("generate and save a week", lambda: save_week(client, seed)),
            ("open recipes", lambda: client.get("/recipes").raise_for_status()),
            ("import a recipe", lambda: client.post("/recipes/import", data={"url": RECIPE_URL})),
            ("look up a barcode", lambda: client.post("/lookup", data={"barcode": BARCODE})),
            ("open saved menus", lambda: client.get("/menus").raise_for_status()),
        ):
            started = time.perf_counter()
            step()
            timings.setdefault(name, []).append(time.perf_counter() - started)


def summary(samples, phase):
    rows = [s for s in samples if s[1] == phase]
    mem = [s[2] for s in rows]
    cpu = [s[3] for s in rows]
    return (
        f"{phase:<10} {len(rows):>4} samples   memory peak {max(mem):6.1f} MiB"
        f" (median {statistics.median(mem):6.1f})   CPU peak {max(cpu):6.1f}%"
        f" (median {statistics.median(cpu):5.1f}%)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:18080")
    parser.add_argument("--container", required=True)
    parser.add_argument("--people", type=int, default=3, help="at once, at a household's pace")
    parser.add_argument("--minutes", type=float, default=2)
    parser.add_argument("--burst", type=int, default=10)
    args = parser.parse_args()

    sampler = Sampler(args.container)
    sampler.start()
    sampler.phase = "idle"
    time.sleep(5)

    sampler.phase = "household"
    timings: dict[str, list[float]] = {}
    rounds = 0
    deadline = time.time() + args.minutes * 60
    with cf.ThreadPoolExecutor(args.people) as pool:
        while time.time() < deadline:
            first_seed = rounds * 10
            list(
                pool.map(
                    lambda n, first=first_seed: household_round(args.base, first + n, timings),
                    range(args.people),
                )
            )
            rounds += 1

    sampler.phase = "burst"
    with cf.ThreadPoolExecutor(args.burst) as pool:

        def generate(n):
            with httpx.Client(base_url=args.base, timeout=120, follow_redirects=True) as client:
                save_week(client, 1000 + n)

        list(pool.map(generate, range(args.burst)))
    sampler.phase = "after"
    time.sleep(5)
    sampler.stop()

    print(
        f"{args.people} people at a household's pace for {args.minutes:g} min ({rounds} rounds),"
        f" then {args.burst} at once generating a week"
    )
    for phase in ("idle", "household", "burst", "after"):
        print(summary(sampler.samples, phase))
    print("Slowest step times (s):")
    for name, values in timings.items():
        print(f"  {name:<26} median {statistics.median(values):5.2f}   max {max(values):5.2f}")


if __name__ == "__main__":
    main()
