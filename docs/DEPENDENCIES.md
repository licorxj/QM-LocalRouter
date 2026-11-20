﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿﻿# LocalRouter - Dependencies

## System Requirements

| Requirement | Minimum Version | Recommended Version |
|-------------|-----------------|-------------------|
| Python | 3.10 | 3.12 |
| Node.js | 18 | 20 LTS |
| Node.js (ZCode local check-in script) | 22 | 22 LTS |
| npm | 9 | 10 |
| pip | 21 | 24 |

---

## Backend Dependencies (Python)

Source: `backend/requirements.txt`

| Package | Version | Purpose |
|---------|---------|---------|
| **fastapi** | 0.115.0 | Web framework for building the REST API |
| **uvicorn[standard]** | 0.30.0 | ASGI server for running FastAPI |
| **sqlalchemy[asyncio]** | 2.0.35 | Async ORM for database operations |
| **aiosqlite** | 0.20.0 | Async SQLite driver |
| **alembic** | 1.13.2 | Database schema migrations (active; config in `backend/alembic.ini`, versions in `backend/alembic/versions/`) |
| **pydantic** | 2.9.0 | Data validation and settings management |
| **pydantic-settings** | 2.5.0 | Environment variable / .env file loading |
| **httpx** | 0.27.0 | Async HTTP client for upstream API forwarding |
| **cryptography** | 43.0.0 | Fernet symmetric encryption for API keys |
| **python-dotenv** | 1.0.1 | .env file loading |
| **python-multipart** | 0.0.29 | Form/UploadFile support (backup/restore endpoints) |
| **litellm** | >=1.0.0 | LLM routing / forwarding kernel (lazy provider loading) |
| **pywebview** | 6.2.1 | Desktop shell native webview (Windows frozen build; skipped on Linux/Docker) |
| **psutil** | 7.2.2 | Service-type plugins (apiserver): detect/terminate processes holding a port; bundled into frozen builds |

### Backend Dev / Runtime Dependencies

These are included automatically with `uvicorn[standard]`:

- `uvloop` 鈥?Fast event loop (Linux only)
- `httptools` 鈥?Fast HTTP parser
- `websockets` 鈥?WebSocket support (for future features)

---

## Frontend Dependencies (Node.js)

Source: `frontend/package.json`

### Production Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| **react** | ^18.3.1 | UI library |
| **react-dom** | ^18.3.1 | React DOM renderer |
| **react-router-dom** | ^6.26.0 | Client-side routing |
| **@tanstack/react-query** | ^5.56.0 | Server state management / data fetching |
| **axios** | ^1.7.0 | HTTP client for API calls |
| **zustand** | ^4.5.0 | Lightweight client state management |
| **clsx** | ^2.1.0 | Conditional className utility |
| **tailwind-merge** | ^2.5.0 | Tailwind class merging without conflicts |
| **class-variance-authority** | ^0.7.0 | Type-safe component variants |
| **@radix-ui/react-slot** | ^1.1.0 | Radix UI primitive 鈥?Slot |
| **@radix-ui/react-dialog** | ^1.1.0 | Radix UI primitive 鈥?Dialog/Modal |
| **@radix-ui/react-dropdown-menu** | ^2.1.0 | Radix UI primitive 鈥?Dropdown Menu |
| **@radix-ui/react-select** | ^2.1.0 | Radix UI primitive 鈥?Select |
| **@radix-ui/react-switch** | ^1.1.0 | Radix UI primitive 鈥?Switch/Toggle |
| **@radix-ui/react-tabs** | ^1.1.0 | Radix UI primitive 鈥?Tabs |
| **@radix-ui/react-tooltip** | ^1.1.0 | Radix UI primitive 鈥?Tooltip |
| **@radix-ui/react-label** | ^2.1.0 | Radix UI primitive 鈥?Label |
| **@radix-ui/react-separator** | ^1.1.0 | Radix UI primitive 鈥?Separator |
| **lucide-react** | ^0.441.0 | Icon library |
| **recharts** | ^2.13.0 | Charting library (Dashboard) |
| **react-markdown** | ^10.1.0 | Markdown rendering in chat messages |
| **remark-gfm** | ^4.0.1 | GitHub Flavored Markdown plugin (tables, task lists, etc.) |

### Development Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| **typescript** | ^5.5.0 | TypeScript compiler |
| **vite** | 5.4.11 | Build tool and dev server |
| **@vitejs/plugin-react** | 4.3.1 | Vite React plugin |
| **tailwindcss** | ^3.4.12 | Utility-first CSS framework |
| **postcss** | ^8.4.47 | CSS post-processor |
| **autoprefixer** | ^10.4.20 | CSS vendor prefix auto-adding |
| **@types/react** | ^18.3.0 | React TypeScript types |
| **@types/react-dom** | ^18.3.0 | React DOM TypeScript types |

---

## Dependency Graph

```
LocalRouter
├── Backend (Python 3.10+)
│   ├── fastapi ─── uvicorn ─── httptools
│   │   └── pydantic ─── pydantic-settings ─── python-dotenv
│   ├── sqlalchemy[asyncio] ─── aiosqlite
│   │   └── alembic ─── schema migrations (sync sqlite engine, same app.db)
│   ├── httpx ─── HTTP forwarding
│   ├── cryptography ─── Fernet encryption
│   └── psutil ─── apiserver plugin port/process management
│
├── Frontend (Node.js 18+)
│   ├── react ─── react-dom ─── react-router-dom
│   ├── @tanstack/react-query ─── axios
│   ├── zustand ─── state management
│   ├── shadcn/ui (Radix primitives + Tailwind)
│   │   └── @radix-ui/* (dialog, select, switch, tabs, etc.)
│   └── vite ─── typescript
│       └── tailwindcss ─── postcss ─── autoprefixer
│
└── Database
    └── SQLite (zero-config, file-based)
```

---

## ZCode Local Check-in Script (optional)

`backend/data/zcode-autoclaim/claim-now.mjs` is a one-shot check-in helper for the locally
installed ZCode desktop client. Backend service `backend/app/services/zcode_claim.py`
detects the installation and runs the script through `POST /api/accounts/zcode/claim`.

- **Node.js >= 22 is required** for this script (built-in `WebSocket` / `fetch`, zero npm deps).
  Node 18/20 is sufficient for the frontend build but NOT for this script.
- Requires the ZCode desktop client to be installed. Set the `ZCODE_EXE` environment variable
  to override the executable path when installed in a non-standard location.
- Requires ZCode launched with `--remote-debugging-port=9222` (see `start-debug.cmd` in the
  script directory); the script drives the client's native UI via CDP and sends no HTTP requests itself.
- Bundled assets: `claim-now.mjs`, `README.md`, `start-debug.cmd`, `start-isolated.cmd`.

---

## Plugins — distributed on demand, NOT bundled with releases

Script plugins and binary worker plugins are **not shipped in the GitHub source tree or Release
assets**. This keeps release packages small and avoids distributing third-party binaries (e.g. the
`opencode2api` gateway) through GitHub. Users download only the plugins they need at runtime.

- **Distribution channel**: the app fetches the online catalog `index.json` from the COS release
  bucket (`https://qmzf-release-1323480989.cos.ap-guangzhou.myqcloud.com/ApiRouteManeger/plugins/index.json`),
  then downloads the selected plugin zip on demand. The catalog URL is defined by `CATALOG_URL` in
  `backend/app/services/script_plugin_manager.py`.
- **Release build flow** (`.github/workflows/build-release.yml`) stages only the backend executable,
  `frontend/dist`, `release/run.*` and `.env.example`. It deliberately does **not** copy any
  `data/plugins/` or `data/script_plugins/` content into the release zip / installer. Installed
  plugin state (`installed.json`, `packages/`, `staging/`) is local runtime data, ignored by git and
  never committed.
- **Service-type plugins** (`type: "apiserver"`, e.g. `opencode2api-apiserver`) contain a compiled
  gateway binary. One zip is published per OS/arch (`windows.x64`, `windows.arm64`, `linux.x64`,
  `linux.arm64`, `macos.x64`, `macos.arm64`); the client automatically selects the entry matching its
  platform. After install + enable, the gateway runs locally on `127.0.0.1:12005/v1` and is used by
  the `opencode-free` provider channel.

To add/update a plugin in the catalog, publish its zip and updated `index.json` to the COS release
bucket — no GitHub Release change is required.
