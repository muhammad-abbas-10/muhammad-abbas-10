# Setup

This folder is the content of a GitHub profile repository. GitHub shows the
README of the repository named exactly like your username on your profile page.

## Publish it

- Use the repository `muhammad-abbas-10/muhammad-abbas-10` (it must be public).
- Copy everything in this folder into it, keeping the `.github` folder, and push to `main`.
- Open the repository's Actions tab and, if the "Cards" workflow has not started on its own, run it once with "Run workflow".
- Reload your profile page after the workflow finishes.

## Make sure the links work

- `Pizza-Whatsapp-AI-Bot` must be a public repository, or the project link will show a 404 to visitors.
- Receipt Reconciler has no link yet. To link it, publish the repository, then wrap its card in the README with a link to it.
- Set the "repo" field in `assets/projects.json` for a project to show its live language, stars and forks on the card.

## Change the content

- Featured projects: edit `assets/projects.json` and push. The workflow redraws the cards.
- Banner text and stack rows: edit `ROWS` in `scripts/banner/generate.py`, then run `pip install -r scripts/banner/requirements.txt` and `python scripts/banner/generate.py`.
- Photo in the banner: the face state is built from `assets/source/photo-crop.png` (a background-removed 600x680 cutout made from `assets/source/me.jpg`). To use a different photo run `python scripts/banner/generate.py --photo path/to/photo.png`. The face-mesh overlay uses coordinates in `assets/source/photo-landmarks.json` that fit the current photo, so edit or delete that file for a new photo.
- After regenerating the banner, bump `BANNER_VERSION` in that script and update the `banner-*.v2.svg` paths in `README.md`.
- Preview everything locally by opening `preview.html`.

## Optional extras

- Secret `METRICS_TOKEN`: a personal access token. Not required. If set, contribution numbers come from the GraphQL API.
- `scripts/radar.py`: draws a radar chart from a JSON file you write yourself (`python scripts/radar.py --data your.json -o assets/radar`).
- `scripts/dotify.py`: turns a photo into a dot-matrix SVG, if you ever want a portrait of your own.

## GitHub bio (Settings, then Bio)

CS Student @ FAST NUCES | Building AI & Agentic Systems | Python • C++ • Machine Learning • Open Source | Always learning, always shipping
