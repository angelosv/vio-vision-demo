# Vio Vision Demo

Real-time match analysis demo — FastAPI + Next.js + Gemma 4 (local GPU).

## Stack

| Layer    | Tech                     | Puerto |
|----------|--------------------------|--------|
| Backend  | FastAPI + OpenCV + Gemma | 8000   |
| Frontend | Next.js 14 + Tailwind    | 3000   |

## Setup (primera vez)

```bash
cd vio-vision-demo
npm run setup
```

Esto instala dependencias Python (`backend/requirements.txt`) y Node (`web/`).

## Correr la demo

```bash
# Ambos servicios juntos (recomendado):
npm run dev

# O por separado:
npm run backend    # FastAPI en :8000
npm run frontend   # Next.js en :3000
```

## URL de video de prueba

```
https://firebasestorage.googleapis.com/v0/b/tipio-1ec97.appspot.com/o/bar.v.psg.1.ucl.01.10.2025.fullmatchsports.com.1080p.mp4?alt=media&token=593ce8a1-0462-4c37-98c3-e399f25e3853
```

## Estructura

```
vio-vision-demo/
├── backend/           ← FastAPI + visión (Python)
│   ├── main.py        ← Servidor, WebSocket, /start
│   ├── analyzer.py    ← Llama a Gemma vía curl
│   ├── stream_reader.py ← Lee frames del video
│   └── requirements.txt
├── web/               ← Frontend Next.js
│   ├── app/
│   │   └── page.tsx   ← UI principal
│   ├── components/
│   └── types/
├── package.json       ← Scripts raíz (npm run dev/backend/frontend/setup)
└── README.md
```

## Notas

- Gemma corre en GPU local (`100.99.128.76:11434`) — necesita GPU RTX activa.
- El backend transmite frames analizados por WebSocket al frontend.
- `app.py` y `frontend/` son la versión anterior (Streamlit). Ya no se usan.
