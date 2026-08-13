# eval — the B6 gate

The upgrade plan's decision point: *does the conform pipeline actually turn
arbitrary images into pixel art worth shipping?* Everything downstream is gated
on it, so the scores live here rather than in someone's memory.

`RESULTS.md` holds the run this project was gated on. **Verdict: proceed.**

## Running it

```bash
cp manifest.example.json manifest.json     # point it at your own images
ASEPRITE_PATH=/path/to/aseprite python eval/run_gate.py eval/manifest.json
```

Writes `results/` — per case a 1× sprite, an 8× view, an original-beside-output
comparison, and `raw.json` with the `detect_grid` output, the per-operation
cleanup counts, and colour/opacity measurements.

## Scoring

Five criteria per image, pass/fail, from the plan:

| Criterion | Passes when |
|---|---|
| Silhouette preserved | subject instantly recognisable at 1× |
| Palette discipline | at or under the target count, no muddy near-duplicates |
| Edge quality | hard edges, no AA fringe, regular diagonals |
| Detail retention | key features still legible |
| Is it pixel art? | zoomed in, every pixel is a deliberate square on a true grid |

The decision keys on the **64×64 character tier**, not the average: 4–5 criteria
there means the pipeline works and Phase C can start. Passing only on the
simplest image means the targets are too detailed for it — tune B4 rather than
building on top.

## On the reference images

**They are not committed, deliberately.** The images this project was scored
against are third-party: a character under copyright, two stock photographs, and
a watermarked stock sprite. None of them belong in an MIT-licensed repo, and a
watermarked one should not be used as a control at all — the watermarks
dominated grid detection and voided that measurement in the first run.

`refs/` holds art this project generated itself, which is redistributable and
serves as the clean pixel-art control: `detect_grid` should be confident on an
upscale of it and `cleanup` should barely touch it. That claim is also asserted
in the test suite (`tests/test_pixel_art_control.py`) so it cannot rot.

Bring your own for the rest. Good candidates: one iconic and simple, one mid
character, one detailed and photographic — spread the difficulty or the
go/adjust/stop decision has nothing to discriminate on.
