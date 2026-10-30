# EdgeCases: Asistente de Casos Borde y Stress Testing con IA

Herramienta diseñada para competencias de programación competitiva (**IEEEXtreme, ICPC, Codeforces, etc.**) que combina visión por computadora y LLMs (**Claude 3.5 Sonnet**) para analizar enunciados desde capturas de pantalla, identificar casos de esquina (*edge cases*), generar suites de prueba y realizar **stress testing** automatizado comparando contra un oráculo de fuerza bruta.

---

## 🚀 Características Principales

1. **Lectura Inteligente de Enunciados**:
   - Lee imágenes directamente desde el **portapapeles** (ideal al usar herramientas de recorte de pantalla) o desde una **ruta de archivo** (`.png`, `.jpg`, etc.).
   - Utiliza la visión multimodal de Claude 3.5 Sonnet para extraer cotas, restricciones y requerimientos.

2. **Generación Automática de Artefactos de Competencia**:
   - `analisis.txt`: Complejidad esperada (tiempo y memoria), estructuras de datos recomendadas y lista crítica de edge cases (mínimos, máximos, colisiones, 64-bit int, grafos degenerados, etc.).
   - `gen.py`: Generador en Python con tests aleatorios y casos extremos ajustados a cotas pequeñas/medianas.
   - `brute.cpp`: Solución ingenua/trivial conceptualmente correcta para actuar como **oráculo**.
   - `claude_sol.cpp`: Propuesta de solución óptima implementando Fast I/O y tipos de 64 bits si aplica.

3. **Stress Testing Automatizado (`stress.py`)**:
   - Compila automáticamente las soluciones en C++ con optimizaciones (`-O2 -std=c++17`).
   - Ejecuta un ciclo configurable de pruebas (por defecto 150 ciclos).
   - Genera entradas al vuelo con `gen.py`, ejecuta el oráculo y tu solución, y compara salidas ignorando diferencias de espacios y saltos de línea (`\r\n` vs `\n`).
   - Detecta y reporta inmediatamente **Wrong Answer (WA)** o **Time Limit Exceeded (TLE)** mostrando el caso exacto que falló para facilitar el debug.

---

## 📋 Requisitos Previos

- **Python**: Versión 3.8 o superior.
- **Compilador C++**: `g++` con soporte para C++17 disponible en tu variable de entorno `PATH` (MinGW en Windows, GCC en Linux, Clang/GCC en macOS).
- **API Key de Anthropic**: Para interactuar con Claude 3.5 Sonnet.

---

## ⚙️ Instalación

1. **Clonar el repositorio**:
   ```bash
   git clone https://github.com/Santy4lv/EdgeCases.git
   cd EdgeCases
   ```

2. **(Opcional pero recomendado) Crear un entorno virtual**:
   - En Windows (PowerShell):
     ```powershell
     python -m venv venv
     .\venv\Scripts\Activate.ps1
     ```
   - En Linux / macOS:
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```

3. **Instalar dependencias**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configurar la API Key de Anthropic**:
   - En **Windows (PowerShell)**:
     ```powershell
     $env:ANTHROPIC_API_KEY="tu_api_key_aqui"
     ```
   - En **Windows (CMD)**:
     ```cmd
     set ANTHROPIC_API_KEY=tu_api_key_aqui
     ```
   - En **Linux / macOS (Bash / Zsh)**:
     ```bash
     export ANTHROPIC_API_KEY='tu_api_key_aqui'
     ```
   *(También puedes copiar `.env.example` a `.env` si usas herramientas como direnv o python-dotenv).*

---

## 📖 Flujo de Trabajo y Modo de Uso

### Paso 1: Analizar el Problema y Generar Pruebas
Haz una captura del enunciado del problema (incluyendo ejemplos y límites).

- **Opción A (Portapapeles)**: Toma una captura (`Win + Shift + S` en Windows o `Cmd + Shift + 4` en macOS) para copiarla al portapapeles y ejecuta:
  ```bash
  python assistant.py
  ```

- **Opción B (Archivo)**: Si guardaste la imagen:
  ```bash
  python assistant.py ruta/a/problema.png
  ```

**Archivos generados**:
- `analisis.txt` ➔ Lectura recomendada antes de codificar.
- `gen.py` ➔ Generador de casos.
- `brute.cpp` ➔ Código base del oráculo.
- `claude_sol.cpp` ➔ Propuesta óptima de Claude.
- `raw_response.txt` ➔ Registro completo de la respuesta de la IA.

---

### Paso 2: Implementar tu Solución
Escribe tu solución en un archivo `my_sol.cpp` (o el nombre que prefieras).

```cpp
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(NULL);
    // Tu implementación aquí...
    return 0;
}
```

---

### Paso 3: Ejecutar Stress Testing
Compara tu solución contra la fuerza bruta usando el generador:

```bash
# Si tu solución se llama my_sol.cpp:
python stress.py

# O si tu solución tiene otro nombre (por ejemplo claude_sol.cpp o solucion.cpp):
python stress.py claude_sol.cpp
```

### Paso 4: Diagnóstico de Errores
Si durante las pruebas se detecta una diferencia, `stress.py` se detendrá y te mostrará el informe completo:

```text
==========================================
❌ DISCREPANCIA ENCONTRADA EN EL TEST 42
==========================================
--- ENTRADA GENERADA ---
4 5
1 2 3 4
--- SALIDA ESPERADA (FUERZA BRUTA) ---
10
--- TU SALIDA ---
9
==========================================
```

Con la entrada generada, puedes reproducir el error en tu entorno y corregir el bug rápidamente.

---

## 🛠️ Estructura del Proyecto

```text
EdgeCases/
│
├── assistant.py       # Script principal de visión y extracción de datos vía IA
├── stress.py          # Runner de pruebas de estrés automáticas con g++
├── requirements.txt   # Dependencias de Python (anthropic, Pillow)
├── .env.example       # Plantilla para variables de entorno
├── .gitignore         # Archivos y binarios ignorados
└── README.md          # Documentación del proyecto
```

---

## 💡 Consejos para Competencias

- **Ajustar cotas en `gen.py`**: El generador debe producir entradas lo suficientemente pequeñas para que `brute.cpp` responda en menos de 3 segundos, pero lo bastante variadas para capturar edge cases (valores cero, negativos, números grandes, grafos no conexos, etc.).
- **TLE en el oráculo**: Si ves `[!] TLE en la solución fuerza bruta`, reduce los valores de $N$ o el rango de datos generado en `gen.py`.
- **Fast I/O**: Asegúrate de incluir `cin.tie(NULL); ios_base::sync_with_stdio(false);` en tus soluciones para evitar falsos positivos por TLE en problemas con lecturas masivas.
