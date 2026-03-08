import os
import json
import re
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="VIGI Alarm Server")

SAVE_DIR = "alarmas"
os.makedirs(SAVE_DIR, exist_ok=True)


def es_campo_timestamp(nombre: str) -> bool:
    """Detecta si el nombre del campo es un timestamp numérico como '20260307162912'."""
    return bool(re.match(r'^\d{12,}$', nombre))


def guardar_imagen(data: bytes, ruta: str) -> bool:
    """
    Intenta guardar los bytes como imagen JPEG.
    Verifica que empiece con el magic number FF D8 (JPEG).
    """
    try:
        # Limpiar bytes nulos o basura al inicio
        inicio = data.find(b'\xff\xd8')
        if inicio == -1:
            print(f"  ⚠️  No se encontró header JPEG (FF D8) en los datos")
            return False
        jpeg_bytes = data[inicio:]
        with open(ruta, "wb") as f:
            f.write(jpeg_bytes)
        size_kb = len(jpeg_bytes) / 1024
        print(f"  📸 Imagen guardada: {ruta} ({size_kb:.1f} KB)")
        return True
    except Exception as e:
        print(f"  ❌ Error guardando imagen: {e}")
        return False


@app.post("/alarm")
async def recibir_alarma(request: Request):
    """
    Recibe el POST de la cámara VIGI.

    Estructura real observada (multipart/form-data):
    - Campo "event": JSON string con ip, mac, device_name, event_list
    - Campo "<timestamp>": bytes de la imagen JPEG (ej: "20260307162912")
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        evento_dir = os.path.join(SAVE_DIR, timestamp)
        os.makedirs(evento_dir, exist_ok=True)

        content_type = request.headers.get("content-type", "")
        imagen_guardada = False
        data = {}

        # --- multipart/form-data (con o sin imagen) ---
        if "multipart/form-data" in content_type:
            form = await request.form()

            # 1. Parsear el campo "event" como JSON
            event_raw = form.get("event")
            if event_raw:
                try:
                    data = json.loads(event_raw)
                except Exception:
                    data = {"raw": str(event_raw)}

            # 2. Buscar imagen en campo cuyo nombre es un timestamp numérico
            for campo, valor in form.multi_items():
                if es_campo_timestamp(campo):
                    print(f"  🔍 Campo imagen encontrado: '{campo}'")
                    try:
                        # Puede venir como UploadFile o como string
                        if hasattr(valor, "read"):
                            img_bytes = await valor.read()
                        else:
                            img_bytes = valor.encode("latin-1") if isinstance(valor, str) else bytes(valor)

                        img_path = os.path.join(evento_dir, "imagen.jpg")
                        imagen_guardada = guardar_imagen(img_bytes, img_path)
                    except Exception as e:
                        print(f"  ❌ Error procesando campo imagen: {e}")
                    break

        # --- Solo JSON (sin imagen) ---
        elif "application/json" in content_type:
            data = await request.json()

        # --- Fallback ---
        else:
            try:
                data = await request.json()
            except Exception:
                body = await request.body()
                data = {"raw_body": body.decode("utf-8", errors="replace")}

        # Guardar JSON del evento
        json_path = os.path.join(evento_dir, "evento.json")
        with open(json_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

        # Extraer info del evento
        device_name = data.get("device_name", "desconocido")
        device_ip = data.get("ip", "?")
        event_list = data.get("event_list", [])
        eventos = [e.get("event_type", ["?"])[0] if isinstance(e.get("event_type"), list) else "?" for e in event_list] if event_list else ["desconocido"]

        print(f"[{timestamp}] ✅ Alarma | {device_name} ({device_ip}) | Eventos: {eventos} | Imagen: {imagen_guardada}")

        return JSONResponse(content={
            "status": "ok",
            "timestamp": timestamp,
            "device": device_name,
            "eventos": eventos,
            "imagen_guardada": imagen_guardada,
            "directorio": evento_dir
        })

    except Exception as e:
        print(f"[ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/alarmas")
async def listar_alarmas():
    """Lista todas las alarmas recibidas."""
    alarmas = []
    if os.path.exists(SAVE_DIR):
        for carpeta in sorted(os.listdir(SAVE_DIR), reverse=True):
            ruta = os.path.join(SAVE_DIR, carpeta)
            json_path = os.path.join(ruta, "evento.json")
            img_path = os.path.join(ruta, "imagen.jpg")
            if os.path.isdir(ruta):
                alarma = {
                    "timestamp": carpeta,
                    "tiene_imagen": os.path.exists(img_path),
                    "datos": None
                }
                if os.path.exists(json_path):
                    with open(json_path) as f:
                        alarma["datos"] = json.load(f)
                alarmas.append(alarma)
    return {"total": len(alarmas), "alarmas": alarmas}


@app.get("/")
async def health():
    return {"status": "online", "mensaje": "VIGI Alarm Server corriendo"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=80, reload=True)