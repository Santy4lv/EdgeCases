"""
human_typer.py — Simulador de tipeo humano para editores de código web
Repositorio: IeeXtreme/EdgeCases

Diseñado y optimizado para CS Academy, HackerRank, LeetCode, Codeforces, Monaco, Ace y CodeMirror.
Novedades:
  1. Editor de Código Visual Estilo IDE (NetBeans / VS Code):
     - Canaleta lateral (Gutter) con números de línea sincronizados al scroll.
     - Resaltado de sintaxis en tiempo real para C++ y Java (palabras clave, tipos, directivas, strings, comentarios y números).
     - Fuente monoespaciada de alta legibilidad (Consolas / Cascadia Code).
  2. Formateador Automático Pre-Tipeo (Format Code):
     - Botón visible en barra superior del editor y atajo global Alt + Shift + F.
     - clang-format si está disponible en el sistema.
     - Formateador nativo en Python (normalización de llaves, sangría a 4 espacios por nivel,
       separación de sentencias en llaves y eliminación de líneas vacías redundantes).
  3. Neutralización inmediata de auto-cierre con Supr (Key.delete).
  4. Tipeo 100% literal carácter por carácter (sin omisiones de llaves o corchetes).
  5. Cierre automático de sugerencias con Esc (evita #include pegado en línea 1).
  6. Kill switch global (Esc físico o ratón a esquina sup-izq).
"""

import math
import random
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from typing import Callable, Optional

import customtkinter as ctk
from pynput import keyboard as pynput_keyboard
from pynput.keyboard import Controller, Key

# ──────────────────────────────────────────────
#  Constantes de Teclado y Delimitadores
# ──────────────────────────────────────────────
QWERTY_NEIGHBORS: dict[str, str] = {
    "q": "was", "w": "qeasd", "e": "wrsdf", "r": "etdfg", "t": "ryfgh",
    "y": "tughj", "u": "yihjk", "i": "uojkl", "o": "ipkl", "p": "ol",
    "a": "qwsz", "s": "awedxz", "d": "serfcx", "f": "drtgvc", "g": "ftyhbv",
    "h": "gyujnb", "j": "huikmn", "k": "jiolm", "l": "kop",
    "z": "asx", "x": "zsdc", "c": "xdfv", "v": "cfgb", "b": "vghn",
    "n": "bhjm", "m": "njk",
    "1": "2q", "2": "13qw", "3": "24we", "4": "35er", "5": "46rt",
    "6": "57ty", "7": "68yu", "8": "79ui", "9": "80io", "0": "9p",
}

# Delimitadores que los editores web autocompletan inmediatamente a la derecha
AUTO_CLOSE_TRIGGERS: set[str] = {"{", "(", "[", '"', "'"}

# ──────────────────────────────────────────────
#  Patrones de Resaltado de Sintaxis (C++ / Java)
# ──────────────────────────────────────────────
SYNTAX_PATTERNS = {
    "keyword": (
        r"\b(auto|break|case|catch|class|const|constexpr|continue|default|delete|"
        r"do|else|enum|explicit|export|extern|final|finally|for|friend|goto|if|"
        r"implements|import|inline|instanceof|interface|mutable|namespace|new|"
        r"noexcept|operator|override|package|private|protected|public|return|"
        r"sizeof|static|struct|super|switch|synchronized|template|this|throw|"
        r"throws|try|typedef|typeid|typename|union|using|virtual|volatile|while)\b"
    ),
    "type": (
        r"\b(int|long|short|char|float|double|bool|boolean|byte|void|size_t|"
        r"int64_t|uint64_t|string|String|Integer|Double|Boolean|vector|map|set|"
        r"pair|unordered_map|unordered_set|queue|deque|stack|priority_queue|"
        r"array|tuple|List|ArrayList|HashMap|HashSet|Scanner)\b"
    ),
    "preproc": r"^\s*#\s*(include|define|ifdef|ifndef|endif|pragma|if|elif|else)\b.*",
    "constant": r"\b(true|false|nullptr|NULL|null|\d+(\.\d+)?([eE][+-]?\d+)?)\b",
    "string": r'("(\\.|[^"\\])*"|\'(\\.|[^\'\\])*\')',
    "comment": r"(//.*$|/\*[\s\S]*?\*/)",
}


# ──────────────────────────────────────────────
#  Motor de Formateo de Código (Clang + Nativo)
# ──────────────────────────────────────────────
def native_format_code(code: str) -> str:
    """
    Formateador sintáctico nativo en Python estilo NetBeans:
    1. rstrip en cada línea.
    2. Colapso de líneas en blanco consecutivas (máx 1 consecutiva).
    3. Separación de llaves pegadas a sentencias de una línea.
    4. Sangría jerárquica a 4 espacios por nivel según llaves { y }.
    """
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    lines = code.split("\n")
    cleaned_lines = [line.rstrip() for line in lines]

    # Colapso de líneas en blanco repetidas
    collapsed: list[str] = []
    prev_blank = False
    for line in cleaned_lines:
        is_blank = not line.strip()
        if is_blank:
            if not prev_blank:
                collapsed.append("")
            prev_blank = True
        else:
            collapsed.append(line.strip())
            prev_blank = False

    # Separar llaves si están pegadas en una sola línea (ej. `int main() { int x = 5;`)
    split_lines: list[str] = []
    for line in collapsed:
        if not line:
            split_lines.append("")
            continue

        # Evitar romper inicializadores de arreglos/vectores e.g. `= {1, 2, 3};`
        if re.search(r"=\s*\{.*\}\s*;", line):
            split_lines.append(line)
            continue

        # Si { está dentro de la línea y no es el último caracter
        if "{" in line and not line.endswith("{"):
            idx = line.find("{")
            before = line[:idx].strip()
            after = line[idx + 1:].strip()
            split_lines.append(before + " {" if before else "{")
            if after:
                split_lines.append(after)
            continue

        split_lines.append(line)

    # Indentación jerárquica
    formatted: list[str] = []
    indent_level = 0
    indent_str = "    "

    for line in split_lines:
        if not line:
            formatted.append("")
            continue

        # Ignorar strings y comentarios para conteo de llaves
        code_wo_strings = re.sub(r'"(\\.|[^"\\])*"', '""', line)
        code_wo_strings = re.sub(r"'(\\.|[^'\\])*'", "''", code_wo_strings)
        code_wo_comments = re.sub(r"//.*$", "", code_wo_strings)

        opens = code_wo_comments.count("{")
        closes = code_wo_comments.count("}")

        # Si la línea empieza con '}', reducir sangría antes de imprimir
        match_lead = re.match(r"^\s*\}*", code_wo_comments)
        leading_closes = len(match_lead.group(0).strip()) if match_lead else 0
        effective_indent = max(0, indent_level - leading_closes)

        # Modificadores de acceso o casos de switch
        if re.match(r"^(public|private|protected)\s*:", line) or re.match(r"^(case\s+.*|default)\s*:", line):
            effective_indent = max(0, effective_indent - 1)

        formatted.append((indent_str * effective_indent) + line)
        indent_level = max(0, indent_level + opens - closes)

    return "\n".join(formatted).strip()


def format_code(source: str) -> tuple[str, str]:
    """
    Intenta formatear mediante clang-format; si no está disponible,
    utiliza el formateador nativo en Python.
    Retorna (código_formateado, motor_usado).
    """
    clang_bin = shutil.which("clang-format")
    if clang_bin:
        try:
            res = subprocess.run(
                [clang_bin, "--style={BasedOnStyle: Google, IndentWidth: 4}"],
                input=source,
                capture_output=True,
                text=True,
                timeout=4,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip(), "clang-format"
        except Exception:
            pass

    # Fallback al normalizador sintáctico nativo
    return native_format_code(source), "formateador nativo"


# ──────────────────────────────────────────────
#  Configuración de Tipeo
# ──────────────────────────────────────────────
@dataclass
class TypingConfig:
    # Cadencia humana
    base_interval_ms: float = 75.0
    jitter_sigma_ms: float = 20.0
    error_rate: float = 0.02
    syntax_pause_enabled: bool = True

    # Modo Editor de Código Web
    code_mode: bool = True
    neutralize_auto_pairs: bool = True    # Enviar Delete tras { ( [ " '
    dismiss_autocomplete: bool = True     # Enviar Esc antes de Enter
    natural_indent_lstrip: bool = True    # line.lstrip() para evitar escalera


# ──────────────────────────────────────────────
#  Controlador de Teclado Nativo (OS Level)
# ──────────────────────────────────────────────
class NativeKeyboardDriver:
    """Envía eventos de teclado físicos reales a través del sistema operativo con pynput."""

    def __init__(self) -> None:
        self._kb = Controller()
        self.is_sending_programmatic_esc: bool = False

    def dismiss_autocomplete_popup(self) -> None:
        """Cierra menús de autocompletado flotantes antes de pulsar Enter."""
        self.is_sending_programmatic_esc = True
        try:
            self._kb.press(Key.esc)
            time.sleep(random.uniform(0.025, 0.035))
            self._kb.release(Key.esc)
        finally:
            time.sleep(0.015)
            self.is_sending_programmatic_esc = False

    def press_enter(self) -> None:
        """Pulsación física de la tecla Enter con tiempo de retención para el DOM del navegador."""
        self._kb.press(Key.enter)
        time.sleep(random.uniform(0.025, 0.040))
        self._kb.release(Key.enter)

    def neutralize_phantom_close(self) -> None:
        """Envía Key.delete para eliminar el cierre fantasma insertado a la derecha."""
        self._kb.press(Key.delete)
        time.sleep(random.uniform(0.015, 0.025))
        self._kb.release(Key.delete)

    def type_char(self, ch: str) -> None:
        """Escribe un carácter respetando tabs y unicode."""
        if ch == "\t":
            self._kb.press(Key.tab)
            time.sleep(0.015)
            self._kb.release(Key.tab)
        else:
            self._kb.type(ch)

    def simulate_typo_correction(self, intended_char: str, delay_fn: Callable) -> None:
        """Simula un error de dedo en tecla adyacente y corrección con Backspace."""
        lower = intended_char.lower()
        neighbors = QWERTY_NEIGHBORS.get(lower, "")
        if not neighbors:
            return

        wrong = random.choice(neighbors)
        if intended_char.isupper():
            wrong = wrong.upper()

        self._kb.type(wrong)
        delay_fn(wrong, is_error=True)

        reaction = random.uniform(0.08, 0.20)
        time.sleep(reaction)

        self._kb.press(Key.backspace)
        time.sleep(0.015)
        self._kb.release(Key.backspace)
        time.sleep(random.uniform(0.04, 0.08))


# ──────────────────────────────────────────────
#  Motor de Inyección y Simulación Humana
# ──────────────────────────────────────────────
class HumanTypingEngine:
    """Ejecuta el bucle de tipeo línea por línea en un hilo de ejecución independiente."""

    def __init__(self, config: Optional[TypingConfig] = None) -> None:
        self.config = config or TypingConfig()
        self.driver = NativeKeyboardDriver()
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._on_progress: Optional[Callable[[int, int], None]] = None
        self._on_finished: Optional[Callable[[], None]] = None

    def start(self, text: str, countdown_sec: int = 5) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self.running = True
        self._thread = threading.Thread(
            target=self._run_loop, args=(text, countdown_sec), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.running = False

    def set_callbacks(
        self,
        on_progress: Callable[[int, int], None],
        on_finished: Callable[[], None],
    ) -> None:
        self._on_progress = on_progress
        self._on_finished = on_finished

    # ── Bucle Principal Línea por Línea ───────
    def _run_loop(self, raw_text: str, countdown_sec: int) -> None:
        normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.splitlines()

        total_steps = sum(
            len(line.lstrip() if (self.config.code_mode and self.config.natural_indent_lstrip) else line)
            for line in lines
        ) + max(0, len(lines) - 1)
        total_steps = max(1, total_steps)

        # Cuenta regresiva
        for rem in range(countdown_sec, 0, -1):
            if self._stop_event.is_set():
                self.running = False
                return
            if self._on_progress:
                self._on_progress(-rem, total_steps)
            time.sleep(1.0)

        progress_counter = 0

        for line_idx, line in enumerate(lines):
            if self._stop_event.is_set():
                break

            # 1. Si la línea está vacía, solo enviar Enter y continuar
            if not line.strip():
                if line_idx < len(lines) - 1:
                    if self.config.code_mode and self.config.dismiss_autocomplete:
                        self.driver.dismiss_autocomplete_popup()
                        time.sleep(0.03)

                    self.driver.press_enter()
                    time.sleep(0.06)

                    progress_counter += 1
                    if self._on_progress:
                        self._on_progress(progress_counter, total_steps)
                continue

            # 2. Tipear contenido con sangría removida
            if self.config.code_mode and self.config.natural_indent_lstrip:
                line_to_type = line.lstrip()
            else:
                line_to_type = line

            for ch in line_to_type:
                if self._stop_event.is_set():
                    break

                if ch.isalnum() and random.random() < self.config.error_rate:
                    self.driver.simulate_typo_correction(ch, self._human_delay)

                # Tipeo literal de todos los caracteres
                self.driver.type_char(ch)

                # Neutralización inmediata del auto-cierre
                if self.config.code_mode and self.config.neutralize_auto_pairs:
                    if ch in AUTO_CLOSE_TRIGGERS:
                        time.sleep(random.uniform(0.015, 0.025))
                        self.driver.neutralize_phantom_close()

                self._human_delay(ch)
                progress_counter += 1
                if self._on_progress:
                    self._on_progress(progress_counter, total_steps)

            # 3. Fin de línea: cerrar sugerencias y saltar de línea
            if line_idx < len(lines) - 1:
                if self._stop_event.is_set():
                    break

                if self.config.code_mode and self.config.dismiss_autocomplete:
                    self.driver.dismiss_autocomplete_popup()
                    time.sleep(0.03)

                self.driver.press_enter()
                time.sleep(0.06)

                if self.config.syntax_pause_enabled:
                    time.sleep(random.uniform(0.08, 0.16))

                progress_counter += 1
                if self._on_progress:
                    self._on_progress(progress_counter, total_steps)

        self.running = False
        if self._on_finished:
            self._on_finished()

    def _human_delay(self, ch: str, is_error: bool = False) -> None:
        base = self.config.base_interval_ms / 1000.0
        sigma = self.config.jitter_sigma_ms / 1000.0
        delay = max(0.012, random.gauss(base, sigma))

        if self.config.syntax_pause_enabled and not is_error:
            if ch in (";", "{", "}"):
                delay += random.uniform(0.12, 0.35)
            elif ch in (",", ")", "]"):
                delay += random.uniform(0.05, 0.15)

        time.sleep(delay)


# ──────────────────────────────────────────────
#  Widget de Editor de Código con Canaleta (Gutter)
# ──────────────────────────────────────────────
class VisualCodeEditor(ctk.CTkFrame):
    """
    Editor de código profesional estilo NetBeans / VS Code:
    - Canaleta izquierda con números de línea sincronizados al scroll.
    - Resaltado de sintaxis en tiempo real para C++ / Java.
    - Fuente monoespaciada Consolas/Cascadia Code.
    """

    def __init__(self, parent, font_family="Consolas", font_size=11, **kwargs):
        super().__init__(parent, **kwargs)
        self.font_family = font_family
        self.font_size = font_size
        self._highlight_timer: Optional[str] = None
        self._on_change_callbacks: list[Callable[[], None]] = []

        self._build_editor()

    def _build_editor(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        editor_font = (self.font_family, self.font_size)

        # Canaleta izquierda para números de línea
        self.gutter = tk.Text(
            self,
            width=4,
            padx=6,
            pady=8,
            takefocus=0,
            border=0,
            background="#121215",
            foreground="#52525b",
            state="disabled",
            font=editor_font,
            wrap=tk.NONE,
            relief="flat",
            cursor="arrow",
        )
        self.gutter.grid(row=0, column=0, sticky="ns")

        # Área de texto principal
        self.text = tk.Text(
            self,
            font=editor_font,
            background="#18181b",
            foreground="#f4f4f5",
            insertbackground="#ffffff",
            selectbackground="#27272a",
            padx=10,
            pady=8,
            wrap=tk.NONE,
            relief="flat",
            borderwidth=0,
            undo=True,
        )
        self.text.grid(row=0, column=1, sticky="nsew")

        # Scrollbar vertical
        self.vsb = ctk.CTkScrollbar(self, orientation="vertical", command=self._on_scrollbar_move)
        self.vsb.grid(row=0, column=2, sticky="ns")

        # Scrollbar horizontal
        self.hsb = ctk.CTkScrollbar(self, orientation="horizontal", command=self.text.xview)
        self.hsb.grid(row=1, column=1, sticky="ew")

        # Conectar scrollbars
        self.text.config(
            yscrollcommand=self._on_text_vscroll,
            xscrollcommand=self.hsb.set,
        )

        # Redireccionar scroll de rueda en gutter hacia el texto principal
        self.gutter.bind("<MouseWheel>", lambda e: self.text.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        # Eventos para actualizar números de línea y sintaxis
        self.text.bind("<<Modified>>", self._on_text_modified)
        self.text.bind("<KeyRelease>", self._on_key_release)
        self.text.bind("<Button-1>", lambda _: self.after(10, self._update_gutter))

        self._configure_syntax_tags()
        self._update_gutter()

    def _configure_syntax_tags(self) -> None:
        """Define los colores del tema de sintaxis oscuro (One Dark / NetBeans Dark)."""
        font_regular = (self.font_family, self.font_size)
        font_italic = (self.font_family, self.font_size, "italic")
        font_bold = (self.font_family, self.font_size, "bold")

        self.text.tag_config("keyword", foreground="#c678dd", font=font_bold)
        self.text.tag_config("type", foreground="#e5c07b")
        self.text.tag_config("preproc", foreground="#e06c75")
        self.text.tag_config("constant", foreground="#d19a66")
        self.text.tag_config("string", foreground="#98c379")
        self.text.tag_config("comment", foreground="#71717a", font=font_italic)

    def _on_text_vscroll(self, *args) -> None:
        """Sincroniza la canaleta de números de línea con el scroll del texto."""
        self.gutter.yview_moveto(args[0])
        self.vsb.set(*args)

    def _on_scrollbar_move(self, *args) -> None:
        """Mueve tanto el texto como la canaleta al arrastrar la barra de scroll."""
        self.text.yview(*args)
        self.gutter.yview(*args)

    def _on_key_release(self, event=None) -> None:
        self._update_gutter()
        self._schedule_highlight()

    def _on_text_modified(self, event=None) -> None:
        self.text.edit_modified(False)
        self._update_gutter()
        self._schedule_highlight()
        for cb in self._on_change_callbacks:
            cb()

    def _schedule_highlight(self) -> None:
        """Debounce de 120 ms para resaltar sintaxis sin penalizar rendimiento."""
        if self._highlight_timer:
            self.after_cancel(self._highlight_timer)
        self._highlight_timer = self.after(120, self.highlight_syntax)

    def _update_gutter(self) -> None:
        """Calcula y actualiza los números de línea."""
        line_count = int(self.text.index("end-1c").split(".")[0])
        gutter_width = max(3, len(str(line_count)) + 1)
        self.gutter.config(width=gutter_width)

        numbers = "\n".join(str(i) for i in range(1, line_count + 1))
        self.gutter.config(state="normal")
        self.gutter.delete("1.0", "end")
        self.gutter.insert("1.0", numbers)
        self.gutter.config(state="disabled")

        # Asegurar alineación vertical con el texto
        y_pos = self.text.yview()[0]
        self.gutter.yview_moveto(y_pos)

    def highlight_syntax(self) -> None:
        """Aplica las etiquetas de resaltado según expresiones regulares."""
        content = self.text.get("1.0", "end-1c")
        if not content:
            return

        # Limpiar tags previos
        for tag in SYNTAX_PATTERNS.keys():
            self.text.tag_remove(tag, "1.0", "end")

        def apply_regex(pattern: str, tag: str, flags: int = 0) -> None:
            for match in re.finditer(pattern, content, flags):
                start = f"1.0 + {match.start()} chars"
                end = f"1.0 + {match.end()} chars"
                self.text.tag_add(tag, start, end)

        # Aplicar ordenadamente (comentarios al final para sobreescribir)
        apply_regex(SYNTAX_PATTERNS["keyword"], "keyword")
        apply_regex(SYNTAX_PATTERNS["type"], "type")
        apply_regex(SYNTAX_PATTERNS["constant"], "constant")
        apply_regex(SYNTAX_PATTERNS["preproc"], "preproc", re.MULTILINE)
        apply_regex(SYNTAX_PATTERNS["string"], "string")
        apply_regex(SYNTAX_PATTERNS["comment"], "comment", re.MULTILINE)

    def add_on_change_callback(self, cb: Callable[[], None]) -> None:
        self._on_change_callbacks.append(cb)

    def get_text(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set_text(self, new_text: str) -> None:
        self.text.delete("1.0", "end")
        self.text.insert("1.0", new_text)
        self._update_gutter()
        self.highlight_syntax()


# ──────────────────────────────────────────────
#  Interfaz de Usuario Principal (CustomTkinter)
# ──────────────────────────────────────────────
class HumanTyperApp(ctk.CTk):
    COUNTDOWN_SECS = 5

    def __init__(self) -> None:
        super().__init__()
        self.title("Human Typer  ·  CS Academy & Web Code Editor Suite")
        self.geometry("900x820")
        self.minsize(820, 720)

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.config = TypingConfig()
        self.engine = HumanTypingEngine(self.config)
        self.engine.set_callbacks(self._on_progress_async, self._on_finished_async)

        # Kill switch global via pynput listener
        self._kb_listener = pynput_keyboard.Listener(on_press=self._global_key_press)
        self._kb_listener.start()

        self._build_ui()

        # Atajo global de formateo de código: Alt + Shift + F
        self.bind_all("<Alt-Shift-KeyPress-F>", lambda _: self._on_format_code())
        self.bind_all("<Alt-Shift-KeyPress-f>", lambda _: self._on_format_code())

    # ── Construcción de la Interfaz ───────────
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── 1. Header ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 2))
        ctk.CTkLabel(
            header,
            text="🖮  Human Typer",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text="Simulador de cadencia humana con editor IDE y formateo previo",
            font=ctk.CTkFont(size=12),
            text_color="gray65",
        ).pack(side="left", padx=14)

        # ── 2. Editor de Código Visual Estilo IDE ──
        editor_container = ctk.CTkFrame(self)
        editor_container.grid(row=1, column=0, sticky="nsew", padx=18, pady=6)
        editor_container.grid_columnconfigure(0, weight=1)
        editor_container.grid_rowconfigure(1, weight=1)

        # Barra superior del editor (Toolbar con botón Format)
        toolbar = ctk.CTkFrame(editor_container, fg_color="transparent", height=32)
        toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 4))
        toolbar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            toolbar,
            text="🗎 Código Fuente (C++ / Java):",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="gray75",
        ).grid(row=0, column=0, sticky="w")

        # Badge de estado de formateo
        self.lbl_format_badge = ctk.CTkLabel(
            toolbar,
            text="",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#4ade80",
        )
        self.lbl_format_badge.grid(row=0, column=1, sticky="e", padx=(0, 10))

        # Botón Formatear Código
        self.btn_format = ctk.CTkButton(
            toolbar,
            text="✨ Formatear Código (Alt+Shift+F)",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=28,
            width=220,
            command=self._on_format_code,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
        )
        self.btn_format.grid(row=0, column=2, sticky="e")

        # Instancia del editor visual con canaleta y sintaxis
        self.editor = VisualCodeEditor(editor_container)
        self.editor.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.editor.add_on_change_callback(self._update_text_stats)

        # Barra inferior de estadísticas del editor
        self.lbl_stats = ctk.CTkLabel(
            editor_container,
            text="0 caracteres · 0 palabras · 0 líneas",
            font=ctk.CTkFont(size=11),
            text_color="gray55",
        )
        self.lbl_stats.grid(row=2, column=0, sticky="e", padx=10, pady=(2, 4))

        # ── 3. Panel: Modo Editor de Código Web ──
        code_card = ctk.CTkFrame(self)
        code_card.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 6))
        code_card.grid_columnconfigure((0, 1), weight=1)

        self.sw_code_mode = ctk.CTkSwitch(
            code_card,
            text="Modo Editor de Código Web (Neutralizar auto-cierre y sangría natural)",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._sync_code_mode_state,
        )
        self.sw_code_mode.grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(8, 6))
        self.sw_code_mode.select()

        opts_sub = ctk.CTkFrame(code_card, fg_color="transparent")
        opts_sub.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8))

        self.chk_neutralize = ctk.CTkCheckBox(
            opts_sub,
            text="Neutralizar auto-cierre (Supr tras {, (, [, \", ')",
            font=ctk.CTkFont(size=12),
            command=self._sync_config,
        )
        self.chk_neutralize.pack(side="left", padx=(0, 16))
        self.chk_neutralize.select()

        self.chk_lstrip = ctk.CTkCheckBox(
            opts_sub,
            text="Sangría natural (lstrip para evitar escalera)",
            font=ctk.CTkFont(size=12),
            command=self._sync_config,
        )
        self.chk_lstrip.pack(side="left", padx=(0, 16))
        self.chk_lstrip.select()

        self.chk_dismiss = ctk.CTkCheckBox(
            opts_sub,
            text="Cerrar sugerencias con Esc (Evita #include pegado)",
            font=ctk.CTkFont(size=12),
            command=self._sync_config,
        )
        self.chk_dismiss.pack(side="left")
        self.chk_dismiss.select()

        # ── 4. Panel: Sliders de Simulación Humana ──
        human_card = ctk.CTkFrame(self)
        human_card.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 6))
        human_card.grid_columnconfigure((0, 1, 2), weight=1)

        self._build_slider(
            human_card, col=0,
            label="⏱ Velocidad base",
            from_=30, to=150, initial=75,
            fmt=lambda v: f"{v:.0f} ms/tecla",
            on_change=lambda v: setattr(self.config, "base_interval_ms", v),
        )

        self._build_slider(
            human_card, col=1,
            label="〰 Variabilidad gaussiana (Jitter)",
            from_=0, to=80, initial=20,
            fmt=lambda v: f"±{v:.0f} ms σ",
            on_change=lambda v: setattr(self.config, "jitter_sigma_ms", v),
        )

        self._build_slider(
            human_card, col=2,
            label="✗ Tasa de errores con corrección",
            from_=0, to=15, initial=2,
            fmt=lambda v: f"{v:.1f}%",
            on_change=lambda v: setattr(self.config, "error_rate", v / 100.0),
        )

        # ── 5. Progreso y Estado ──
        prog_frame = ctk.CTkFrame(self, fg_color="transparent")
        prog_frame.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 4))
        prog_frame.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(prog_frame, height=10)
        self.progress_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.progress_bar.set(0)

        self.lbl_status = ctk.CTkLabel(
            prog_frame,
            text="Listo · Pega el código, formatéalo si lo deseas y pulsa Iniciar tipeo.",
            font=ctk.CTkFont(size=12),
            text_color="gray65",
        )
        self.lbl_status.grid(row=1, column=0, sticky="w")

        # ── 6. Botones de Control y Kill Switch ──
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=5, column=0, sticky="ew", padx=18, pady=(4, 14))

        self.btn_start = ctk.CTkButton(
            btn_frame,
            text="▶  Iniciar tipeo (5s)",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=210,
            height=42,
            command=self._start_execution,
            fg_color="#15803d",
            hover_color="#166534",
        )
        self.btn_start.pack(side="left", padx=(0, 12))

        self.btn_stop = ctk.CTkButton(
            btn_frame,
            text="■  Detener  [Esc]",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=180,
            height=42,
            command=self.engine.stop,
            fg_color="#b91c1c",
            hover_color="#991b1b",
            state="disabled",
        )
        self.btn_stop.pack(side="left", padx=(0, 14))

        ctk.CTkLabel(
            btn_frame,
            text="🚨 Kill Switch: Pulsa tecla Esc  |  O mueve el ratón a la esquina sup-izq",
            font=ctk.CTkFont(size=11),
            text_color="gray55",
        ).pack(side="left")

        self._sync_config()

    def _build_slider(
        self, parent, col: int, label: str,
        from_: float, to: float, initial: float,
        fmt: Callable[[float], str], on_change: Callable[[float], None]
    ) -> None:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=0, column=col, padx=14, pady=8, sticky="ew")

        ctk.CTkLabel(frame, text=label, font=ctk.CTkFont(size=12)).pack(anchor="w")

        val_label = ctk.CTkLabel(
            frame, text=fmt(initial), font=ctk.CTkFont(size=12, weight="bold")
        )
        val_label.pack(anchor="w", pady=(0, 2))

        def _callback(v: float) -> None:
            val_label.configure(text=fmt(float(v)))
            on_change(float(v))

        slider = ctk.CTkSlider(frame, from_=from_, to=to, command=_callback, width=210)
        slider.set(initial)
        slider.pack(fill="x")
        on_change(initial)

    # ── Función Formatear Código (Format Code) ──
    def _on_format_code(self) -> None:
        raw_code = self.editor.get_text()
        if not raw_code.strip():
            self._set_status("⚠ El editor está vacío; no hay código para formatear.", "orange")
            return

        formatted, engine_name = format_code(raw_code)
        self.editor.set_text(formatted)

        # Mostrar aviso de éxito en el badge
        badge_msg = f"✓ Código formateado correctamente ({engine_name})"
        self.lbl_format_badge.configure(text=badge_msg)
        self._set_status(f"✨ Formateo completado mediante {engine_name}.", "#4ade80")

        # Desvanecer el badge tras 3.5 segundos
        self.after(3500, lambda: self.lbl_format_badge.configure(text=""))

    # ── Sincronización de Controles ───────────
    def _sync_code_mode_state(self) -> None:
        active = bool(self.sw_code_mode.get())
        state = "normal" if active else "disabled"
        self.chk_neutralize.configure(state=state)
        self.chk_lstrip.configure(state=state)
        self.chk_dismiss.configure(state=state)
        self._sync_config()

    def _sync_config(self) -> None:
        self.config.code_mode = bool(self.sw_code_mode.get())
        self.config.neutralize_auto_pairs = bool(self.chk_neutralize.get())
        self.config.natural_indent_lstrip = bool(self.chk_lstrip.get())
        self.config.dismiss_autocomplete = bool(self.chk_dismiss.get())

    def _update_text_stats(self) -> None:
        content = self.editor.get_text()
        chars = len(content)
        words = len(content.split())
        lines = len(content.splitlines()) if content else 0
        self.lbl_stats.configure(
            text=f"{chars} caracteres · {words} palabras · {lines} líneas"
        )

    # ── Flujo de Ejecución ────────────────────
    def _start_execution(self) -> None:
        code_to_type = self.editor.get_text().rstrip("\r\n")
        if not code_to_type.strip():
            self._set_status("⚠ El área de texto está vacía. Pega código primero.", "orange")
            return
        if self.engine.running:
            return

        self._sync_config()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress_bar.set(0)
        self._set_status(
            f"⏳ Iniciando en {self.COUNTDOWN_SECS}s — Haz clic en el editor destino…",
            "#f59e0b",
        )

        self.engine.start(code_to_type, countdown_sec=self.COUNTDOWN_SECS)

    # ── Callbacks Asíncronos (Thread-safe) ────
    def _on_progress_async(self, current: int, total: int) -> None:
        self.after(0, self._update_progress_ui, current, total)

    def _update_progress_ui(self, current: int, total: int) -> None:
        if current <= 0:
            remaining = abs(current)
            self._set_status(
                f"⏳ Iniciando en {remaining}s — ¡Enfoca la ventana destino!",
                "#f59e0b",
            )
            self.progress_bar.set(0)
        else:
            ratio = current / total if total > 0 else 0
            self.progress_bar.set(ratio)
            pct = ratio * 100
            self._set_status(
                f"⌨ Tipeando… {current}/{total} pasos ({pct:.1f}%)",
                "#4ade80",
            )

    def _on_finished_async(self) -> None:
        self.after(0, self._on_finished_ui)

    def _on_finished_ui(self) -> None:
        self.progress_bar.set(1.0)
        self._set_status("✅ Tipeo finalizado con éxito.", "#4ade80")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    # ── Kill Switch Global (Esc) ──────────────
    def _global_key_press(self, key) -> None:
        if key == pynput_keyboard.Key.esc:
            if getattr(self.engine.driver, "is_sending_programmatic_esc", False):
                return
            if self.engine.running:
                self.engine.stop()
                self.after(0, self._on_emergency_stop_ui)

    def _on_emergency_stop_ui(self) -> None:
        self._set_status("🛑 DETENIDO: Parada de emergencia activada [Esc].", "#ef4444")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def _set_status(self, text: str, color: str = "gray65") -> None:
        self.lbl_status.configure(text=text, text_color=color)

    # ── Cierre Limpio de la Aplicación ───────
    def on_close(self) -> None:
        self.engine.stop()
        try:
            self._kb_listener.stop()
        except Exception:
            pass
        self.destroy()


# ──────────────────────────────────────────────
#  Punto de Entrada
# ──────────────────────────────────────────────
if __name__ == "__main__":
    try:
        import pyautogui
        pyautogui.FAILSAFE = True
    except ImportError:
        pass

    app = HumanTyperApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
