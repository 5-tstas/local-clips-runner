# run_local.py  (лежит в корне проекта, рядом с папкой app/)
import os, sys
sys.path.insert(0, os.path.dirname(__file__))  # гарантируем, что пакет app виден

from app.server import app
import uvicorn

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=7080)
