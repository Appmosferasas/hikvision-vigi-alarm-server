import os
import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile
import uvicorn

app = FastAPI(title="Hikvision Alarm Server")

SAVE_DIR = "alarmas_hikvision"
os.makedirs(SAVE_DIR, exist_ok=True)


def parsear_xml_evento(xml_str: str) -> dict:
    try:
        xml_limpio = re.sub(r'\sxmlns="[^"]+"', '', xml_str)
        root = ET.fromstring(xml_limpio)

        def get(tag):
            el = root.find(tag)
            return el.text.strip() if el is not None and el.text else None

        return {
            "ip":               get("ipAddress"),
            "mac":              get("macAddress"),
            "channel":          get("channelID"),
            "channelName":      get("channelName"),
            "dateTime":         get("dateTime"),
            "eventType":        get("eventType"),
            "eventState":       get("eventState"),
            "eventDescription": get("eventDescription"),
            "activePostCount":  get("activePostCount"),
        }
    except Exception as e:
        print(f"  ⚠️  Error parseando XML: {e}")
        return {"raw_xml": xml_str}


def guardar_imagen(data: bytes, ruta: str) -> bool:
    try:
        inicio = data.find(b'\xff\xd8')
        if inicio == -1:
            print(f"  ⚠️  No se encontró header JPEG (FF D8)")
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


async def leer_campo(valor) -> bytes:
    """Lee un campo del form sea string, bytes o UploadFile."""
    if isinstance(valor, UploadFile):
        return await valor.read()
    elif isinstance(valor, bytes):
        return valor
    elif isinstance(valor, str):
        return valor.encode("utf-8")
    return b""


# Nombres de campos conocidos que contienen XML de eventos Hikvision
CAMPOS_XML = {
    "fielddetection",       # Detección de intrusiones
    "linedetection",        # Cruce de línea
    "MoveDetection",        # Motion detection (sin .xml)
    "shelteralarm",         # Tamper
    "diskfull",             # Disco lleno
    "VMD",
}

# Nombres de campos conocidos que contienen imágenes
CAMPOS_IMAGEN = {
    "intrusionImage",       # Imagen de intrusión
    "Picture_Name",         # Formato antiguo
    "picture_name",
    "MoveDetectionImage",   # Motion detection imagen
    "lineImage",            # Cruce de línea imagen
    "image",
    "picture",
    "snapshot",
    "Snapshot",
    "img",
}


def es_xml(nombre: str, content_type: str = "") -> bool:
    return (
        nombre in CAMPOS_XML or
        nombre.endswith(".xml") or
        "xml" in content_type.lower()
    )


def es_imagen(nombre: str, content_type: str = "") -> bool:
    return (
        nombre in CAMPOS_IMAGEN or
        nombre.endswith(".jpg") or
        nombre.endswith(".jpeg") or
        nombre.lower().endswith("image") or
        "image" in content_type.lower() or
        "pjpeg" in content_type.lower()
    )


@app.post("/alarm")
@app.post("/alarm/hikvision")
async def recibir_alarma(request: Request):
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        evento_dir = os.path.join(SAVE_DIR, timestamp)
        os.makedirs(evento_dir, exist_ok=True)

        content_type = request.headers.get("content-type", "")
        imagen_guardada = False
        data = {}
        xml_raw = ""
        img_count = 0

        # --- MODO 1: multipart/form-data ---
        if "multipart/form-data" in content_type:
            form = await request.form()
            campos = list(form.keys())
            print(f"  📋 Campos recibidos: {campos}")

            for campo, valor in form.multi_items():
                campo_ct = valor.content_type if isinstance(valor, UploadFile) else ""
                raw_bytes = await leer_campo(valor)

                if es_xml(campo, campo_ct) and not xml_raw:
                    xml_raw = raw_bytes.decode("utf-8", errors="replace")
                    print(f"  📄 XML en campo '{campo}' ({len(xml_raw)} chars)")

                elif es_imagen(campo, campo_ct):
                    # Soporta múltiples imágenes por evento
                    nombre_img = f"imagen_{img_count}.jpg" if img_count > 0 else "imagen.jpg"
                    img_path = os.path.join(evento_dir, nombre_img)
                    if guardar_imagen(raw_bytes, img_path):
                        imagen_guardada = True
                        img_count += 1

            if xml_raw:
                data = parsear_xml_evento(xml_raw)

        # --- MODO 2: text/xml directo ---
        elif "text/xml" in content_type or "application/xml" in content_type:
            body = await request.body()
            xml_raw = body.decode("utf-8", errors="replace")
            data = parsear_xml_evento(xml_raw)

        # --- FALLBACK ---
        else:
            body = await request.body()
            xml_raw = body.decode("utf-8", errors="replace")
            if "<EventNotificationAlert" in xml_raw:
                data = parsear_xml_evento(xml_raw)
            else:
                data = {"raw_body": xml_raw[:500]}

        # Guardar XML original
        if xml_raw:
            with open(os.path.join(evento_dir, "evento.xml"), "w") as f:
                f.write(xml_raw)

        # Guardar JSON parseado
        with open(os.path.join(evento_dir, "evento.json"), "w") as f:
            json.dump(data, f, indent=2, default=str)

        event_type  = data.get("eventType", "?")
        event_state = data.get("eventState", "?")
        device_ip   = data.get("ip", "?")
        channel     = data.get("channelName") or data.get("channel", "?")
        print(f"[{timestamp}] ✅ IP: {device_ip} | Canal: {channel} | Evento: {event_type} ({event_state}) | Imágenes: {img_count}")

        return JSONResponse(content={
            "status":          "ok",
            "timestamp":       timestamp,
            "ip":              device_ip,
            "channel":         channel,
            "eventType":       event_type,
            "eventState":      event_state,
            "imagen_guardada": imagen_guardada,
            "imagenes":        img_count,
            "directorio":      evento_dir
        })

    except Exception as e:
        print(f"[ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/alarmas")
async def listar_alarmas():
    alarmas = []
    if os.path.exists(SAVE_DIR):
        for carpeta in sorted(os.listdir(SAVE_DIR), reverse=True):
            ruta      = os.path.join(SAVE_DIR, carpeta)
            json_path = os.path.join(ruta, "evento.json")
            img_path  = os.path.join(ruta, "imagen.jpg")
            if os.path.isdir(ruta):
                alarma = {
                    "timestamp":    carpeta,
                    "tiene_imagen": os.path.exists(img_path),
                    "datos":        None
                }
                if os.path.exists(json_path):
                    with open(json_path) as f:
                        alarma["datos"] = json.load(f)
                alarmas.append(alarma)
    return {"total": len(alarmas), "alarmas": alarmas}


@app.get("/")
async def health():
    return {"status": "online", "mensaje": "Hikvision Alarm Server corriendo"}


if __name__ == "__main__":
    uvicorn.run("main_hikevision:app", host="0.0.0.0", port=80, reload=True)