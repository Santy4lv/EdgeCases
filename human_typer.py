"""
human_typer.py — Simulador de tipeo humano para editores de código web y de escritorio
Repositorio: IeeXtreme/EdgeCases

Diseñado y optimizado para CS Academy, HackerRank, LeetCode, Codeforces, Monaco, Ace, CodeMirror y editores IDE.
Modos de Inyección:
  1. Modo Playwright (Segundo Plano · Autónomo · No bloquea teclado ni ventana):
     - Inyecta eventos de teclado directamente en el proceso del navegador mediante Chrome DevTools Protocol.
     - Permite al usuario continuar programando, usando VS Code u otras aplicaciones sin interferencia.
     - Perfil persistente (.browser_profile): conserva sesiones, cookies y estado de pestañas.
     - Detección inteligente y enfoque de editores web: Monaco Editor, Ace Editor, CodeMirror 5/6, textareas e iframes.
     - Conexión opcional a instancias existentes de Chrome vía CDP (puerto 9222).
     - Limpieza previa opcional del editor destino (Ctrl+A + Supr).
  2. Modo Teclado Nativo OS (pynput):
     - Genera pulsaciones físicas del sistema operativo (ideal para editores locales como VS Code o Bloc de Notas).
     - Cuenta regresiva de 5 segundos para enfocar la ventana destino.
  3. Detección visual de líneas modificadas (Git-diff / IDE style):
     - Al editar o retocar cualquier línea antes de iniciar, la línea se resalta en ámbar cálido.
     - La canaleta (Gutter) marca con '~' y color ámbar brillante las líneas tocadas.
     - Contador interactivo en la barra superior con lista de líneas modificadas y botón para fijar base.
  4. Editor de Código Visual Estilo IDE (NetBeans / VS Code):
     - Canaleta lateral (Gutter) con números de línea sincronizados al scroll.
     - Resaltado de sintaxis en tiempo real para C++ y Java (palabras clave, tipos, directivas, strings, comentarios y números).
     - Fuente monoespaciada de alta legibilidad (Consolas / Cascadia Code).
  5. Formateador Automático Pre-Tipeo (Format Code):
     - Botón visible en barra superior del editor y atajo global Alt + Shift + F.
     - clang-format si está disponible; fallback a normalizador sintáctico nativo en Python.
  6. Neutralización inmediata de auto-cierre con Supr (Key.delete).
  7. Tipeo 100% literal carácter por carácter (sin omisiones de llaves o corchetes).
  8. Cierre automático de sugerencias con Esc (evita #include pegado en línea 1).
  9. Kill switch global seguro (Esc físico detiene tanto modo nativo como Playwright).
"""

import difflib
import math
import os
import queue
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

# ── Importación Segura de Playwright ──────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    sync_playwright = None
    PLAYWRIGHT_AVAILABLE = False


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
    3. Separación de llaves pegadas a sentencias en la misma línea.
    4. Sangría jerárquica a 4 espacios por nivel según llaves { y }.
    """
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    lines = code.split("\n")
    cleaned_lines = [line.rstrip() for line in lines]

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

    split_lines: list[str] = []
    for line in collapsed:
        if not line:
            split_lines.append("")
            continue

        # Preservar inicializadores de arrays/vectores e.g. `= {1, 2, 3};`
        if re.search(r"=\s*\{.*\}\s*;", line):
            split_lines.append(line)
            continue

        # Separar { si no está al final de la línea
        if "{" in line and not line.endswith("{"):
            idx = line.find("{")
            before = line[:idx].strip()
            after = line[idx + 1:].strip()
            split_lines.append(before + " {" if before else "{")
            if after:
                split_lines.append(after)
            continue

        split_lines.append(line)

    formatted: list[str] = []
    indent_level = 0
    indent_str = "    "

    for line in split_lines:
        if not line:
            formatted.append("")
            continue

        code_wo_strings = re.sub(r'"(\\.|[^"\\])*"', '""', line)
        code_wo_strings = re.sub(r"'(\\.|[^'\\])*'", "''", code_wo_strings)
        code_wo_comments = re.sub(r"//.*$", "", code_wo_strings)

        opens = code_wo_comments.count("{")
        closes = code_wo_comments.count("}")

        match_lead = re.match(r"^\s*\}*", code_wo_comments)
        leading_closes = len(match_lead.group(0).strip()) if match_lead else 0
        effective_indent = max(0, indent_level - leading_closes)

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

    # Modo de inyección
    injection_mode: str = "playwright" if PLAYWRIGHT_AVAILABLE else "native"
    clear_before_typing: bool = False     # Limpiar editor antes de tipear (Playwright)
    custom_selector: str = ""             # Selector CSS opcional


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
#  Motor de Inyección Nativo (pynput)
# ──────────────────────────────────────────────
class HumanTypingEngine:
    """Ejecuta el bucle de tipeo nativo (OS) línea por línea en un hilo de ejecución."""

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

    def _run_loop(self, raw_text: str, countdown_sec: int) -> None:
        normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.splitlines()

        total_steps = sum(
            len(line.lstrip() if (self.config.code_mode and self.config.natural_indent_lstrip) else line)
            for line in lines
        ) + max(0, len(lines) - 1)
        total_steps = max(1, total_steps)

        # Cuenta regresiva para enfocar la ventana destino
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

            if self.config.code_mode and self.config.natural_indent_lstrip:
                line_to_type = line.lstrip()
            else:
                line_to_type = line

            for ch in line_to_type:
                if self._stop_event.is_set():
                    break

                if ch.isalnum() and random.random() < self.config.error_rate:
                    self.driver.simulate_typo_correction(ch, self._human_delay)

                self.driver.type_char(ch)

                if self.config.code_mode and self.config.neutralize_auto_pairs:
                    if ch in AUTO_CLOSE_TRIGGERS:
                        time.sleep(random.uniform(0.015, 0.025))
                        self.driver.neutralize_phantom_close()

                self._human_delay(ch)
                progress_counter += 1
                if self._on_progress:
                    self._on_progress(progress_counter, total_steps)

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
#  Trabajador y Gestor Playwright (Segundo Plano)
# ──────────────────────────────────────────────
class PlaywrightWorker:
    """
    Gestiona la sesión de Playwright en un hilo dedicado con cola de mensajes.
    Permite:
      - Abrir navegador con perfil persistente (guarda sesiones y logins).
      - Conectarse a Chrome existente por CDP (puerto 9222).
      - Listar y alternar entre pestañas abiertas.
      - Detectar y enfocar editores de código (Monaco, Ace, CodeMirror 5/6, textareas e iframes).
      - Tipear en segundo plano mediante CDP sin robar el foco de Windows ni bloquear el teclado.
    """

    def __init__(self) -> None:
        self._cmd_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self.is_connected = False
        self.browser_mode = "desconectado"
        self.running_typing = False
        self._thread: Optional[threading.Thread] = None

        if PLAYWRIGHT_AVAILABLE:
            self._thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._thread.start()

    def _worker_loop(self) -> None:
        if not PLAYWRIGHT_AVAILABLE or sync_playwright is None:
            while True:
                req = self._cmd_queue.get()
                if req is None:
                    break
                cmd, args, resp_q = req
                if cmd == "QUIT":
                    break
                if resp_q:
                    resp_q.put((False, "Playwright no está instalado. Ejecuta: pip install playwright"))
            return

        with sync_playwright() as p:
            self.p = p
            self.context = None
            self.browser = None
            self.selected_tab_idx = 0

            while True:
                req = self._cmd_queue.get()
                if req is None:
                    break
                cmd, args, resp_q = req

                try:
                    if cmd == "QUIT":
                        self._cleanup_internal()
                        if resp_q:
                            resp_q.put((True, "Quit OK"))
                        break

                    elif cmd == "LAUNCH":
                        profile_dir = args
                        self._cleanup_internal()
                        launched = False
                        err_detail = ""

                        # 1. Intentar Chrome nativo del sistema
                        try:
                            self.context = p.chromium.launch_persistent_context(
                                user_data_dir=profile_dir,
                                headless=False,
                                channel="chrome",
                                args=["--no-first-run", "--no-default-browser-check"],
                            )
                            self.browser_mode = "Google Chrome (Persistente)"
                            launched = True
                        except Exception as e_chrome:
                            err_detail = str(e_chrome)

                        # 2. Fallback a Chromium de Playwright
                        if not launched:
                            try:
                                self.context = p.chromium.launch_persistent_context(
                                    user_data_dir=profile_dir,
                                    headless=False,
                                    args=["--no-first-run", "--no-default-browser-check"],
                                )
                                self.browser_mode = "Chromium (Persistente)"
                                launched = True
                            except Exception as e_chromium:
                                err_detail = str(e_chromium)

                        if launched and self.context:
                            if not self.context.pages:
                                self.context.new_page()
                            self.selected_tab_idx = 0
                            self.is_connected = True
                            if resp_q:
                                resp_q.put((True, f"Navegador abierto ({self.browser_mode})"))
                        else:
                            if resp_q:
                                msg = f"No se pudo iniciar el navegador: {err_detail}. Si es necesario ejecuta: playwright install chromium"
                                resp_q.put((False, msg))

                    elif cmd == "CONNECT_CDP":
                        cdp_url = args
                        self._cleanup_internal()
                        self.browser = p.chromium.connect_over_cdp(cdp_url)
                        self.context = (
                            self.browser.contexts[0]
                            if self.browser.contexts
                            else self.browser.new_context()
                        )
                        if not self.context.pages:
                            self.context.new_page()
                        self.selected_tab_idx = 0
                        self.browser_mode = f"CDP ({cdp_url})"
                        self.is_connected = True
                        if resp_q:
                            resp_q.put((True, f"Conectado vía CDP a {cdp_url}"))

                    elif cmd == "GET_TABS":
                        if not self.context or not self._is_alive():
                            self.is_connected = False
                            if resp_q:
                                resp_q.put((False, []))
                            continue

                        tabs = []
                        for i, pg in enumerate(self.context.pages):
                            try:
                                title = pg.title() or "(Sin título)"
                                url = pg.url or "about:blank"
                                tabs.append({"index": i, "title": title, "url": url})
                            except Exception:
                                pass
                        if resp_q:
                            resp_q.put((True, tabs))

                    elif cmd == "SELECT_TAB":
                        idx = args
                        if self.context and 0 <= idx < len(self.context.pages):
                            self.selected_tab_idx = idx
                            if resp_q:
                                resp_q.put((True, f"Pestaña {idx} seleccionada"))
                        else:
                            if resp_q:
                                resp_q.put((False, "Índice de pestaña inválido"))

                    elif cmd == "DETECT_EDITOR":
                        page = self._get_active_page()
                        if not page:
                            if resp_q:
                                resp_q.put((False, {"type": "Sin pestaña activa", "ok": False}))
                            continue
                        custom_sel = args or ""
                        info = self._detect_and_focus_editor(page, custom_sel)
                        if resp_q:
                            resp_q.put((True, info))

                    elif cmd == "START_TYPING":
                        text, config, countdown, on_prog, on_finish = args
                        page = self._get_active_page()
                        if not page:
                            if resp_q:
                                resp_q.put((False, "No hay pestaña activa en el navegador"))
                            if on_finish:
                                on_finish(False, "No hay pestaña activa")
                            continue

                        if resp_q:
                            resp_q.put((True, "Tipeo en curso"))
                        self.running_typing = True
                        self._run_typing_loop(page, text, config, countdown, on_prog, on_finish)
                        self.running_typing = False

                    elif cmd == "CLOSE":
                        self._cleanup_internal()
                        if resp_q:
                            resp_q.put((True, "Navegador desconectado"))

                except Exception as e:
                    if resp_q:
                        resp_q.put((False, str(e)))

    def _is_alive(self) -> bool:
        if not self.context:
            return False
        try:
            return len(self.context.pages) > 0
        except Exception:
            return False

    def _get_active_page(self):
        if not self.context or not self.context.pages:
            return None
        if 0 <= self.selected_tab_idx < len(self.context.pages):
            return self.context.pages[self.selected_tab_idx]
        return self.context.pages[0]

    def _cleanup_internal(self) -> None:
        self.is_connected = False
        self.browser_mode = "desconectado"
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        self.context = None
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        self.browser = None

    def _detect_and_focus_editor(self, page, custom_selector: str = "") -> dict:
        """
        Detecta y enfoca automáticamente editores web como Monaco, Ace, CodeMirror,
        textareas nativas o elementos editables, buscando tanto en el documento principal
        como en posibles iframes embebidos.
        """
        detection_script = f"""
        (() => {{
            const customSel = {repr(custom_selector)};
            if (customSel && customSel.trim().length > 0) {{
                try {{
                    const el = document.querySelector(customSel);
                    if (el) {{
                        el.focus();
                        return {{ type: "Selector personalizado ('" + customSel + "')", ok: true }};
                    }}
                }} catch(e) {{
                    return {{ type: "Error selector: " + e.message, ok: false }};
                }}
            }}

            // 1. Monaco Editor (LeetCode, HackerRank nuevo, VS Code Web)
            try {{
                if (window.monaco && window.monaco.editor) {{
                    const eds = window.monaco.editor.getEditors();
                    if (eds && eds.length > 0) {{
                        eds[0].focus();
                        return {{ type: "Monaco Editor (API)", ok: true }};
                    }}
                }}
                const mta = document.querySelector('.monaco-editor textarea');
                if (mta) {{
                    mta.focus();
                    return {{ type: "Monaco Editor (DOM)", ok: true }};
                }}
            }} catch(e) {{}}

            // 2. Ace Editor (CS Academy, HackerRank clásico)
            try {{
                if (window.ace) {{
                    const aceEl = document.querySelector('.ace_editor');
                    if (aceEl && window.ace.edit) {{
                        window.ace.edit(aceEl).focus();
                        return {{ type: "Ace Editor (API)", ok: true }};
                    }}
                }}
                const ata = document.querySelector('.ace_editor textarea.ace_text-input, textarea.ace_text-input');
                if (ata) {{
                    ata.focus();
                    return {{ type: "Ace Editor (DOM)", ok: true }};
                }}
            }} catch(e) {{}}

            // 3. CodeMirror 5 y 6 (Codeforces, etc.)
            try {{
                const cm6 = document.querySelector('.cm-content[contenteditable="true"]');
                if (cm6) {{
                    cm6.focus();
                    return {{ type: "CodeMirror 6", ok: true }};
                }}
                const cm5 = document.querySelector('.CodeMirror textarea');
                if (cm5) {{
                    cm5.focus();
                    return {{ type: "CodeMirror 5", ok: true }};
                }}
            }} catch(e) {{}}

            // 4. Elemento actualmente activo si es editable
            const active = document.activeElement;
            if (active && (active.tagName === 'TEXTAREA' || active.isContentEditable || active.tagName === 'INPUT')) {{
                return {{ type: "Elemento Activo (" + active.tagName + ")", ok: true }};
            }}

            // 5. Textarea estándar visible
            const visibleTA = Array.from(document.querySelectorAll('textarea')).find(el => el.offsetParent !== null);
            if (visibleTA) {{
                visibleTA.focus();
                return {{ type: "Textarea estándar", ok: true }};
            }}

            // 6. ContentEditable visible
            const ce = Array.from(document.querySelectorAll('[contenteditable="true"]')).find(el => el.offsetParent !== null);
            if (ce) {{
                ce.focus();
                return {{ type: "ContentEditable", ok: true }};
            }}

            return null;
        }})()
        """
        # Intentar en la página principal
        try:
            res = page.evaluate(detection_script)
            if res and res.get("ok"):
                return res
        except Exception:
            pass

        # Buscar en iframes si no se detectó en la ventana principal
        try:
            for fr in page.frames:
                try:
                    res_fr = fr.evaluate(detection_script)
                    if res_fr and res_fr.get("ok"):
                        res_fr["type"] += " (en iframe)"
                        return res_fr
                except Exception:
                    continue
        except Exception:
            pass

        return {"type": "Ningún editor detectado automáticamente", "ok": False}

    def _run_typing_loop(
        self,
        page,
        raw_text: str,
        config: TypingConfig,
        countdown_sec: int,
        on_progress: Optional[Callable[[int, int], None]],
        on_finished: Optional[Callable[[bool, str], None]],
    ) -> None:
        self._stop_event.clear()

        # Cuenta regresiva breve
        for rem in range(countdown_sec, 0, -1):
            if self._stop_event.is_set():
                if on_finished:
                    on_finished(False, "Tipeo cancelado por el usuario.")
                return
            if on_progress:
                on_progress(-rem, 100)
            time.sleep(1.0)

        # 1. Asegurar enfoque en el editor destino
        self._detect_and_focus_editor(page, config.custom_selector)

        # 2. Limpieza previa opcional del editor
        if config.clear_before_typing:
            try:
                page.keyboard.press("Control+A")
                time.sleep(0.04)
                page.keyboard.press("Backspace")
                time.sleep(0.05)
            except Exception:
                pass

        normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.splitlines()

        total_steps = sum(
            len(line.lstrip() if (config.code_mode and config.natural_indent_lstrip) else line)
            for line in lines
        ) + max(0, len(lines) - 1)
        total_steps = max(1, total_steps)

        progress_counter = 0

        for line_idx, line in enumerate(lines):
            if self._stop_event.is_set():
                break

            # Línea vacía: solo enviar Enter
            if not line.strip():
                if line_idx < len(lines) - 1:
                    if config.code_mode and config.dismiss_autocomplete:
                        page.keyboard.press("Escape")
                        time.sleep(0.02)

                    page.keyboard.press("Enter")
                    time.sleep(0.04)

                    progress_counter += 1
                    if on_progress:
                        on_progress(progress_counter, total_steps)
                continue

            line_to_type = (
                line.lstrip()
                if (config.code_mode and config.natural_indent_lstrip)
                else line
            )

            for ch in line_to_type:
                if self._stop_event.is_set():
                    break

                # Simulación de error tipográfico con autocorrección
                if ch.isalnum() and random.random() < config.error_rate:
                    lower = ch.lower()
                    neighbors = QWERTY_NEIGHBORS.get(lower, "")
                    if neighbors:
                        wrong = random.choice(neighbors)
                        if ch.isupper():
                            wrong = wrong.upper()

                        page.keyboard.type(wrong)
                        self._human_delay(config, wrong, is_error=True)
                        time.sleep(random.uniform(0.06, 0.14))
                        page.keyboard.press("Backspace")
                        time.sleep(random.uniform(0.03, 0.06))

                # Tipeo del carácter
                if ch == "\t":
                    page.keyboard.press("Tab")
                else:
                    page.keyboard.type(ch)

                # Neutralización del auto-cierre con Supr
                if config.code_mode and config.neutralize_auto_pairs:
                    if ch in AUTO_CLOSE_TRIGGERS:
                        time.sleep(random.uniform(0.012, 0.022))
                        page.keyboard.press("Delete")

                self._human_delay(config, ch)
                progress_counter += 1
                if on_progress:
                    on_progress(progress_counter, total_steps)

            # Fin de línea: cerrar sugerencias emergentes y saltar línea
            if line_idx < len(lines) - 1:
                if self._stop_event.is_set():
                    break

                if config.code_mode and config.dismiss_autocomplete:
                    page.keyboard.press("Escape")
                    time.sleep(0.02)

                page.keyboard.press("Enter")
                time.sleep(0.04)

                if config.syntax_pause_enabled:
                    time.sleep(random.uniform(0.06, 0.14))

                progress_counter += 1
                if on_progress:
                    on_progress(progress_counter, total_steps)

        was_stopped = self._stop_event.is_set()
        if on_finished:
            msg = "Tipeo detenido por el usuario." if was_stopped else "Tipeo finalizado con éxito en Playwright."
            on_finished(not was_stopped, msg)

    def _human_delay(self, config: TypingConfig, ch: str, is_error: bool = False) -> None:
        base = config.base_interval_ms / 1000.0
        sigma = config.jitter_sigma_ms / 1000.0
        delay = max(0.008, random.gauss(base, sigma))

        if config.syntax_pause_enabled and not is_error:
            if ch in (";", "{", "}"):
                delay += random.uniform(0.09, 0.24)
            elif ch in (",", ")", "]"):
                delay += random.uniform(0.04, 0.10)

        time.sleep(delay)

    # ── Métodos Públicos (Thread-safe) ────────
    def launch(self, profile_dir: str) -> tuple[bool, str]:
        if not PLAYWRIGHT_AVAILABLE:
            return False, "Playwright no está instalado. Ejecuta: pip install playwright && playwright install chromium"
        q = queue.Queue()
        self._cmd_queue.put(("LAUNCH", profile_dir, q))
        try:
            return q.get(timeout=30)
        except Exception as e:
            return False, f"Timeout esperando apertura del navegador: {e}"

    def connect_cdp(self, cdp_url: str = "http://localhost:9222") -> tuple[bool, str]:
        if not PLAYWRIGHT_AVAILABLE:
            return False, "Playwright no está instalado. Ejecuta: pip install playwright"
        q = queue.Queue()
        self._cmd_queue.put(("CONNECT_CDP", cdp_url, q))
        try:
            return q.get(timeout=10)
        except Exception as e:
            return False, f"Timeout conectando a CDP: {e}"

    def get_tabs(self) -> tuple[bool, list[dict]]:
        if not PLAYWRIGHT_AVAILABLE:
            return False, []
        q = queue.Queue()
        self._cmd_queue.put(("GET_TABS", None, q))
        try:
            return q.get(timeout=5)
        except Exception:
            return False, []

    def select_tab(self, idx: int) -> tuple[bool, str]:
        if not PLAYWRIGHT_AVAILABLE:
            return False, "Playwright no instalado"
        q = queue.Queue()
        self._cmd_queue.put(("SELECT_TAB", idx, q))
        try:
            return q.get(timeout=5)
        except Exception as e:
            return False, str(e)

    def detect_editor(self, custom_selector: str = "") -> tuple[bool, dict]:
        if not PLAYWRIGHT_AVAILABLE:
            return False, {"type": "Playwright no instalado", "ok": False}
        q = queue.Queue()
        self._cmd_queue.put(("DETECT_EDITOR", custom_selector, q))
        try:
            return q.get(timeout=8)
        except Exception as e:
            return False, {"type": f"Error detectando editor: {e}", "ok": False}

    def start_typing(
        self,
        text: str,
        config: TypingConfig,
        countdown: int,
        on_prog: Callable[[int, int], None],
        on_finish: Callable[[bool, str], None],
    ) -> tuple[bool, str]:
        if not PLAYWRIGHT_AVAILABLE:
            return False, "Playwright no instalado"
        q = queue.Queue()
        self._cmd_queue.put(("START_TYPING", (text, config, countdown, on_prog, on_finish), q))
        try:
            return q.get(timeout=10)
        except Exception as e:
            return False, str(e)

    def stop_typing(self) -> None:
        self._stop_event.set()

    def close(self) -> tuple[bool, str]:
        if not PLAYWRIGHT_AVAILABLE:
            return True, "Cerrado"
        q = queue.Queue()
        self._cmd_queue.put(("CLOSE", None, q))
        try:
            return q.get(timeout=8)
        except Exception as e:
            return False, str(e)

    def quit(self) -> None:
        self.stop_typing()
        if self._thread and self._thread.is_alive():
            q = queue.Queue()
            self._cmd_queue.put(("QUIT", None, q))
            try:
                q.get(timeout=2.0)
            except Exception:
                pass


# ──────────────────────────────────────────────
#  Widget de Editor de Código con Canaleta (Gutter)
# ──────────────────────────────────────────────
class VisualCodeEditor(ctk.CTkFrame):
    """
    Editor de código profesional estilo NetBeans / VS Code:
    - Canaleta izquierda con números de línea y marcas de modificación '~'.
    - Detección precisa de líneas modificadas mediante difflib.
    - Resaltado de sintaxis en tiempo real para C++ / Java.
    - Fuente monoespaciada Consolas/Cascadia Code.
    """

    NAVIGATION_KEYS = {
        "Up", "Down", "Left", "Right", "Home", "End", "Prior", "Next",
        "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
        "Caps_Lock", "Num_Lock", "Scroll_Lock", "Escape"
    }

    def __init__(self, parent, font_family="Consolas", font_size=11, **kwargs):
        super().__init__(parent, **kwargs)
        self.font_family = font_family
        self.font_size = font_size
        self._highlight_timer: Optional[str] = None
        self._on_change_callbacks: list[Callable[[], None]] = []

        self.baseline_lines: list[str] = []
        self.modified_lines: set[int] = set()
        self._is_internal_update: bool = False

        self._build_editor()

    def _build_editor(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        editor_font = (self.font_family, self.font_size)

        # Canaleta izquierda para números de línea y marcas '~'
        self.gutter = tk.Text(
            self,
            width=5,
            padx=4,
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

        # Scrollbars
        self.vsb = ctk.CTkScrollbar(self, orientation="vertical", command=self._on_scrollbar_move)
        self.vsb.grid(row=0, column=2, sticky="ns")

        self.hsb = ctk.CTkScrollbar(self, orientation="horizontal", command=self.text.xview)
        self.hsb.grid(row=1, column=1, sticky="ew")

        self.text.config(
            yscrollcommand=self._on_text_vscroll,
            xscrollcommand=self.hsb.set,
        )

        self.gutter.bind(
            "<MouseWheel>",
            lambda e: self.text.yview_scroll(int(-1 * (e.delta / 120)), "units"),
        )

        self.text.bind("<<Modified>>", self._on_text_modified)
        self.text.bind("<KeyRelease>", self._on_key_release)
        self.text.bind("<Button-1>", lambda _: self.after(10, self._update_gutter))

        self._configure_syntax_tags()
        self._update_gutter()

    def _configure_syntax_tags(self) -> None:
        font_regular = (self.font_family, self.font_size)
        font_italic = (self.font_family, self.font_size, "italic")
        font_bold = (self.font_family, self.font_size, "bold")

        self.text.tag_config("keyword", foreground="#c678dd", font=font_bold)
        self.text.tag_config("type", foreground="#e5c07b")
        self.text.tag_config("preproc", foreground="#e06c75")
        self.text.tag_config("constant", foreground="#d19a66")
        self.text.tag_config("string", foreground="#98c379")
        self.text.tag_config("comment", foreground="#71717a", font=font_italic)

        # Fondo sutil para líneas modificadas
        self.text.tag_config("modified_line", background="#242118")

        self.gutter.tag_config(
            "gutter_modified",
            foreground="#fbbf24",
            background="#2a2415",
            font=font_bold,
        )
        self.gutter.tag_config(
            "gutter_normal",
            foreground="#52525b",
            background="#121215",
            font=font_regular,
        )

    def _on_text_vscroll(self, *args) -> None:
        self.gutter.yview_moveto(args[0])
        self.vsb.set(*args)

    def _on_scrollbar_move(self, *args) -> None:
        self.text.yview(*args)
        self.gutter.yview(*args)

    def _on_key_release(self, event=None) -> None:
        # Ignorar teclas puramente de navegación para no recalcular innecesariamente
        if event and event.keysym in self.NAVIGATION_KEYS:
            return
        self._recalculate_diff()
        self._update_gutter()
        self._schedule_highlight()

    def _on_text_modified(self, event=None) -> None:
        self.text.edit_modified(False)
        if self._is_internal_update:
            return

        current_text = self.get_text()
        if not current_text.strip():
            self.baseline_lines.clear()
            self.modified_lines.clear()
        else:
            self._recalculate_diff()

        self._update_gutter()
        self._schedule_highlight()
        for cb in self._on_change_callbacks:
            cb()

    def _recalculate_diff(self) -> None:
        current_lines = self.get_text().splitlines()
        if not self.baseline_lines:
            if current_lines:
                self.baseline_lines = list(current_lines)
            self.modified_lines.clear()
            return

        matcher = difflib.SequenceMatcher(None, self.baseline_lines, current_lines)
        mods = set()
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ("replace", "insert"):
                for line_no in range(j1 + 1, j2 + 1):
                    mods.add(line_no)
        self.modified_lines = mods

    def reset_baseline(self) -> None:
        self.baseline_lines = self.get_text().splitlines()
        self.modified_lines.clear()
        self._update_gutter()
        for cb in self._on_change_callbacks:
            cb()

    def _schedule_highlight(self) -> None:
        if self._highlight_timer:
            self.after_cancel(self._highlight_timer)
        self._highlight_timer = self.after(120, self.highlight_syntax)

    def _update_gutter(self) -> None:
        line_count = int(self.text.index("end-1c").split(".")[0])
        gutter_width = max(5, len(str(line_count)) + 3)
        self.gutter.config(width=gutter_width)

        self.gutter.config(state="normal")
        self.gutter.delete("1.0", "end")

        # Limpiar tags previos
        self.text.tag_remove("modified_line", "1.0", "end")

        for line_no in range(1, line_count + 1):
            is_mod = line_no in self.modified_lines
            line_str = str(line_no)
            if is_mod:
                marker_text = f"~{line_str:>{gutter_width - 2}}\n"
                self.gutter.insert("end", marker_text, "gutter_modified")
            else:
                marker_text = f" {line_str:>{gutter_width - 2}}\n"
                self.gutter.insert("end", marker_text, "gutter_normal")

        self.gutter.config(state="disabled")

        # Aplicar el tag de fondo solo a las líneas que fueron modificadas (optimización de rendimiento)
        for line_no in self.modified_lines:
            if 1 <= line_no <= line_count:
                self.text.tag_add("modified_line", f"{line_no}.0", f"{line_no}.end + 1c")

        self.text.tag_lower("modified_line")

        y_pos = self.text.yview()[0]
        self.gutter.yview_moveto(y_pos)

    def highlight_syntax(self) -> None:
        content = self.text.get("1.0", "end-1c")
        if not content:
            return

        for tag in SYNTAX_PATTERNS.keys():
            self.text.tag_remove(tag, "1.0", "end")

        def apply_regex(pattern: str, tag: str, flags: int = 0) -> None:
            for match in re.finditer(pattern, content, flags):
                start = f"1.0 + {match.start()} chars"
                end = f"1.0 + {match.end()} chars"
                self.text.tag_add(tag, start, end)

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

    def set_text(self, new_text: str, update_baseline: bool = False) -> None:
        self._is_internal_update = True
        try:
            self.text.delete("1.0", "end")
            self.text.insert("1.0", new_text)
        finally:
            self._is_internal_update = False

        if update_baseline or not self.baseline_lines:
            self.baseline_lines = new_text.splitlines()
            self.modified_lines.clear()
        else:
            self._recalculate_diff()

        self._update_gutter()
        self.highlight_syntax()
        for cb in self._on_change_callbacks:
            cb()


# ──────────────────────────────────────────────
#  Interfaz de Usuario Principal (CustomTkinter)
# ──────────────────────────────────────────────
class HumanTyperApp(ctk.CTk):
    COUNTDOWN_SECS_NATIVE = 5
    COUNTDOWN_SECS_PLAYWRIGHT = 2

    def __init__(self) -> None:
        super().__init__()
        self.title("Human Typer  ·  Playwright Web Suite & IDE Typer")
        self.geometry("980x920")
        self.minsize(880, 800)

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.config = TypingConfig()

        # Motor de tipeo nativo (OS pynput)
        self.native_engine = HumanTypingEngine(self.config)
        self.native_engine.set_callbacks(self._on_progress_async, self._on_native_finished_async)

        # Trabajador Playwright en hilo independiente
        self.pw_worker = PlaywrightWorker()
        self.browser_profile_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), ".browser_profile")
        )

        # Kill switch global via pynput listener
        self._kb_listener = pynput_keyboard.Listener(on_press=self._global_key_press)
        self._kb_listener.start()

        self._open_tabs_cache: list[dict] = []

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
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(10, 2))
        ctk.CTkLabel(
            header,
            text="🖮  Human Typer",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text="Simulador de cadencia humana con soporte Playwright (segundo plano) y teclado OS",
            font=ctk.CTkFont(size=12),
            text_color="gray65",
        ).pack(side="left", padx=14)

        # ── 2. Editor de Código Visual Estilo IDE ──
        editor_container = ctk.CTkFrame(self)
        editor_container.grid(row=1, column=0, sticky="nsew", padx=18, pady=4)
        editor_container.grid_columnconfigure(0, weight=1)
        editor_container.grid_rowconfigure(1, weight=1)

        # Toolbar superior del editor
        toolbar = ctk.CTkFrame(editor_container, fg_color="transparent", height=32)
        toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 2))
        toolbar.grid_columnconfigure(1, weight=1)

        left_sub = ctk.CTkFrame(toolbar, fg_color="transparent")
        left_sub.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            left_sub,
            text="🗎 Código Fuente (C++ / Java):",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="gray85",
        ).pack(side="left", padx=(0, 10))

        self.lbl_modified_info = ctk.CTkLabel(
            left_sub,
            text="✓ Sin modificaciones",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#71717a",
        )
        self.lbl_modified_info.pack(side="left", padx=(0, 8))

        self.btn_reset_marks = ctk.CTkButton(
            left_sub,
            text="📌 Fijar base",
            font=ctk.CTkFont(size=11),
            height=26,
            width=90,
            command=self._on_reset_marks,
            fg_color="#27272a",
            hover_color="#3f3f46",
            state="disabled",
        )
        self.btn_reset_marks.pack(side="left")

        right_sub = ctk.CTkFrame(toolbar, fg_color="transparent")
        right_sub.grid(row=0, column=1, sticky="e")

        self.lbl_format_badge = ctk.CTkLabel(
            right_sub,
            text="",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#4ade80",
        )
        self.lbl_format_badge.pack(side="left", padx=(0, 10))

        self.btn_format = ctk.CTkButton(
            right_sub,
            text="✨ Formatear Código (Alt+Shift+F)",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=28,
            width=210,
            command=self._on_format_code,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
        )
        self.btn_format.pack(side="left")

        self.editor = VisualCodeEditor(editor_container)
        self.editor.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 2))
        self.editor.add_on_change_callback(self._update_text_stats)

        self.lbl_stats = ctk.CTkLabel(
            editor_container,
            text="0 caracteres · 0 palabras · 0 líneas",
            font=ctk.CTkFont(size=11),
            text_color="gray55",
        )
        self.lbl_stats.grid(row=2, column=0, sticky="e", padx=10, pady=(1, 3))

        # ── 3. Panel: Modo de Inyección & Playwright ──
        mode_card = ctk.CTkFrame(self)
        mode_card.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 4))
        mode_card.grid_columnconfigure(0, weight=1)

        top_mode_row = ctk.CTkFrame(mode_card, fg_color="transparent")
        top_mode_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(6, 4))
        top_mode_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            top_mode_row,
            text="Modo de Inyección:",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left", padx=(0, 10))

        mode_options = []
        if PLAYWRIGHT_AVAILABLE:
            mode_options.append("🌐 Playwright (Segundo plano · No bloquea)")
        mode_options.append("⌨ Teclado Nativo OS (pynput)")

        self.seg_mode = ctk.CTkSegmentedButton(
            top_mode_row,
            values=mode_options,
            command=self._on_mode_change,
            font=ctk.CTkFont(size=12, weight="bold"),
            selected_color="#2563eb",
        )
        self.seg_mode.pack(side="left")
        default_mode = "🌐 Playwright (Segundo plano · No bloquea)" if PLAYWRIGHT_AVAILABLE else "⌨ Teclado Nativo OS (pynput)"
        self.seg_mode.set(default_mode)

        # Sub-panel de controles Playwright
        self.pw_frame = ctk.CTkFrame(mode_card, fg_color="#18181b", corner_radius=6)
        if PLAYWRIGHT_AVAILABLE and self.config.injection_mode == "playwright":
            self.pw_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(2, 8))
        self.pw_frame.grid_columnconfigure(1, weight=1)

        pw_row1 = ctk.CTkFrame(self.pw_frame, fg_color="transparent")
        pw_row1.pack(fill="x", padx=10, pady=(6, 3))

        self.btn_pw_launch = ctk.CTkButton(
            pw_row1,
            text="🌐 Abrir Navegador Playwright",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=28,
            width=200,
            command=self._on_toggle_playwright_browser,
            fg_color="#0284c7",
            hover_color="#0369a1",
        )
        self.btn_pw_launch.pack(side="left", padx=(0, 8))

        self.btn_pw_cdp = ctk.CTkButton(
            pw_row1,
            text="🔗 Conectar a Chrome existente (CDP 9222)",
            font=ctk.CTkFont(size=11),
            height=28,
            width=230,
            command=self._on_connect_cdp,
            fg_color="#27272a",
            hover_color="#3f3f46",
        )
        self.btn_pw_cdp.pack(side="left", padx=(0, 12))

        self.lbl_pw_status = ctk.CTkLabel(
            pw_row1,
            text="⚪ Navegador desconectado",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#a1a1aa",
        )
        self.lbl_pw_status.pack(side="left")

        pw_row2 = ctk.CTkFrame(self.pw_frame, fg_color="transparent")
        pw_row2.pack(fill="x", padx=10, pady=(2, 4))

        ctk.CTkLabel(pw_row2, text="Pestaña destino:", font=ctk.CTkFont(size=11, weight="bold")).pack(
            side="left", padx=(0, 6)
        )

        self.cb_pw_tabs = ctk.CTkComboBox(
            pw_row2,
            values=["(Abre o conecta el navegador primero)"],
            command=self._on_tab_selected,
            width=290,
            font=ctk.CTkFont(size=11),
        )
        self.cb_pw_tabs.pack(side="left", padx=(0, 8))

        self.btn_pw_refresh_tabs = ctk.CTkButton(
            pw_row2,
            text="🔄 Refrescar",
            font=ctk.CTkFont(size=11),
            height=26,
            width=85,
            command=self._refresh_playwright_tabs,
            fg_color="#27272a",
            hover_color="#3f3f46",
        )
        self.btn_pw_refresh_tabs.pack(side="left", padx=(0, 8))

        self.btn_pw_detect = ctk.CTkButton(
            pw_row2,
            text="🎯 Detectar Editor",
            font=ctk.CTkFont(size=11, weight="bold"),
            height=26,
            width=120,
            command=self._on_detect_editor,
            fg_color="#3b82f6",
            hover_color="#2563eb",
        )
        self.btn_pw_detect.pack(side="left", padx=(0, 10))

        self.lbl_pw_editor_info = ctk.CTkLabel(
            pw_row2,
            text="Editor: (no verificado)",
            font=ctk.CTkFont(size=11),
            text_color="#71717a",
        )
        self.lbl_pw_editor_info.pack(side="left")

        pw_row3 = ctk.CTkFrame(self.pw_frame, fg_color="transparent")
        pw_row3.pack(fill="x", padx=10, pady=(2, 6))

        self.chk_pw_clear = ctk.CTkCheckBox(
            pw_row3,
            text="Limpiar editor antes de tipear (Ctrl+A + Supr)",
            font=ctk.CTkFont(size=11),
            command=self._sync_config,
        )
        self.chk_pw_clear.pack(side="left", padx=(0, 14))

        self.lbl_pw_tip = ctk.CTkLabel(
            pw_row3,
            text="💡 Puedes minimizar el navegador o trabajar en otras aplicaciones; el tipeo continúa de forma autónoma.",
            font=ctk.CTkFont(size=11),
            text_color="#38bdf8",
        )
        self.lbl_pw_tip.pack(side="left")

        self.lbl_native_info = ctk.CTkLabel(
            mode_card,
            text="ℹ Modo Teclado Nativo: Al iniciar dispones de 5s para enfocar la ventana destino. Envía teclas físicas globales al sistema.",
            font=ctk.CTkFont(size=11),
            text_color="#f59e0b",
        )
        if not PLAYWRIGHT_AVAILABLE or self.config.injection_mode == "native":
            self.lbl_native_info.grid(row=1, column=0, sticky="w", padx=14, pady=(4, 8))

        # ── 4. Panel: Modo Editor de Código Web ──
        code_card = ctk.CTkFrame(self)
        code_card.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 4))
        code_card.grid_columnconfigure((0, 1), weight=1)

        self.sw_code_mode = ctk.CTkSwitch(
            code_card,
            text="Modo Editor de Código Web (Neutralizar auto-cierre y sangría natural)",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._sync_code_mode_state,
        )
        self.sw_code_mode.grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(6, 4))
        self.sw_code_mode.select()

        opts_sub = ctk.CTkFrame(code_card, fg_color="transparent")
        opts_sub.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 6))

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

        # ── 5. Panel: Sliders de Simulación Humana ──
        human_card = ctk.CTkFrame(self)
        human_card.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 4))
        human_card.grid_columnconfigure((0, 1, 2), weight=1)

        self._build_slider(
            human_card, col=0,
            label="⏱ Velocidad base",
            from_=20, to=150, initial=75,
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

        # ── 6. Progreso y Estado ──
        prog_frame = ctk.CTkFrame(self, fg_color="transparent")
        prog_frame.grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 4))
        prog_frame.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(prog_frame, height=10)
        self.progress_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.progress_bar.set(0)

        self.lbl_status = ctk.CTkLabel(
            prog_frame,
            text="Listo · Pega el código, selecciona el modo y pulsa Iniciar tipeo.",
            font=ctk.CTkFont(size=12),
            text_color="gray65",
        )
        self.lbl_status.grid(row=1, column=0, sticky="w")

        # ── 7. Botones de Control y Kill Switch ──
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=6, column=0, sticky="ew", padx=18, pady=(4, 12))

        start_label = "▶  Iniciar tipeo (Playwright)" if self.config.injection_mode == "playwright" else "▶  Iniciar tipeo (5s Nativo)"
        self.btn_start = ctk.CTkButton(
            btn_frame,
            text=start_label,
            font=ctk.CTkFont(size=14, weight="bold"),
            width=220,
            height=42,
            command=self._start_execution,
            fg_color="#15803d",
            hover_color="#166534",
        )
        self.btn_start.pack(side="left", padx=(0, 12))

        self.btn_stop = ctk.CTkButton(
            btn_frame,
            text="■  Detener",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=180,
            height=42,
            command=self._stop_execution,
            fg_color="#b91c1c",
            hover_color="#991b1b",
            state="disabled",
        )
        self.btn_stop.pack(side="left", padx=(0, 14))

        ctk.CTkLabel(
            btn_frame,
            text="🚨 Parada de emergencia: Botón Detener o tecla Esc (detiene ambos modos)",
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
        frame.grid(row=0, column=col, padx=14, pady=6, sticky="ew")

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

    # ── Gestión de Modo de Inyección ──────────
    def _on_mode_change(self, selected_mode: str) -> None:
        if "Playwright" in selected_mode:
            self.config.injection_mode = "playwright"
            self.pw_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(2, 8))
            self.lbl_native_info.grid_forget()
            self.btn_start.configure(text="▶  Iniciar tipeo (Playwright)")
        else:
            self.config.injection_mode = "native"
            self.pw_frame.grid_forget()
            self.lbl_native_info.grid(row=1, column=0, sticky="w", padx=14, pady=(4, 8))
            self.btn_start.configure(text="▶  Iniciar tipeo (5s Nativo)")

    # ── Control de Playwright ─────────────────
    def _on_toggle_playwright_browser(self) -> None:
        if self.pw_worker.is_connected:
            self._set_status("Cerrando navegador Playwright...", "gray65")
            ok, msg = self.pw_worker.close()
            self.lbl_pw_status.configure(text="⚪ Navegador desconectado", text_color="#a1a1aa")
            self.btn_pw_launch.configure(text="🌐 Abrir Navegador Playwright", fg_color="#0284c7")
            self.cb_pw_tabs.configure(values=["(Navegador desconectado)"])
            self.cb_pw_tabs.set("(Navegador desconectado)")
            self.lbl_pw_editor_info.configure(text="Editor: (no verificado)", text_color="#71717a")
            self._set_status("Navegador desconectado.", "gray65")
        else:
            self._set_status("Abriendo navegador con perfil persistente...", "#38bdf8")
            self.btn_pw_launch.configure(state="disabled")
            self.update_idletasks()

            def _thread_launch():
                ok, msg = self.pw_worker.launch(self.browser_profile_dir)
                self.after(0, self._after_pw_launch, ok, msg)

            threading.Thread(target=_thread_launch, daemon=True).start()

    def _after_pw_launch(self, ok: bool, msg: str) -> None:
        self.btn_pw_launch.configure(state="normal")
        if ok:
            mode_desc = self.pw_worker.browser_mode
            self.lbl_pw_status.configure(text=f"🟢 Conectado ({mode_desc})", text_color="#4ade80")
            self.btn_pw_launch.configure(text="❌ Desconectar Navegador", fg_color="#b91c1c")
            self._refresh_playwright_tabs()
            self._set_status(f"✓ Navegador listo. Abre tu problema en el navegador y detecta el editor.", "#4ade80")
        else:
            self.lbl_pw_status.configure(text="🔴 Error al abrir", text_color="#ef4444")
            self._set_status(f"Error al abrir navegador: {msg}", "#ef4444")

    def _on_connect_cdp(self) -> None:
        self._set_status("Conectando a Chrome vía CDP (localhost:9222)...", "#38bdf8")

        def _thread_cdp():
            ok, msg = self.pw_worker.connect_cdp("http://localhost:9222")
            self.after(0, self._after_cdp_connect, ok, msg)

        threading.Thread(target=_thread_cdp, daemon=True).start()

    def _after_cdp_connect(self, ok: bool, msg: str) -> None:
        if ok:
            self.lbl_pw_status.configure(text="🟢 Conectado vía CDP (9222)", text_color="#4ade80")
            self.btn_pw_launch.configure(text="❌ Desconectar Navegador", fg_color="#b91c1c")
            self._refresh_playwright_tabs()
            self._set_status("✓ Conexión CDP establecida con éxito.", "#4ade80")
        else:
            self._set_status(
                "⚠ No se pudo conectar al puerto 9222. Inicia Chrome con: chrome.exe --remote-debugging-port=9222 o usa 'Abrir Navegador'.",
                "orange",
            )

    def _refresh_playwright_tabs(self) -> None:
        if not self.pw_worker.is_connected:
            return

        def _thread_tabs():
            ok, tabs = self.pw_worker.get_tabs()
            self.after(0, self._after_tabs_refreshed, ok, tabs)

        threading.Thread(target=_thread_tabs, daemon=True).start()

    def _after_tabs_refreshed(self, ok: bool, tabs: list[dict]) -> None:
        if not ok or not tabs:
            self.cb_pw_tabs.configure(values=["(Sin pestañas abiertas)"])
            self.cb_pw_tabs.set("(Sin pestañas abiertas)")
            return

        self._open_tabs_cache = tabs
        tab_labels = []
        for t in tabs:
            title_trunc = t["title"][:38] + ("…" if len(t["title"]) > 38 else "")
            tab_labels.append(f"[{t['index']}] {title_trunc}")

        self.cb_pw_tabs.configure(values=tab_labels)
        if tab_labels:
            cur = self.cb_pw_tabs.get()
            if cur not in tab_labels:
                self.cb_pw_tabs.set(tab_labels[0])
                self.pw_worker.select_tab(0)

    def _on_tab_selected(self, val: str) -> None:
        match = re.match(r"^\[(\d+)\]", val)
        if match:
            idx = int(match.group(1))
            self.pw_worker.select_tab(idx)
            self._on_detect_editor()

    def _on_detect_editor(self) -> None:
        if not self.pw_worker.is_connected:
            self._set_status("⚠ Abre o conecta el navegador primero.", "orange")
            return

        def _thread_detect():
            ok, info = self.pw_worker.detect_editor(self.config.custom_selector)
            self.after(0, self._after_editor_detected, ok, info)

        threading.Thread(target=_thread_detect, daemon=True).start()

    def _after_editor_detected(self, ok: bool, info: dict) -> None:
        if ok and info.get("ok"):
            ed_type = info.get("type", "Editor web")
            self.lbl_pw_editor_info.configure(
                text=f"✓ {ed_type} detectado y enfocado",
                text_color="#4ade80",
            )
            self._set_status(f"🎯 {ed_type} listo para recibir tipeo.", "#4ade80")
        else:
            err_type = info.get("type", "No detectado") if isinstance(info, dict) else str(info)
            self.lbl_pw_editor_info.configure(
                text=f"⚠ {err_type}",
                text_color="#fbbf24",
            )
            self._set_status(
                "⚠ Haz clic dentro del editor de código en la página web para enfocarlo antes de tipear.",
                "orange",
            )

    # ── Formatear Código (Format Code) ────────
    def _on_format_code(self) -> None:
        raw_code = self.editor.get_text()
        if not raw_code.strip():
            self._set_status("⚠ El editor está vacío; no hay código para formatear.", "orange")
            return

        formatted, engine_name = format_code(raw_code)
        self.editor.set_text(formatted, update_baseline=False)

        badge_msg = f"✓ Formateado ({engine_name})"
        self.lbl_format_badge.configure(text=badge_msg)
        self._set_status(f"✨ Formateo completado mediante {engine_name}.", "#4ade80")
        self.after(3500, lambda: self.lbl_format_badge.configure(text=""))

    def _on_reset_marks(self) -> None:
        self.editor.reset_baseline()
        self._set_status("📌 Base fijada: marcas de modificación restablecidas.", "#38bdf8")

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
        self.config.clear_before_typing = bool(self.chk_pw_clear.get())

    def _update_text_stats(self) -> None:
        content = self.editor.get_text()
        chars = len(content)
        words = len(content.split())
        lines = len(content.splitlines()) if content else 0
        mods = self.editor.modified_lines
        mod_count = len(mods)

        if mod_count > 0:
            lines_preview = ", ".join(str(l) for l in sorted(mods)[:6])
            if mod_count > 6:
                lines_preview += f"... (+{mod_count - 6})"
            self.lbl_modified_info.configure(
                text=f"✎ {mod_count} línea(s) tocada(s): [{lines_preview}]",
                text_color="#fbbf24",
            )
            self.btn_reset_marks.configure(state="normal")
        else:
            self.lbl_modified_info.configure(
                text="✓ Sin modificaciones",
                text_color="#71717a",
            )
            self.btn_reset_marks.configure(state="disabled")

        mod_suffix = f" · {mod_count} tocadas" if mod_count > 0 else ""
        self.lbl_stats.configure(
            text=f"{chars} caracteres · {words} palabras · {lines} líneas{mod_suffix}"
        )

    # ── Flujo de Ejecución (Iniciar / Detener) ─
    def _start_execution(self) -> None:
        code_to_type = self.editor.get_text().rstrip("\r\n")
        if not code_to_type.strip():
            self._set_status("⚠ El área de texto está vacía. Pega código primero.", "orange")
            return

        if self.native_engine.running or self.pw_worker.running_typing:
            return

        self._sync_config()

        # ── Modo Playwright ──
        if self.config.injection_mode == "playwright":
            if not self.pw_worker.is_connected:
                self._set_status("⚠ El navegador Playwright no está conectado. Abriendo ahora...", "#f59e0b")
                ok, msg = self.pw_worker.launch(self.browser_profile_dir)
                if not ok:
                    self._set_status(f"Error al abrir navegador: {msg}", "#ef4444")
                    return
                self._after_pw_launch(True, msg)

            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            self.progress_bar.set(0)
            self._set_status(
                f"⏳ Iniciando en {self.COUNTDOWN_SECS_PLAYWRIGHT}s (Playwright en segundo plano)...",
                "#38bdf8",
            )

            threading.Thread(
                target=lambda: self.pw_worker.start_typing(
                    code_to_type,
                    self.config,
                    self.COUNTDOWN_SECS_PLAYWRIGHT,
                    self._on_progress_async,
                    self._on_playwright_finished_async,
                ),
                daemon=True,
            ).start()

        # ── Modo Teclado Nativo OS (pynput) ──
        else:
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            self.progress_bar.set(0)
            self._set_status(
                f"⏳ Iniciando en {self.COUNTDOWN_SECS_NATIVE}s — Haz clic en el editor destino…",
                "#f59e0b",
            )
            self.native_engine.start(code_to_type, countdown_sec=self.COUNTDOWN_SECS_NATIVE)

    def _stop_execution(self) -> None:
        if self.pw_worker.running_typing:
            self.pw_worker.stop_typing()
        if self.native_engine.running:
            self.native_engine.stop()

        self._set_status("■ Detención solicitada.", "#ef4444")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    # ── Callbacks Asíncronos ──────────────────
    def _on_progress_async(self, current: int, total: int) -> None:
        self.after(0, self._update_progress_ui, current, total)

    def _update_progress_ui(self, current: int, total: int) -> None:
        if current <= 0:
            remaining = abs(current)
            if self.config.injection_mode == "playwright":
                self._set_status(
                    f"⏳ Iniciando en {remaining}s (Inyección Playwright autónoma en segundo plano)...",
                    "#38bdf8",
                )
            else:
                self._set_status(
                    f"⏳ Iniciando en {remaining}s — ¡Enfoca la ventana destino!",
                    "#f59e0b",
                )
            self.progress_bar.set(0)
        else:
            ratio = current / total if total > 0 else 0
            self.progress_bar.set(ratio)
            pct = ratio * 100
            mode_tag = "🌐 Playwright" if self.config.injection_mode == "playwright" else "⌨ Nativo"
            self._set_status(
                f"{mode_tag} · Tipeando… {current}/{total} pasos ({pct:.1f}%)",
                "#4ade80",
            )

    def _on_playwright_finished_async(self, success: bool, message: str) -> None:
        self.after(0, self._on_playwright_finished_ui, success, message)

    def _on_playwright_finished_ui(self, success: bool, message: str) -> None:
        color = "#4ade80" if success else "#ef4444"
        if success:
            self.progress_bar.set(1.0)
        self._set_status(f"✓ {message}", color)
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def _on_native_finished_async(self) -> None:
        self.after(0, self._on_native_finished_ui)

    def _on_native_finished_ui(self) -> None:
        self.progress_bar.set(1.0)
        self._set_status("✅ Tipeo nativo finalizado con éxito.", "#4ade80")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    # ── Kill Switch Global (Esc) ──────────────
    def _global_key_press(self, key) -> None:
        if key == pynput_keyboard.Key.esc:
            # Ignorar Esc programático generado por el driver nativo para cerrar autocomplete
            if getattr(self.native_engine.driver, "is_sending_programmatic_esc", False):
                return

            # Esc detiene la ejecución tanto en modo nativo como en modo Playwright
            if self.native_engine.running or self.pw_worker.running_typing:
                self._stop_execution()
                self.after(0, self._on_emergency_stop_ui)

    def _on_emergency_stop_ui(self) -> None:
        self._set_status("🛑 DETENIDO: Parada de emergencia activada [Esc].", "#ef4444")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def _set_status(self, text: str, color: str = "gray65") -> None:
        self.lbl_status.configure(text=text, text_color=color)

    # ── Cierre Limpio de la Aplicación ───────
    def on_close(self) -> None:
        self.native_engine.stop()
        self.pw_worker.quit()
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
