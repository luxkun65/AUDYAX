import os
import re
import json
from yt_dlp import YoutubeDL
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB
from PIL import Image

# Configuración del directorio de descarga
DOWNLOAD_DIR = "B:/Music/Prueba_de_Python"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# Nombre del archivo donde se registrarán los videos omitidos
FALTANTES_FILE = os.path.join(DOWNLOAD_DIR, "faltantes.txt")

# ==============================================================================
# 🛠️ Función para Registrar Videos Omitidos (Download Hook)
# ==============================================================================
def registro_faltantes(d):
    """
    Función hook que se ejecuta tras cada acción de descarga de yt-dlp.
    Registra el título y la URL del video si es omitido.
    """
    # El estado 'finished' indica que la descarga fue exitosa.
    # El estado 'error' o 'skipped' (implícito en la falta de 'finished' y la presencia de 'url')
    # significa que no se descargó, pero la metadata está disponible.
    if d['status'] == 'finished':
        return # Si se descargó correctamente, no hacemos nada.
    
    # Si la descarga no ha terminado, miramos el error de omisión:
    if d.get('info_dict', {}).get('is_downloaded') is False and d.get('info_dict', {}).get('url'):
        
        # Obtenemos la información relevante del video omitido
        title = d.get('info_dict', {}).get('title', 'Título Desconocido')
        url = d.get('info_dict', {}).get('webpage_url', 'URL Desconocida')
        
        # yt-dlp no siempre proporciona la razón de omisión clara en el hook, 
        # pero podemos registrar que fue omitido.
        reason = "Omitido/No disponible (revisar log de consola para detalles)"
        
        line = f"Video: {title}\nURL: {url}\nRazón: {reason}\n---\n"
        
        # Escribimos en el archivo faltantes.txt
        with open(FALTANTES_FILE, 'a', encoding='utf-8') as f:
            f.write(line)
            
        print(f"\n⚠️ REGISTRADO COMO FALTANTE: {title}")


# ==============================================================================
# 🛠️ Función de Procesamiento por Archivo (La misma que funciona)
# ==============================================================================
def process_mp3_metadata_and_cleanup(mp3_path):
    """Procesa un archivo MP3: lee metadata, añade tags, inserta portada y limpia temporales."""
    
    base_name = os.path.splitext(os.path.basename(mp3_path))[0]
    json_path = os.path.join(DOWNLOAD_DIR, f"{base_name}.info.json")
    thumbnail_webp = os.path.join(DOWNLOAD_DIR, f"{base_name}.webp")
    thumbnail_jpg = os.path.join(DOWNLOAD_DIR, f"{base_name}.jpg")
    
    # print(f"\nProcesando archivo: {mp3_path}") # Descomentar para ver el log detallado

    # 1. Obtener la Metadata del archivo JSON
    if not os.path.exists(json_path):
        # print(f"❌ Error: Archivo de metadata JSON no encontrado para {base_name}. Saltando.")
        return

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            info_dict = json.load(f)
            
        title = info_dict.get("title", base_name)
        artist = info_dict.get("uploader", "Unknown Artist")
        album = info_dict.get("album", info_dict.get("playlist", title))
        
    except json.JSONDecodeError:
        # print(f"❌ Error al leer o parsear el JSON de metadata para {base_name}. Saltando.")
        return

    # 2. Procesamiento de la miniatura (webp a jpeg)
    if os.path.exists(thumbnail_webp):
        try:
            with Image.open(thumbnail_webp) as img:
                img = img.convert("RGB")
                img.save(thumbnail_jpg, "JPEG")
        except Exception:
            thumbnail_jpg = None
    else:
        thumbnail_jpg = None

    # 3. Agregar metadata al archivo MP3
    try:
        audio = MP3(mp3_path, ID3=ID3)
        if not audio.tags:
            audio.add_tags()

        audio.tags.add(TIT2(encoding=3, text=title)) 
        audio.tags.add(TPE1(encoding=3, text=artist))
        audio.tags.add(TALB(encoding=3, text=album))

        # Agregar carátula
        if thumbnail_jpg and os.path.exists(thumbnail_jpg):
            if 'APIC:' in audio.tags:
                del audio.tags['APIC:'] 
            
            with open(thumbnail_jpg, "rb") as img:
                image_data = img.read()
                audio.tags.add(
                    APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,  
                        desc="Cover",
                        data=image_data,
                    )
                )

        audio.save(v2_version=3)
        # print(f"✅ Metadata y portada finalizadas en {mp3_path}.")

    except Exception:
        # print(f"⚠️ Advertencia: Ocurrió un error al etiquetar el MP3.")
        pass

    # 4. Eliminar archivos residuales
    residual_files = [json_path, thumbnail_webp, thumbnail_jpg]
    for residual in residual_files:
        if os.path.exists(residual):
            try:
                os.remove(residual)
            except Exception:
                pass


# ==============================================================================
# 🚀 Bloque Principal de Ejecución
# ==============================================================================

# Eliminar el archivo de faltantes anterior si existe
if os.path.exists(FALTANTES_FILE):
    os.remove(FALTANTES_FILE)

# Obtener URL de entrada
url = input("Ingresa la URL del video o lista de reproducción de YouTube: ")

# Configuración para yt-dlp
options = {
    # ... (Opciones de formato y postprocesamiento) ...
    "format": "bestaudio[ext=m4a]/bestaudio", 
    "postprocessors": [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }
    ],
    "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
    "writethumbnail": True,
    "writeinfojson": True,  
    "noplaylist": False,    
    "prefer_ffmpeg": True,
    
    # Opciones de robustez
    "retries": 10,           
    "fragment_retries": 10, 
    "concurrent_fragments": 5,
    "geo_bypass": True,      
    "external_downloader": "ffmpeg",
    "external_downloader_args": {
        "ffmpeg": ["-nostats", "-loglevel", "error"]
    },
    
    # ⭐ CLAVE: Añadir el hook para registrar faltantes
    "progress_hooks": [registro_faltantes], 
}

try:
    print("--- INICIANDO DESCARGA. Se registrarán faltantes en 'faltantes.txt' ---")
    with YoutubeDL(options) as ydl:
        ydl.download([url]) 
    print("--- DESCARGA FINALIZADA. INICIANDO PROCESAMIENTO DE METADATA ---")

    # 2. Procesar cada archivo MP3 generado
    archivos_procesados = 0
    for filename in os.listdir(DOWNLOAD_DIR):
        if filename.endswith(".mp3"):
            mp3_path = os.path.join(DOWNLOAD_DIR, filename)
            process_mp3_metadata_and_cleanup(mp3_path)
            archivos_procesados += 1
            
    print("\n--- ¡PROCESO TERMINADO! ---")
    print(f"Archivos MP3 procesados y etiquetados: {archivos_procesados}")
    
    if os.path.exists(FALTANTES_FILE):
        print(f"👉 Se encontraron videos faltantes. Revisa el archivo: {FALTANTES_FILE}")
    else:
        print("🎉 ¡No se detectaron videos faltantes! (o yt-dlp no pudo obtener la metadata de los omitidos)")

except Exception as e:
    print(f"\n--- FALLO FATAL ---")
    print(f"Error general en la ejecución: {e}")