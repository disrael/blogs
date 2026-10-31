# Blog Archive (David Israel)

Public mirror and AI-readable Markdown archive of essays by David Israel (`@disrael`).

* **Live Human-Readable Site:** [https://disrael.github.io/blogs/](https://disrael.github.io/blogs/)
* **AI / LLM Discovery Index (`llms.txt`):** [https://disrael.github.io/blogs/llms.txt](https://disrael.github.io/blogs/llms.txt)

## Why This Repo Exists
Medium blocks automated web readers and AI tools via `robots.txt` and Cloudflare, and long single-page HTML articles often exceed the ~10,000-character fetch limit of LLM browsing tools. 

This repository converts Medium HTML exports into:
1. Clean, minimal HTML pages under `/posts/` with locally hosted images under `/assets/`.
2. Raw, zero-boilerplate Markdown files under `/raw/` automatically split on paragraph boundaries into `<= 8,500`-character parts (`.part1.md`, `.part2.md`, etc.) so AI tools can read 100% of every essay without truncation.

## How to Add New Medium Posts Later
1. Drop new Medium `.html` post files into `medium-export/`.
2. Run `./.venv/bin/python build.py` locally (so any new images are downloaded and cached into `./assets/`).
3. Commit and push to `main`—GitHub Actions will automatically deploy the updated site and `llms.txt`.
