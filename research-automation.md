# Research automation

This repository now has a small automation loop for the incompleteness-theorem
research pages.

Run on Windows:

```powershell
.\tools\research_automation.cmd --write
```

It scans local HTML/PDF/SVG artifacts, groups them into research workstreams,
extracts theorem-like markers such as `Definition`, `Theorem`, `Question`, and
`Caution`, checks local links and fragments, and writes:

- `research-dashboard.md`
- `research-dashboard.json`

For CI or a quick local health check:

```powershell
.\tools\research_automation.cmd --check
```

The GitHub Actions workflow in `.github/workflows/research-automation.yml`
runs the same local-link check on pushes and pull requests.

On machines with a normal `python` command, the direct entry point is also
available:

```powershell
python tools/research_automation.py --write
```
