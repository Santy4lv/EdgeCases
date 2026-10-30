import base64
import io
import os
import re
import sys
from PIL import Image, ImageGrab
import anthropic

# Inicialización del cliente oficial de Anthropic
api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("Error: La variable de entorno ANTHROPIC_API_KEY no está definida.")
    print("Ejecuta: export ANTHROPIC_API_KEY='tu_api_key'")
    sys.exit(1)

client = anthropic.Anthropic(api_key=api_key)

SYSTEM_PROMPT = """Eres un asistente de ingeniería de software y programación competitiva (ICPC, IEEEXtreme).
Analiza el enunciado y la imagen del problema provisto.

Debes responder OBLIGATORIAMENTE utilizando las siguientes etiquetas XML para estructurar la salida:

<ANALISIS>
- Complejidad esperada (Tiempo y Memoria) en base a las cotas del problema.
- Casos esquina / Edge cases críticos (mínimos, máximos, colisiones, 64-bit int, grafos degenerados, etc.).
- Enfoque algorítmico y estructuras de datos recomendadas.
</ANALISIS>

<GENERADOR>
# Código completo en Python 3 para generar casos de prueba aleatorios y extremos (enfatizando N pequeño/mediano para stress testing).
# Debe imprimir la entrada estándar esperada directamente por stdout.
</GENERADOR>

<FUERZA_BRUTA>
// Código C++ completo de una solución ingenua / trivial (O(N^2), exponencial, etc.).
// Debe ser conceptualmente correcta para usar como oráculo de pruebas, sin importar si da TLE con N grande.
</FUERZA_BRUTA>

<SOLUCION_OPTIMA>
// Código C++ con la mejor propuesta óptima implementando Fast I/O y tipos long long si aplica.
</SOLUCION_OPTIMA>
"""

def clean_code_block(content: str) -> str:
    """Elimina delimitadores markdown tipo ```cpp o ```python si el modelo los incluye."""
    lines = content.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()

def extract_tag_content(text: str, tag: str) -> str:
    """Extrae el contenido de una etiqueta XML tolerando variaciones."""
    pattern = rf"<{tag}>(.*?)</{tag}>"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""

def get_image_base64() -> str:
    """Obtiene la imagen desde un archivo pasado como argumento o directamente del portapapeles."""
    img = None
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        print(f"Cargando imagen desde archivo: {sys.argv[1]}")
        img = Image.open(sys.argv[1])
    else:
        print("Intentando leer imagen desde el portapapeles...")
        img = ImageGrab.grabclipboard()

    if img is None or not isinstance(img, Image.Image):
        print("Error: No se encontró ninguna imagen en el portapapeles ni se especificó un archivo existente.")
        print("Uso: python assistant.py [ruta_imagen.png]")
        sys.exit(1)

    # Conversión a modo RGB para garantizar compatibilidad con JPEG
    if img.mode != "RGB":
        img = img.convert("RGB")

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")

def main():
    image_b64 = get_image_base64()
    print("Enviando problema a Claude 3.5 Sonnet...")

    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=3000,
            temperature=0.1,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Analiza este problema de programación competitiva y genera todos los bloques solicitados.",
                        },
                    ],
                }
            ],
        )
    except Exception as e:
        print(f"Error en la llamada a la API: {e}")
        sys.exit(1)

    raw_text = response.content[0].text

    # Guardar la respuesta cruda de respaldo
    with open("raw_response.txt", "w", encoding="utf-8") as f:
        f.write(raw_text)

    # Extraer secciones
    analisis = extract_tag_content(raw_text, "ANALISIS")
    generador = clean_code_block(extract_tag_content(raw_text, "GENERADOR"))
    fuerza_bruta = clean_code_block(extract_tag_content(raw_text, "FUERZA_BRUTA"))
    sol_optima = clean_code_block(extract_tag_content(raw_text, "SOLUCION_OPTIMA"))

    # Guardar en disco
    if analisis:
        with open("analisis.txt", "w", encoding="utf-8") as f:
            f.write(analisis)
        print("[OK] analisis.txt generado con éxito.")
    else:
        print("[AVISO] No se pudo parsear <ANALISIS>, revisa raw_response.txt")

    if generador:
        with open("gen.py", "w", encoding="utf-8") as f:
            f.write(generador)
        print("[OK] gen.py generado con éxito.")

    if fuerza_bruta:
        with open("brute.cpp", "w", encoding="utf-8") as f:
            f.write(fuerza_bruta)
        print("[OK] brute.cpp generado con éxito.")

    if sol_optima:
        with open("claude_sol.cpp", "w", encoding="utf-8") as f:
            f.write(sol_optima)
        print("[OK] claude_sol.cpp generado con éxito.")

if __name__ == "__main__":
    main()