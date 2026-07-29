# ParaGUIBench project site

The website is a backend-free React/Vite application designed for the GitHub Pages
project path `/ParaGUIBench/`. It loads a deterministic, privacy-safe task projection
from `public/data/site-data.json`; it does not fetch private APIs, use analytics, or
require runtime credentials.

## Local development

Node.js 22.12 or newer is required:

```bash
npm ci
npm test
npm run dev
```

The local URL is `http://127.0.0.1:5173/ParaGUIBench/`.

Build and validate the exact Pages artifact:

```bash
npm run build -- --base /ParaGUIBench/
node scripts/validate-static-site.mjs dist --base /ParaGUIBench/
```

Before building, regenerate or check the public data from the repository root:

```bash
python scripts/site/generate_site_data.py --repo-root .
python scripts/site/generate_site_data.py --repo-root . --check
```

## Dependency tree

```text
index.html
└── src/main.jsx
    └── src/App.jsx
        ├── src/content.js
        ├── src/components/*
        │   └── src/components/Icon.jsx
        ├── src/lib/taskData.js
        └── public/data/site-data.json
            └── scripts/site/generate_site_data.py
                ├── benchmark/manifests/release-v1.json
                ├── benchmark/manifests/runtime-support-v1.json
                └── benchmark/tasks/*.json (hash verification only)

vite.config.js
└── dist/
    └── scripts/validate-static-site.mjs

.github/workflows/site-ci.yml
└── test → public-data check → npm ci → build → static artifact gate

.github/workflows/pages.yml
└── trusted main build → Pages artifact upload → github-pages deployment
```

React owns UI state only. `src/lib/taskData.js` is a framework-independent pure-data
layer covered by Node's built-in test runner. The Python generator is the only component
allowed to project canonical metadata into the website dataset.

## Deployment

In repository settings, choose **Settings → Pages → Build and deployment → GitHub
Actions** once. Pull requests build and validate the site without deployment
permissions. Production deployment is restricted to `pkgunboat/ParaGUIBench` on `main`
and uses the protected `github-pages` environment.
