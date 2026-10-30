"""
ocr_markdown.py
===============
Herramienta de escritorio profesional para convertir capturas de pantalla a Markdown.

Características destacadas:
  - Soporte MULTI-FRAGMENTO: Agrega capturas continuas por pedazos.
  - Reordenamiento visual de fragmentos: Mover arriba (⬆️), Mover abajo (⬇️) o Eliminar (🗑️).
  - Detección automática del hardware del equipo (GPU NVIDIA, VRAM, o CPU).
  - Selección adaptativa del motor según los recursos de cada usuario.
  - Cronómetro en tiempo real y registro de duración.
  - Botón de Detener/Cancelar proceso en ejecución inmediata.
  - Vista previa interactiva de cada fragmento al seleccionarlo en la lista.
  - Consolidación limpia a Markdown unificado respetando el orden establecido.

Uso:
    python ocr_markdown.py
"""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import List, Optional, Tuple

# ── Dependencias requeridas ───────────────────────────────────────────────────
try:
    import customtkinter as ctk
except ImportError:
    sys.exit("[ERROR] customtkinter no instalado. Ejecuta: pip install customtkinter")

try:
    from PIL import Image, ImageGrab, ImageTk
except ImportError:
    sys.exit("[ERROR] Pillow no instalado. Ejecuta: pip install Pillow")

try:
    from dotenv import load_dotenv
except ImportError:
    sys.exit("[ERROR] python-dotenv no instalado. Ejecuta: pip install python-dotenv")

# ── Carga de variables de entorno ─────────────────────────────────────────────
_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")

# ── Constantes y Configuración ────────────────────────────────────────────────
APP_TITLE  = "OCR → Markdown Studio (Multi-Fragmentos)"
APP_WIDTH  = 1380
APP_HEIGHT = 780
THUMB_MAX  = 460

GEMINI_SYSTEM_PROMPT = (
    "Eres un transcriptor OCR de precisión absoluta. "
    "Tu única tarea es reproducir fielmente el texto de la imagen como Markdown puro. "
    "REGLAS ESTRICTAS:\n"
    "1. Preserva la jerarquía de encabezados usando #, ##, ### etc.\n"
    "2. Respeta listas ordenadas (1. 2. 3.) y desordenadas (- o *).\n"
    "3. Convierte tablas al formato Markdown estándar (| col | col |).\n"
    "4. Envuelve bloques de código con triple backtick e indica el lenguaje "
    "   (```python, ```js, ```bash, etc.) si es detectable.\n"
    "5. Mantén énfasis: **negrita**, *cursiva*, `código inline`.\n"
    "6. NO añadas introducciones, saludos, resúmenes, comentarios ni texto "
    "   que no esté en la imagen.\n"
    "7. Entrega ÚNICAMENTE el Markdown transcrito, sin envolturas adicionales."
)


# ══════════════════════════════════════════════════════════════════════════════
# Modelo de Datos para Fragmentos
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ImageFragment:
    image: Image.Image
    title: str
    markdown_output: str = ""
    status: str = "Pendiente"


# ══════════════════════════════════════════════════════════════════════════════
# Detección Inteligente de Hardware
# ══════════════════════════════════════════════════════════════════════════════

def detect_hardware() -> Tuple[str, float]:
    """Detecta GPU NVIDIA y VRAM (en GB)."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.DEVNULL
        )
        lines = out.strip().splitlines()
        if lines:
            parts = lines[0].split(",")
            gpu_name = parts[0].strip()
            vram_gb = round(float(parts[1].strip()) / 1024.0, 1)
            return gpu_name, vram_gb
    except Exception:
        pass
    return "CPU / Gráficos Integrados", 0.0


def check_ollama_status() -> Tuple[bool, list[str]]:
    """Verifica si Ollama está activo y qué modelos tiene."""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name", "") for m in data.get("models", [])]
            return True, models
    except Exception:
        return False, []


# ══════════════════════════════════════════════════════════════════════════════
# Motores de Extracción
# ══════════════════════════════════════════════════════════════════════════════

def _ocr_gemini(image: Image.Image, model_name: str = "gemini-flash-latest") -> str:
    """Extrae texto usando la API de Gemini (Flash o Pro)."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY no encontrada en el archivo .env")

    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name=model_name, system_instruction=GEMINI_SYSTEM_PROMPT)

    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    image_bytes = buf.getvalue()

    response = model.generate_content(
        contents=[
            {"mime_type": "image/png", "data": image_bytes},
            "Transcribe fielmente toda la imagen al formato Markdown.",
        ]
    )
    return response.text.strip()


def _ocr_ollama(image: Image.Image, model_name: str = "moondream") -> str:
    """Extrae texto usando el modelo VLM local corriendo en Ollama."""
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    b64_img = base64.b64encode(buf.getvalue()).decode("utf-8")

    prompt = (
        "Transcribe precisely all the text, tables, and code in this image into pure Markdown format. "
        "Do not include any commentary or explanations."
    )

    payload = {
        "model": model_name,
        "prompt": prompt,
        "images": [b64_img],
        "stream": False,
    }

    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("response", "").strip()


def _ocr_local_classic(image: Image.Image) -> str:
    """Fallback local clásico con Tesseract o EasyOCR."""
    try:
        import pytesseract
        text = pytesseract.image_to_string(image, lang="spa+eng")
        return _text_to_markdown(text)
    except Exception:
        pass

    try:
        import easyocr
        reader = easyocr.Reader(["es", "en"], gpu=False, verbose=False)
        rgb = image.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, format="PNG")
        results = reader.readtext(buf.getvalue(), detail=0, paragraph=True)
        return _text_to_markdown("\n".join(results))
    except Exception as exc:
        raise RuntimeError(f"No se pudo ejecutar OCR clásico: {exc}")


def _text_to_markdown(raw: str) -> str:
    lines = raw.splitlines()
    md_lines: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            md_lines.append("")
            continue
        if stripped.isupper() and len(stripped) <= 60:
            md_lines.append(f"## {stripped.title()}")
        else:
            md_lines.append(stripped)
    return "\n".join(md_lines)


# ══════════════════════════════════════════════════════════════════════════════
# Interfaz Gráfica Principal (3 Columnas: Lista | Visor | Markdown)
# ══════════════════════════════════════════════════════════════════════════════

class OcrMarkdownApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title(APP_TITLE)
        self.geometry(f"{APP_WIDTH}x{APP_HEIGHT}")
        self.minsize(1050, 680)

        # Lista ordenada de fragmentos
        self.fragments: List[ImageFragment] = []
        self.selected_idx: int = -1

        self._photo_ref: Optional[ImageTk.PhotoImage] = None

        # Control de ejecución y cronómetro
        self._is_running_ocr: bool = False
        self._ocr_start_time: float = 0.0
        self._cancel_requested: bool = False

        # Detección de hardware
        self.gpu_name, self.vram_gb = detect_hardware()
        self.ollama_active, self.ollama_models = check_ollama_status()

        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_topbar()
        self._build_main_area()
        self._build_bottombar()

    def _get_engine_options(self) -> Tuple[list[str], str]:
        options = ["☁️ Gemini Flash (Recomendado)", "☁️ Gemini Pro (Alta Precisión)"]
        if self.ollama_active:
            if any("moondream" in m for m in self.ollama_models):
                options.append("⚡ Local IA: Moondream (Optimizado 4GB VRAM)")
            if any("minicpm-v" in m for m in self.ollama_models):
                options.append("🧠 Local IA: MiniCPM-V (Alta Resolución)")

        options.append("💻 Local Clásico (Tesseract / EasyOCR)")
        return options, options[0]

    # ── Barra Superior ────────────────────────────────────────────────────────

    def _build_topbar(self) -> None:
        bar = ctk.CTkFrame(self, height=58, corner_radius=0)
        bar.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        bar.grid_columnconfigure(5, weight=1)

        btn_paste = ctk.CTkButton(
            bar,
            text="📋  + Pegar Fragmento",
            width=180,
            command=self._paste_fragment,
        )
        btn_paste.grid(row=0, column=0, padx=(12, 6), pady=10)

        btn_load = ctk.CTkButton(
            bar,
            text="📂  + Cargar Archivo(s)",
            width=170,
            fg_color="transparent",
            border_width=1,
            command=self._load_files,
        )
        btn_load.grid(row=0, column=1, padx=6, pady=10)

        btn_clear = ctk.CTkButton(
            bar,
            text="🧹  Limpiar Todo",
            width=120,
            fg_color="#313244",
            hover_color="#45475a",
            command=self._clear_all,
        )
        btn_clear.grid(row=0, column=2, padx=6, pady=10)

        hw_text = f"🖥️ {self.gpu_name}"
        if self.vram_gb > 0:
            hw_text += f" ({self.vram_gb} GB VRAM)"
        lbl_hw = ctk.CTkLabel(
            bar,
            text=hw_text,
            text_color="#89b4fa",
            font=ctk.CTkFont(size=11, weight="bold")
        )
        lbl_hw.grid(row=0, column=3, padx=(14, 10), pady=10)

        lbl_engine = ctk.CTkLabel(bar, text="Motor:")
        lbl_engine.grid(row=0, column=4, padx=(10, 4), pady=10)

        options, default_engine = self._get_engine_options()
        self._engine_var = tk.StringVar(value=default_engine)
        self._engine_menu = ctk.CTkOptionMenu(
            bar,
            values=options,
            variable=self._engine_var,
            width=290,
        )
        self._engine_menu.grid(row=0, column=5, padx=(0, 12), pady=10, sticky="e")

    # ── Área Principal (Columna Fragmentos | Visor | Editor Markdown) ─────────

    def _build_main_area(self) -> None:
        paned = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        paned.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 0))
        paned.grid_columnconfigure(0, weight=0, minsize=260)  # Panel de Fragmentos
        paned.grid_columnconfigure(1, weight=1)               # Visor de Imagen
        paned.grid_columnconfigure(2, weight=1)               # Editor Markdown
        paned.grid_rowconfigure(0, weight=1)

        # ── Columna 1: Administrador de Fragmentos y Orden ───────────────────
        col_list = ctk.CTkFrame(paned, corner_radius=8, width=260)
        col_list.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=0)
        col_list.grid_rowconfigure(1, weight=1)
        col_list.grid_columnconfigure(0, weight=1)

        self._lbl_frag_count = ctk.CTkLabel(
            col_list,
            text="📑 Fragmentos (0)",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self._lbl_frag_count.grid(row=0, column=0, pady=(8, 4))

        # Lista seleccionable con scroll
        self._list_frame = ctk.CTkScrollableFrame(col_list, corner_radius=6)
        self._list_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))

        # Barra de botones para reordenar (Arriba / Abajo / Eliminar)
        order_bar = ctk.CTkFrame(col_list, fg_color="transparent")
        order_bar.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 8))
        order_bar.grid_columnconfigure((0, 1, 2), weight=1)

        self._btn_up = ctk.CTkButton(
            order_bar,
            text="⬆️ Subir",
            height=30,
            command=self._move_fragment_up,
        )
        self._btn_up.grid(row=0, column=0, padx=2)

        self._btn_down = ctk.CTkButton(
            order_bar,
            text="⬇️ Bajar",
            height=30,
            command=self._move_fragment_down,
        )
        self._btn_down.grid(row=0, column=1, padx=2)

        self._btn_del = ctk.CTkButton(
            order_bar,
            text="🗑️ Borrar",
            height=30,
            fg_color="#f38ba8",
            hover_color="#eb7093",
            text_color="#11111b",
            command=self._delete_fragment,
        )
        self._btn_del.grid(row=0, column=2, padx=2)

        # ── Columna 2: Visor de Imagen Seleccionada ─────────────────────────
        col_view = ctk.CTkFrame(paned, corner_radius=8)
        col_view.grid(row=0, column=1, sticky="nsew", padx=4, pady=0)
        col_view.grid_rowconfigure(1, weight=1)
        col_view.grid_columnconfigure(0, weight=1)

        self._lbl_view_title = ctk.CTkLabel(
            col_view,
            text="Vista Previa del Fragmento",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self._lbl_view_title.grid(row=0, column=0, pady=(8, 4))

        self._canvas = tk.Canvas(
            col_view,
            bg="#181825",
            highlightthickness=0,
            cursor="crosshair",
        )
        self._canvas.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self._canvas.bind("<Configure>", self._on_canvas_resize)

        self._placeholder_text = self._canvas.create_text(
            15, 15,
            text="Sin fragmentos añadidos.\nPega varias capturas con Win + Shift + S\ny organízalas en el orden que desees.",
            fill="#585b70",
            anchor="nw",
            font=("Consolas", 11),
        )

        # Botón de Procesar / Detener
        action_bar = ctk.CTkFrame(col_view, fg_color="transparent")
        action_bar.grid(row=2, column=0, padx=8, pady=(0, 10), sticky="ew")
        action_bar.grid_columnconfigure(0, weight=1)

        self._btn_process = ctk.CTkButton(
            action_bar,
            text="⚡  Procesar y Unificar Fragmentos",
            command=self._run_ocr_threaded,
            height=38,
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self._btn_process.grid(row=0, column=0, sticky="ew")

        self._btn_cancel = ctk.CTkButton(
            action_bar,
            text="🛑  Detener",
            command=self._cancel_ocr,
            height=38,
            width=110,
            fg_color="#f38ba8",
            hover_color="#eb7093",
            text_color="#11111b",
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self._btn_cancel.grid_remove()

        # ── Columna 3: Editor de Markdown Unificado ─────────────────────────
        col_md = ctk.CTkFrame(paned, corner_radius=8)
        col_md.grid(row=0, column=2, sticky="nsew", padx=(4, 0), pady=0)
        col_md.grid_rowconfigure(1, weight=1)
        col_md.grid_columnconfigure(0, weight=1)

        lbl_md_title = ctk.CTkLabel(
            col_md,
            text="Markdown Unificado Final (Editable)",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        lbl_md_title.grid(row=0, column=0, pady=(8, 4))

        self._md_text = ctk.CTkTextbox(
            col_md,
            font=ctk.CTkFont(family="Consolas", size=12),
            wrap="word",
            activate_scrollbars=True,
        )
        self._md_text.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

    # ── Barra Inferior ────────────────────────────────────────────────────────

    def _build_bottombar(self) -> None:
        bar = ctk.CTkFrame(self, height=48, corner_radius=0)
        bar.grid(row=2, column=0, sticky="ew", padx=0, pady=0)
        bar.grid_columnconfigure(0, weight=1)

        status_frame = ctk.CTkFrame(bar, fg_color="transparent")
        status_frame.grid(row=0, column=0, padx=14, pady=8, sticky="w")

        self._status_var = tk.StringVar(value="✅  Listo para agregar fragmentos")
        lbl_status = ctk.CTkLabel(
            status_frame,
            textvariable=self._status_var,
            font=ctk.CTkFont(size=12),
            anchor="w",
        )
        lbl_status.pack(side="left")

        self._timer_var = tk.StringVar(value="")
        lbl_timer = ctk.CTkLabel(
            status_frame,
            textvariable=self._timer_var,
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color="#f9e2af",
        )
        lbl_timer.pack(side="left", padx=(10, 0))

        btn_copy = ctk.CTkButton(
            bar,
            text="📋  Copiar al Portapapeles",
            width=180,
            fg_color="transparent",
            border_width=1,
            command=self._copy_markdown,
        )
        btn_copy.grid(row=0, column=1, padx=6, pady=8)

        btn_save = ctk.CTkButton(
            bar,
            text="💾  Guardar como .md",
            width=180,
            command=self._save_markdown,
        )
        btn_save.grid(row=0, column=2, padx=(0, 12), pady=8)

    # ── Gestión y Reordenamiento de Fragmentos ────────────────────────────────

    def _add_fragment(self, image: Image.Image, name_hint: str = "") -> None:
        idx = len(self.fragments) + 1
        title = name_hint if name_hint else f"Fragmento #{idx} ({image.width}x{image.height})"
        fragment = ImageFragment(image=image, title=title)
        self.fragments.append(fragment)
        self._select_fragment(len(self.fragments) - 1)
        self._refresh_fragments_list()
        self._set_status(f"➕  Fragmento #{idx} añadido ({len(self.fragments)} en total)")

    def _refresh_fragments_list(self) -> None:
        """Reconstruye los botones de la lista lateral con el orden actual."""
        for widget in self._list_frame.winfo_children():
            widget.destroy()

        self._lbl_frag_count.configure(text=f"📑 Fragmentos ({len(self.fragments)})")

        for i, frag in enumerate(self.fragments):
            is_selected = (i == self.selected_idx)
            btn_color = "#3b4261" if is_selected else "transparent"
            border_w = 2 if is_selected else 1

            status_badge = "⏳" if frag.status == "Procesando" else ("✅" if frag.status == "Listo" else "⚪")
            item_text = f"{i+1}. {status_badge} {frag.title}"

            btn = ctk.CTkButton(
                self._list_frame,
                text=item_text,
                anchor="w",
                fg_color=btn_color,
                border_width=border_w,
                border_color="#7aa2f7" if is_selected else "#414868",
                height=32,
                font=ctk.CTkFont(size=11),
                command=lambda idx=i: self._select_fragment(idx)
            )
            btn.pack(fill="x", pady=2, padx=2)

    def _select_fragment(self, idx: int) -> None:
        if 0 <= idx < len(self.fragments):
            self.selected_idx = idx
            frag = self.fragments[idx]
            self._lbl_view_title.configure(text=f"Vista Previa: #{idx+1} {frag.title}")
            self._render_thumbnail(frag.image)
            self._refresh_fragments_list()

    def _move_fragment_up(self) -> None:
        i = self.selected_idx
        if i > 0 and len(self.fragments) > 1:
            self.fragments[i - 1], self.fragments[i] = self.fragments[i], self.fragments[i - 1]
            self._select_fragment(i - 1)
            self._set_status(f"⬆️ Fragmento movido a la posición #{i}")

    def _move_fragment_down(self) -> None:
        i = self.selected_idx
        if 0 <= i < len(self.fragments) - 1:
            self.fragments[i + 1], self.fragments[i] = self.fragments[i], self.fragments[i + 1]
            self._select_fragment(i + 1)
            self._set_status(f"⬇️ Fragmento movido a la posición #{i + 2}")

    def _delete_fragment(self) -> None:
        if 0 <= self.selected_idx < len(self.fragments):
            deleted = self.fragments.pop(self.selected_idx)
            if self.fragments:
                self.selected_idx = min(self.selected_idx, len(self.fragments) - 1)
                self._select_fragment(self.selected_idx)
            else:
                self.selected_idx = -1
                self._canvas.delete("all")
                self._placeholder_text = self._canvas.create_text(
                    15, 15,
                    text="Sin fragmentos cargados.\nPega o carga nuevas imágenes.",
                    fill="#585b70",
                    anchor="nw",
                    font=("Consolas", 11),
                )
                self._lbl_view_title.configure(text="Vista Previa del Fragmento")
                self._refresh_fragments_list()
            self._set_status(f"🗑️ Eliminado fragmento: {deleted.title}")

    def _clear_all(self) -> None:
        if not self.fragments:
            return
        if messagebox.askyesno("Limpiar todo", "¿Deseas borrar todos los fragmentos cargados?"):
            self.fragments.clear()
            self.selected_idx = -1
            self._canvas.delete("all")
            self._placeholder_text = self._canvas.create_text(
                15, 15,
                text="Sin fragmentos cargados.\nPega o carga nuevas imágenes.",
                fill="#585b70",
                anchor="nw",
                font=("Consolas", 11),
            )
            self._refresh_fragments_list()
            self._set_status("🧹 Se han limpiado todos los fragmentos")

    # ── Manejo de Entradas (Pegar / Cargar) ────────────────────────────────────

    def _paste_fragment(self) -> None:
        try:
            img = ImageGrab.grabclipboard()
            if img is None:
                messagebox.showwarning("Portapapeles vacío", "No hay imagen en el portapapeles.\nToma una con Win + Shift + S y presiona el botón.")
                return
            if not isinstance(img, Image.Image):
                messagebox.showwarning("Contenido inválido", "El contenido del portapapeles no es una imagen válida.")
                return
            self._add_fragment(img)
        except Exception as exc:
            messagebox.showerror("Error al pegar", str(exc))

    def _load_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Seleccionar imágenes de fragmentos",
            filetypes=[
                ("Imágenes", "*.png *.jpg *.jpeg *.bmp *.tiff *.tif *.webp *.gif"),
                ("Todos los archivos", "*.*"),
            ],
        )
        if not paths:
            return
        for p in paths:
            try:
                img = Image.open(p)
                self._add_fragment(img, name_hint=Path(p).name)
            except Exception as exc:
                messagebox.showerror("Error al cargar", f"No se pudo cargar {Path(p).name}: {exc}")

    def _on_canvas_resize(self, _event: tk.Event) -> None:
        if 0 <= self.selected_idx < len(self.fragments):
            self._render_thumbnail(self.fragments[self.selected_idx].image)

    def _render_thumbnail(self, image: Image.Image) -> None:
        cw = self._canvas.winfo_width()  or THUMB_MAX
        ch = self._canvas.winfo_height() or THUMB_MAX
        img = image.copy()
        img.thumbnail((cw, ch), Image.LANCZOS)
        self._photo_ref = ImageTk.PhotoImage(img)
        self._canvas.delete("all")
        cx, cy = cw // 2, ch // 2
        self._canvas.create_image(cx, cy, anchor="center", image=self._photo_ref)

    # ── Cronómetro y Cancelación ──────────────────────────────────────────────

    def _start_timer(self) -> None:
        self._is_running_ocr = True
        self._cancel_requested = False
        self._ocr_start_time = time.time()
        self._btn_process.configure(state="disabled", text="⏳  Procesando fragmentos...")
        self._btn_cancel.grid(row=0, column=1, padx=(8, 0))
        self._update_timer_tick()

    def _update_timer_tick(self) -> None:
        if not self._is_running_ocr:
            return
        elapsed = time.time() - self._ocr_start_time
        mins = int(elapsed // 60)
        secs = elapsed % 60
        if mins > 0:
            self._timer_var.set(f"⏱️ {mins}m {secs:04.1f}s")
        else:
            self._timer_var.set(f"⏱️ {secs:04.1f}s")
        self.after(100, self._update_timer_tick)

    def _stop_timer(self) -> float:
        self._is_running_ocr = False
        self._btn_cancel.grid_remove()
        self._btn_process.configure(state="normal", text="⚡  Procesar y Unificar Fragmentos")
        return time.time() - self._ocr_start_time

    def _cancel_ocr(self) -> None:
        if not self._is_running_ocr:
            return
        self._cancel_requested = True
        elapsed = self._stop_timer()
        self._set_status("🛑  Proceso detenido por el usuario")
        self._timer_var.set(f"⏱️ Detenido a los {elapsed:.1f}s")

    # ── Ejecución de OCR Secuencial y Consolidación ───────────────────────────

    def _run_ocr_threaded(self) -> None:
        if not self.fragments:
            messagebox.showwarning("Sin fragmentos", "Agrega al menos una captura o fragmento primero.")
            return

        self._start_timer()
        self._set_status(f"⏳  Iniciando procesamiento de {len(self.fragments)} fragmento(s)...")
        thread = threading.Thread(target=self._run_ocr_pipeline, daemon=True)
        thread.start()

    def _run_ocr_pipeline(self) -> None:
        engine = self._engine_var.get()
        total = len(self.fragments)
        combined_markdown: list[str] = []

        try:
            for idx, frag in enumerate(self.fragments):
                if self._cancel_requested:
                    return

                frag.status = "Procesando"
                self.after(0, self._refresh_fragments_list)
                self.after(0, self._set_status, f"⏳  Procesando fragmento {idx + 1} de {total}...")

                # Llamada al motor correspondiente para cada fragmento
                if "Gemini Flash" in engine:
                    md_part = _ocr_gemini(frag.image, model_name="gemini-flash-latest")
                elif "Gemini Pro" in engine:
                    md_part = _ocr_gemini(frag.image, model_name="gemini-pro-latest")
                elif "Moondream" in engine:
                    md_part = _ocr_ollama(frag.image, model_name="moondream")
                elif "MiniCPM" in engine:
                    md_part = _ocr_ollama(frag.image, model_name="minicpm-v")
                else:
                    md_part = _ocr_local_classic(frag.image)

                frag.markdown_output = md_part
                frag.status = "Listo"
                combined_markdown.append(md_part)

                self.after(0, self._refresh_fragments_list)

            if self._cancel_requested:
                return

            # Unificación final con separación limpia
            final_markdown = "\n\n".join(combined_markdown).strip()

            elapsed = self._stop_timer()
            self.after(0, self._display_markdown, final_markdown)
            self.after(0, self._timer_var.set, f"⏱️ {total} fragmentos en {elapsed:.2f}s")
            self.after(0, self._set_status, f"✅  ¡{total} fragmento(s) unificados en el orden exacto!")

        except Exception as exc:
            if self._cancel_requested:
                return
            self._stop_timer()
            self.after(0, messagebox.showerror, "Error durante la extracción", str(exc))
            self.after(0, self._set_status, "❌  Ocurrió un error al procesar")
            self.after(0, self._timer_var.set, "")

    def _display_markdown(self, text: str) -> None:
        self._md_text.delete("1.0", tk.END)
        self._md_text.insert("1.0", text)

    def _copy_markdown(self) -> None:
        md = self._md_text.get("1.0", tk.END).strip()
        if not md:
            messagebox.showwarning("Vacío", "No hay contenido Markdown para copiar.")
            return
        self.clipboard_clear()
        self.clipboard_append(md)
        self._set_status("📋  Markdown copiado al portapapeles")

    def _save_markdown(self) -> None:
        md = self._md_text.get("1.0", tk.END).strip()
        if not md:
            messagebox.showwarning("Vacío", "No hay texto que guardar.")
            return
        path = filedialog.asksaveasfilename(
            title="Guardar archivo Markdown unificado",
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Texto", "*.txt"), ("Todos los archivos", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(md, encoding="utf-8")
            self._set_status(f"💾  Guardado: {Path(path).name}")
        except Exception as exc:
            messagebox.showerror("Error al guardar", str(exc))

    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)


# ══════════════════════════════════════════════════════════════════════════════
# Entrada Principal
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    app = OcrMarkdownApp()
    app.mainloop()


if __name__ == "__main__":
    main()
