"""Lightweight desktop dashboard for BOTPYTON.

Standard-library Tk UI. It starts only when launched, runs one task at a time,
and delegates work to the existing deterministic local handlers.
"""
from __future__ import annotations

import json
import threading
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

BG = "#F3F6FA"
WHITE = "#FFFFFF"
NAVY = "#111D31"
NAVY_HOVER = "#20304A"
INK = "#172438"
MUTED = "#65748A"
LINE = "#DEE6F0"
TEAL = "#078D83"
TEAL_DARK = "#08766F"
BLUE = "#2F69C8"
SOFT_BLUE = "#EAF1FC"
SOFT_TEAL = "#E5F6F3"
SOFT_AMBER = "#FFF4DF"
RED = "#B83B45"

STATUS_LABELS = {
    "collected": "Listo", "completed": "Completado",
    "measured": "Medición completada", "scan_complete": "Análisis completo",
    "preview_complete": "Vista previa lista", "restore_preview_complete": "Restauración revisada",
    "restored": "Restaurado", "cache_quarantined": "Cuarentena creada",
    "cache_restored": "Caché restaurada", "no_candidates": "No se encontraron elementos",
    "no_cache_candidates": "No se encontraron archivos de caché elegibles",
    "not_present": "No encontrado", "unsupported_platform": "No disponible en este sistema",
    "preview_partial": "Análisis parcial", "scan_partial": "Análisis parcial",
    "partial": "Resultado parcial", "partial_limit": "Límite alcanzado",
    "partial_measurement": "Medición parcial", "cache_quarantine_blocked": "No se hicieron cambios",
    "cache_restore_blocked": "Restauración bloqueada", "confirmation_mismatch": "Confirmación no válida",
    "timeout": "Tiempo límite alcanzado", "unavailable": "No disponible",
    "blocked": "Bloqueado", "error": "Error", "integrity_error": "Error de integridad",
    "not_started": "No iniciado", "ready_to_restore": "Listo para restaurar",
    "diagnostic_only": "Diagnóstico completado", "query_error": "La consulta falló",
}
FIELD_LABELS = {
    "operation": "Operación", "status": "Estado", "note": "Detalle", "message": "Mensaje",
    "tests_run": "Pruebas ejecutadas", "failures": "Pruebas fallidas", "errors": "Errores de prueba",
    "recommendation": "Recomendación", "next_action": "Siguiente paso",
    "candidate_count": "Archivos encontrados", "candidate_files": "Archivos encontrados",
    "candidate_bytes": "Tamaño estimado", "candidate_mib": "Tamaño estimado (MiB)",
    "items_count": "Elementos encontrados", "items_returned": "Elementos mostrados",
    "entries_examined": "Elementos revisados", "files_examined": "Archivos revisados",
    "directories_examined": "Carpetas revisadas", "profiles_examined": "Perfiles revisados",
    "cache_roots_examined": "Cachés revisadas", "ready_count": "Listos para restaurar",
    "files_quarantined": "Archivos en cuarentena", "files_restored": "Archivos restaurados",
    "files_moved": "Archivos movidos", "files_skipped": "Elementos omitidos",
    "files_deleted": "Archivos eliminados", "free_gib": "Espacio libre",
    "free_percent": "Disco libre (%)", "available_memory_mb": "RAM disponible",
    "total_memory_mb": "RAM instalada", "physical_available_mb": "RAM física disponible",
    "scan_limited": "Análisis limitado", "access_errors": "Errores de acceso",
    "ai_enabled": "IA activada", "external_ai_apis_enabled": "APIs de IA externas",
    "network_used": "Red utilizada", "network_access_requested": "Consulta de red solicitada",
    "settings_changed": "Configuración modificada", "software_changes_made": "Programas modificados",
    "drivers_updated": "Controladores actualizados", "integrity_status": "Integridad del informe",
    "manifest_path": "Archivo de recuperación", "root_path": "Carpeta analizada",
}
LIST_KEYS = {"items", "candidates", "findings", "scans", "largest_files", "duplicate_groups"}
HIDDEN_KEYS = {
    "product", "schema_version", "operation_assessment", "limitations", "checks",
    "sha256", "manifest_sha256", "items", "candidates", "findings", "scans",
    "roots", "measurements", "operation_summaries", "physical_disks", "windows_disks",
    "drivers", "largest_files", "extensions", "duplicate_groups", "output_lines",
    "raw_result",
}


def format_bytes(value: object) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return str(value)
    size = max(0.0, float(value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size):,} B" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"


def _display_value(key: str, value: object) -> str:
    if key.endswith("_bytes") or key in {"candidate_bytes", "current_rss_bytes", "peak_rss_bytes"}:
        return format_bytes(value)
    if key.endswith("_mib") and isinstance(value, (int, float)):
        return f"{value:,.1f} MiB"
    if key.endswith("_mb") and isinstance(value, (int, float)):
        return f"{value:,.0f} MB"
    if key == "free_percent" and isinstance(value, (int, float)):
        return f"{value:.1f}%"
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value)


def format_report(raw: str, exit_code: int = 0, *, technical: bool = False) -> str:
    """Present a compact human-readable summary of a structured CLI report."""
    raw = raw.strip()
    if not raw:
        return "La operación terminó sin un informe de texto."
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw[-12000:]
    if technical:
        return json.dumps(data, ensure_ascii=False, indent=2)[:24000]
    if isinstance(data, list):
        lines = [f"Se devolvieron {len(data)} registros."]
        for item in data[:16]:
            if isinstance(item, dict):
                title = item.get("id") or item.get("name") or item.get("task_id") or "Elemento"
                status = item.get("status") or item.get("queue_status") or ""
                lines.append(f"• {title}" + (f" — {status}" if status else ""))
        if len(data) > 16:
            lines.append(f"Hay {len(data) - 16} registros más en el informe técnico.")
        return "\n".join(lines)
    if not isinstance(data, dict):
        return str(data)

    status = data.get("status")
    assessment = data.get("operation_assessment")
    if not isinstance(status, str) and isinstance(assessment, dict):
        status = assessment.get("status")
    lines = [STATUS_LABELS.get(status, status.replace("_", " ").capitalize()) if isinstance(status, str)
             else ("Operación completada" if exit_code == 0 else "Resultado de la operación")]
    if exit_code == 2:
        lines.append("Resultado parcial o limitado. No se considera un análisis completo.")
    elif exit_code not in (0, 2):
        lines.append("La operación terminó con un error. Revisá el detalle antes de repetirla.")
    lines.append("")

    priority = (
        "operation", "candidate_count", "candidate_files", "candidate_bytes", "candidate_mib",
        "items_count", "entries_examined", "files_examined", "directories_examined",
        "profiles_examined", "cache_roots_examined", "ready_count", "files_quarantined",
        "files_restored", "files_moved", "files_skipped", "files_deleted", "free_gib",
        "free_percent", "available_memory_mb", "total_memory_mb", "physical_available_mb",
        "integrity_status", "scan_limited", "access_errors", "manifest_path", "root_path",
        "ai_enabled", "external_ai_apis_enabled", "network_used", "settings_changed",
        "software_changes_made", "drivers_updated", "network_access_requested",
        "tests_run", "failures", "errors",
    )
    for key in priority:
        if key not in data:
            continue
        value = _display_value(key, data[key])
        if value:
            lines.append(f"{FIELD_LABELS.get(key, key.replace('_', ' ').capitalize())}: {value}")
    for key in ("note", "message", "recommendation", "next_action"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            lines.extend(["", f"{FIELD_LABELS.get(key, key.capitalize())}: {value.strip()}"])

    for key in ("findings", "items", "candidates", "scans", "largest_files", "duplicate_groups"):
        values = data.get(key)
        if not isinstance(values, list) or not values:
            continue
        if key == "findings":
            lines.extend(["", "Puntos a revisar:"])
            for item in values[:7]:
                if isinstance(item, dict):
                    label = item.get("title") or item.get("name") or "Aviso"
                    detail = item.get("detail") or item.get("recommendation") or ""
                    lines.append(f"• {label}" + (f": {detail}" if detail else ""))
        elif key == "scans":
            lines.extend(["", "Carpetas revisadas:"])
            for item in values[:7]:
                if isinstance(item, dict):
                    label = item.get("label") or item.get("location") or "Carpeta"
                    lines.append(f"• {label}: {item.get('candidate_files', 0)} archivos elegibles")
        elif key == "duplicate_groups":
            lines.extend(["", "Posibles duplicados exactos:"])
            for item in values[:7]:
                if isinstance(item, dict):
                    lines.append(f"• {item.get('file_count', 0)} archivos iguales — "
                                 f"{format_bytes(item.get('potential_duplicate_bytes', 0))} potenciales")
        else:
            lines.extend(["", "Elementos encontrados:"])
            for item in values[:7]:
                if not isinstance(item, dict):
                    continue
                label = item.get("name") or item.get("path") or item.get("location") or item.get("browser") or "Elemento"
                suffix = ""
                if isinstance(item.get("size_bytes"), (int, float)):
                    suffix += f" — {format_bytes(item['size_bytes'])}"
                if isinstance(item.get("age_days"), int):
                    suffix += f" — {item['age_days']} días"
                state = item.get("status")
                if isinstance(state, str) and state != "candidate_not_deleted":
                    suffix += f" — {state.replace('_', ' ')}"
                lines.append(f"• {label}{suffix}")
            if len(values) > 7:
                lines.append(f"Hay {len(values) - 7} elementos más; abrí el selector o el informe técnico.")
    if isinstance(assessment, dict) and isinstance(assessment.get("next_action"), str):
        next_action = assessment["next_action"]
        if next_action and next_action not in [str(x) for x in data.values()]:
            lines.extend(["", f"Siguiente paso: {next_action}"])
    return "\n".join(lines).strip()


def _call_main(argv: list[str]) -> tuple[int, str]:
    """Call the existing command dispatcher in-process to avoid a second Python process."""
    from .main import main as bot_main
    buffer = StringIO()
    with redirect_stdout(buffer), redirect_stderr(buffer):
        try:
            code = int(bot_main(argv) or 0)
        except SystemExit as exc:
            code = int(exc.code or 0) if isinstance(exc.code, int) else 1
        except Exception as exc:
            print(f"Error: {type(exc).__name__}: {str(exc)[:500]}")
            code = 1
    return code, buffer.getvalue()


class BotPytonApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("BOTPYTON | Asistente informático")
        self.root.geometry("1180x820")
        self.root.minsize(940, 660)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.busy = False
        self.current_page = "Inicio"
        self.last_raw = ""
        self.last_report: dict | None = None
        self.last_exit_code = 0
        self.technical_view = False
        self.candidate_report: dict | None = None
        self.candidate_kind: str | None = None
        self.candidate_lookup: dict[str, dict] = {}
        self.restore_kind: str | None = None
        self.restore_manifest: str | None = None
        self.restore_ready = 0
        self._pending_restore_manifest: str | None = None
        self.project_path = tk.StringVar(value=str(Path.home()))
        self._build_style()
        self._build_layout()
        self.show_page("Inicio")

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=INK,
                        rowheight=30, borderwidth=0, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#EAF0F6", foreground=INK,
                        font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", "#DCEAFD")], foreground=[("selected", INK)])
        style.configure("BOT.Horizontal.TProgressbar", background=TEAL, troughcolor="#DDE7F0",
                        bordercolor="#DDE7F0", lightcolor=TEAL, darkcolor=TEAL)

    def _build_layout(self) -> None:
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="both", expand=True)
        shell.grid_columnconfigure(1, weight=1)
        shell.grid_rowconfigure(0, weight=1)

        sidebar = tk.Frame(shell, bg=NAVY, width=218)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        brand = tk.Frame(sidebar, bg=NAVY)
        brand.pack(fill="x", padx=20, pady=(23, 28))
        tk.Label(brand, text="◉", bg=NAVY, fg="#5DE0D0",
                 font=("Segoe UI Symbol", 27, "bold")).pack(side="left")
        brand_text = tk.Frame(brand, bg=NAVY)
        brand_text.pack(side="left", padx=(8, 0))
        tk.Label(brand_text, text="BOTPYTON", bg=NAVY, fg=WHITE,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w")
        tk.Label(brand_text, text="Asistente local", bg=NAVY, fg="#9DAFC8",
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))
        tk.Label(sidebar, text="MENÚ PRINCIPAL", bg=NAVY, fg="#8294AE",
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=23, pady=(0, 8))
        self.nav_buttons: dict[str, tk.Button] = {}
        for name, icon in (("Inicio", "⌂"), ("Cuidado del PC", "◈"),
                           ("Proyectos", "▣"), ("Más herramientas", "☷")):
            btn = tk.Button(sidebar, text=f"  {icon}    {name}", anchor="w",
                command=lambda page=name: self.show_page(page), bg=NAVY, fg="#D8E2F0",
                activebackground=NAVY_HOVER, activeforeground=WHITE, relief="flat",
                borderwidth=0, font=("Segoe UI", 10), padx=16, pady=12, cursor="hand2")
            btn.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[name] = btn
        tk.Frame(sidebar, bg=NAVY).pack(fill="both", expand=True)
        safety = tk.Frame(sidebar, bg="#1A2941", padx=14, pady=14)
        safety.pack(fill="x", side="bottom", padx=12, pady=(10, 16))
        tk.Label(safety, text="●  MODO LOCAL", bg="#1A2941", fg="#6BE0C2",
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(safety, text="Sin IA ni servicio en segundo plano.\nLas acciones sensibles piden permiso.",
                 bg="#1A2941", fg="#C0CDDF", justify="left", wraplength=170,
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(8, 0))

        main_area = tk.Frame(shell, bg=BG)
        main_area.grid(row=0, column=1, sticky="nsew")
        main_area.grid_rowconfigure(1, weight=1)
        main_area.grid_columnconfigure(0, weight=1)
        top = tk.Frame(main_area, bg=WHITE, height=66, highlightbackground=LINE, highlightthickness=1)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        tk.Label(top, text="Asistente informático", bg=WHITE, fg=INK,
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=24, pady=(12, 0))
        tk.Label(top, text="Una herramienta a la vez · Tú decides qué cambia",
                 bg=WHITE, fg=MUTED, font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", padx=24, pady=(1, 12))
        self.top_status = tk.Label(top, text="● LISTO", bg=WHITE, fg=TEAL,
                                   font=("Segoe UI", 9, "bold"))
        self.top_status.grid(row=0, column=1, rowspan=2, sticky="e", padx=24)

        body = tk.Frame(main_area, bg=BG)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.page_host = tk.Frame(self.canvas, bg=BG)
        self.page_window = self.canvas.create_window((0, 0), window=self.page_host, anchor="nw")
        self.page_host.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.page_window, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._mousewheel)

        results = tk.Frame(main_area, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        results.grid(row=2, column=0, sticky="ew", padx=16, pady=(7, 13))
        results.grid_columnconfigure(0, weight=1)
        heading = tk.Frame(results, bg=WHITE)
        heading.grid(row=0, column=0, sticky="ew", padx=14, pady=(9, 3))
        tk.Label(heading, text="Resultado", bg=WHITE, fg=INK,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.result_badge = tk.Label(heading, text="Aún sin análisis", bg=SOFT_BLUE, fg=BLUE,
                                     padx=9, pady=3, font=("Segoe UI", 8, "bold"))
        self.result_badge.pack(side="left", padx=10)
        self.detail_button = tk.Button(heading, text="Ver JSON técnico", command=self.toggle_details,
            relief="flat", bd=0, bg=WHITE, fg=BLUE, activebackground=WHITE,
            font=("Segoe UI", 8, "underline"), cursor="hand2")
        self.detail_button.pack(side="right")
        self.progress = ttk.Progressbar(results, mode="indeterminate", style="BOT.Horizontal.TProgressbar")
        self.progress.grid(row=1, column=0, sticky="ew", padx=14, pady=(1, 4))
        self.output = tk.Text(results, height=5, wrap="word", bg=WHITE, fg=INK, bd=0,
            relief="flat", padx=14, pady=5, font=("Segoe UI", 9), state="disabled")
        self.output.grid(row=2, column=0, sticky="ew", padx=(0, 6), pady=(0, 5))
        out_scroll = ttk.Scrollbar(results, orient="vertical", command=self.output.yview)
        self.output.configure(yscrollcommand=out_scroll.set)
        out_scroll.grid(row=2, column=1, sticky="ns", pady=(0, 5), padx=(0, 6))
        self.restore_button = tk.Button(results, text="Restaurar elementos revisados",
            command=self.restore_after_preview, state="disabled", relief="flat", bd=0,
            bg="#E6F5F0", fg=TEAL_DARK, activebackground="#D8F0E8",
            font=("Segoe UI", 9, "bold"), padx=12, pady=7, cursor="hand2")
        self.restore_button.grid(row=3, column=0, sticky="e", padx=14, pady=(0, 9))

    def _mousewheel(self, event) -> None:
        try:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except (tk.TclError, AttributeError):
            pass

    def _set_output(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)
        self.output.configure(state="disabled")

    def show_page(self, name: str) -> None:
        for nav, btn in self.nav_buttons.items():
            active = nav == name
            btn.configure(bg=NAVY_HOVER if active else NAVY, fg=WHITE if active else "#D8E2F0",
                          font=("Segoe UI", 10, "bold" if active else "normal"))
        for child in self.page_host.winfo_children():
            child.destroy()
        if name == "Inicio":
            self._build_home()
        elif name == "Cuidado del PC":
            self._build_pc_page()
        elif name == "Proyectos":
            self._build_projects_page()
        else:
            self._build_more_page()
        self.canvas.yview_moveto(0)

    def _page_intro(self, title: str, subtitle: str) -> None:
        frame = tk.Frame(self.page_host, bg=BG)
        frame.pack(fill="x", padx=24, pady=(19, 13))
        tk.Label(frame, text=title, bg=BG, fg=INK, font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(frame, text=subtitle, bg=BG, fg=MUTED, font=("Segoe UI", 10),
                 wraplength=850, justify="left").pack(anchor="w", pady=(5, 0))

    def _button(self, parent, label: str, command, *, primary: bool = False, small: bool = False) -> tk.Button:
        return tk.Button(parent, text=label, command=command, anchor="center",
            bg=TEAL if primary else SOFT_BLUE, fg=WHITE if primary else BLUE,
            activebackground=TEAL_DARK if primary else "#DCE8F8",
            activeforeground=WHITE if primary else BLUE, relief="flat", bd=0,
            padx=16 if not small else 10, pady=10 if not small else 7,
            font=("Segoe UI", 9, "bold"), cursor="hand2")

    def _card_grid(self, cards: list[tuple[str, str, str, object]], columns: int = 3) -> None:
        grid = tk.Frame(self.page_host, bg=BG)
        grid.pack(fill="x", padx=24, pady=(0, 16))
        for col in range(columns):
            grid.grid_columnconfigure(col, weight=1, uniform="cards")
        for index, (icon, title, description, action) in enumerate(cards):
            row, col = divmod(index, columns)
            card = tk.Frame(grid, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
            card.grid(row=row, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 6 if col < columns - 1 else 0), pady=(0, 10))
            inside = tk.Frame(card, bg=WHITE)
            inside.pack(fill="both", expand=True, padx=15, pady=14)
            tk.Label(inside, text=icon, bg=SOFT_TEAL, fg=TEAL_DARK,
                     font=("Segoe UI Symbol", 15, "bold"), width=3, pady=4).pack(anchor="w")
            tk.Label(inside, text=title, bg=WHITE, fg=INK, font=("Segoe UI", 10, "bold"),
                     wraplength=225, justify="left").pack(anchor="w", pady=(10, 3))
            tk.Label(inside, text=description, bg=WHITE, fg=MUTED, font=("Segoe UI", 9),
                     wraplength=230, justify="left").pack(anchor="w", fill="x")
            if callable(action):
                command = action
            else:
                args, key = action
                command = lambda a=args, k=key, label=title: self.run_action(label, list(a), key=k)
            tk.Button(inside, text="Abrir →", command=command, anchor="w", bg=WHITE, fg=BLUE,
                      activebackground=WHITE, relief="flat", bd=0, font=("Segoe UI", 9, "bold"),
                      cursor="hand2").pack(anchor="w", pady=(10, 0))

    def _build_home(self) -> None:
        self._page_intro("Tu PC, bajo control.", "Elegí una tarea. BOTPYTON trabaja de forma local, muestra lo que encuentra y no modifica nada sin autorización.")
        hero = tk.Frame(self.page_host, bg=NAVY, padx=24, pady=23)
        hero.pack(fill="x", padx=24, pady=(0, 15))
        left = tk.Frame(hero, bg=NAVY)
        left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text="DIAGNÓSTICO RECOMENDADO", bg=NAVY, fg="#6BE0C9",
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(left, text="Revisar el estado de mi PC", bg=NAVY, fg=WHITE,
                 font=("Segoe UI", 17, "bold")).pack(anchor="w", pady=(8, 5))
        tk.Label(left, text="Memoria disponible, espacio del disco, procesos y elementos que conviene revisar. Solo lectura.",
                 bg=NAVY, fg="#CDD8E7", font=("Segoe UI", 9), wraplength=450,
                 justify="left").pack(anchor="w")
        self._button(left, "INICIAR ANÁLISIS", lambda: self.run_action(
            "Análisis del PC", ["--pc-check"], key="pc_check"), primary=True).pack(anchor="w", pady=(16, 0))
        right = tk.Frame(hero, bg="#1C2D46", padx=17, pady=16)
        right.pack(side="right", padx=(20, 0), fill="y")
        for icon, title, desc in (
            ("✓", "Sin IA ni API", "Sin servicios de IA"),
            ("↻", "Bajo demanda", "Se cierra con la ventana"),
            ("◇", "Recuperable", "Cuarentena, no borrado"),
        ):
            row = tk.Frame(right, bg="#1C2D46")
            row.pack(anchor="w", pady=4)
            tk.Label(row, text=icon, bg="#1C2D46", fg="#69DBC8",
                     font=("Segoe UI", 12, "bold"), width=2).pack(side="left")
            col = tk.Frame(row, bg="#1C2D46")
            col.pack(side="left")
            tk.Label(col, text=title, bg="#1C2D46", fg=WHITE,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w")
            tk.Label(col, text=desc, bg="#1C2D46", fg="#B8C7DA",
                     font=("Segoe UI", 8)).pack(anchor="w")
        tk.Label(self.page_host, text="HERRAMIENTAS RÁPIDAS", bg=BG, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=24, pady=(4, 9))
        self._card_grid([
            ("⌁", "Archivos temporales", "Revisar archivos antiguos antes de moverlos.", (["--pc-cleanup-preview"], "temp_preview")),
            ("◎", "Caché del navegador", "Ver caché elegible; no toca contraseñas ni historial.", (["--pc-app-cleanup-preview"], "app_cache_preview")),
            ("▥", "Memoria y procesos", "Ver procesos con más memoria, sin cerrarlos.", (["--pc-processes"], "processes")),
            ("◷", "Programas al inicio", "Revisar destinos de inicio, sin desactivar nada.", (["--pc-startup-advisor"], "startup")),
            ("▦", "Aplicaciones instaladas", "Consultar el inventario sin desinstalar programas.", (["--pc-apps"], "apps")),
            ("▤", "Analizar un proyecto", "Inspeccionar una carpeta de trabajo.", lambda: self.show_page("Proyectos")),
        ], 3)

    def _build_pc_page(self) -> None:
        self._page_intro("Cuidado del PC", "Herramientas de diagnóstico y limpieza supervisada. Primero revisás; luego elegís qué hacer.")
        self._build_candidate_panel()
        self._card_grid([
            ("⌂", "Análisis general", "Estado del disco, RAM, procesos y hallazgos locales.", (["--pc-check"], "pc_check")),
            ("⌁", "Archivos temporales", "Buscar archivos antiguos. La vista previa no los mueve.", (["--pc-cleanup-preview"], "temp_preview")),
            ("◎", "Caché del navegador", "Revisar caché de Chrome, Edge, Brave y Firefox.", (["--pc-app-cleanup-preview"], "app_cache_preview")),
            ("▦", "Caché de programas", "Revisar cachés de Discord, Slack y VS Code.", (["--pc-desktop-cache-preview"], "desktop_cache")),
            ("▥", "Memoria y procesos", "Ver consumo actual, sin cerrar procesos.", (["--pc-processes"], "processes")),
            ("◷", "Inicio de Windows", "Ver destinos de inicio sin desactivar programas.", (["--pc-startup-advisor"], "startup")),
            ("▤", "Aplicaciones instaladas", "Ver nombres, editor y versión.", (["--pc-apps"], "apps")),
            ("▧", "Estado de discos", "Consultar el estado que informa Windows.", (["--pc-disk-health"], "disk_health")),
            ("◇", "Controladores", "Ver metadatos locales; no actualiza drivers.", (["--pc-drivers"], "drivers")),
            ("✓", "Revisar requisitos", "Comprobar Python, espacio, RAM y herramientas.", (["--doctor"], "doctor")),
            ("▧", "Registro de operaciones", "Verificar el registro local de cuarentenas y restauraciones.", (["--pc-audit"], "pc_audit")),
            ("◉", "Medir una pasada", "Diagnóstico acotado; no es una comparación con CCleaner.", (["--pc-benchmark"], "benchmark")),
        ], 3)
        block = tk.Frame(self.page_host, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        block.pack(fill="x", padx=24, pady=(0, 12))
        tk.Label(block, text="Herramientas por carpeta", bg=WHITE, fg=INK,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(13, 3))
        tk.Label(block, text="Elegí una carpeta. El análisis es limitado y no borra archivos.",
                 bg=WHITE, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 10))
        row = tk.Frame(block, bg=WHITE)
        row.pack(fill="x", padx=16, pady=(0, 14))
        self._button(row, "Ver archivos más grandes", self._analyze_storage, small=True).pack(side="left", padx=(0, 8))
        self._button(row, "Buscar duplicados exactos", self._find_duplicates, small=True).pack(side="left")
        restore = tk.Frame(self.page_host, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        restore.pack(fill="x", padx=24, pady=(0, 20))
        tk.Label(restore, text="Cuarentena y recuperación", bg=WHITE, fg=INK,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(13, 3))
        tk.Label(restore, text="Los archivos se ponen en cuarentena, no se eliminan definitivamente. Elegí un manifest.json y revisá primero qué se puede recuperar.",
                 bg=WHITE, fg=MUTED, wraplength=830, justify="left",
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 10))
        restore_row = tk.Frame(restore, bg=WHITE)
        restore_row.pack(fill="x", padx=16, pady=(0, 14))
        self._button(restore_row, "Revisar restauración de temporales", lambda: self._choose_restore_manifest("temp"), small=True).pack(side="left", padx=(0, 8))
        self._button(restore_row, "Revisar restauración de caché web", lambda: self._choose_restore_manifest("browser"), small=True).pack(side="left")

    def _build_projects_page(self) -> None:
        self._page_intro("Trabajar en un proyecto", "Inspeccioná repositorios, detectá pruebas y procesá tareas permitidas. Seleccioná primero la carpeta de trabajo.")
        panel = tk.Frame(self.page_host, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        panel.pack(fill="x", padx=24, pady=(0, 15))
        tk.Label(panel, text="CARPETA DEL PROYECTO", bg=WHITE, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=16, pady=(14, 5))
        pathrow = tk.Frame(panel, bg=WHITE)
        pathrow.pack(fill="x", padx=16, pady=(0, 14))
        tk.Entry(pathrow, textvariable=self.project_path, bg="#F8FAFD", fg=INK, relief="flat",
                 highlightbackground=LINE, highlightthickness=1, font=("Segoe UI", 9)).pack(side="left", fill="x", expand=True, ipady=9)
        self._button(pathrow, "Elegir carpeta…", self._choose_project, small=True).pack(side="left", padx=(8, 0))
        self._card_grid([
            ("⌕", "Analizar proyecto", "Inventario limitado sin ejecutar código.", lambda: self.run_action(
                "Analizar proyecto", ["--inspect-repository", self.project_path.get().strip() or "."], key="project_inspect")),
            ("✓", "Detectar pruebas", "Encontrar pruebas conocidas sin ejecutarlas.", lambda: self.run_action(
                "Detectar pruebas", ["--list-project-checks", "--project-root", self.project_path.get().strip() or "."], key="project_checks")),
            ("▶", "Ejecutar una prueba", "Pide el ID exacto y permiso antes de correr código.", self._run_project_check),
            ("≡", "Ver tareas pendientes", "Consultar la cola local sin procesarla.", (["--queue"], "queue")),
            ("＋", "Agregar tarea JSON", "Validar y encolar una tarea sin ejecutarla.", self._enqueue_task),
            ("⚙", "Procesar una tarea", "Ejecutar como máximo una tarea con confirmación.", self._work_once),
        ], 2)

    def _build_more_page(self) -> None:
        self._page_intro("Más herramientas", "Opciones ocasionales. Cualquier consulta en línea se señala antes de ejecutarse.")
        self._card_grid([
            ("✓", "Diagnóstico de BOTPYTON", "Revisar Python, espacio, RAM y archivos esenciales.", (["--doctor"], "doctor")),
            ("⌁", "Comprobar el núcleo", "Validar sintaxis Python y memoria local.", (["--check"], "check")),
            ("▤", "Herramientas instaladas", "Detectar herramientas sin instalarlas.", (["--tools"], "tools")),
            ("≡", "Cola de tareas", "Consultar las tareas pendientes o terminadas.", (["--queue"], "queue")),
            ("◷", "Pruebas del programa", "Ejecutar las pruebas propias de BOTPYTON.", self._run_self_tests),
            ("▣", "Crear ZIP portátil", "Empaquetar el código; no genera un EXE autónomo.", self._package_source),
        ], 2)
        note = tk.Frame(self.page_host, bg=SOFT_AMBER, highlightbackground="#F0D49B", highlightthickness=1)
        note.pack(fill="x", padx=24, pady=(0, 20))
        tk.Label(note, text="Consulta opcional de actualizaciones", bg=SOFT_AMBER, fg="#80500A",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(12, 4))
        tk.Label(note, text="WinGet consulta a Microsoft por Internet para listar actualizaciones disponibles. BOTPYTON no instala ni actualiza aplicaciones automáticamente.",
                 bg=SOFT_AMBER, fg="#80500A", font=("Segoe UI", 9), wraplength=820, justify="left").pack(anchor="w", padx=16)
        self._button(note, "Consultar actualizaciones disponibles…", self._check_updates).pack(anchor="w", padx=16, pady=(10, 12))

    def _build_candidate_panel(self) -> None:
        panel = tk.Frame(self.page_host, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        panel.pack(fill="x", padx=24, pady=(0, 15))
        tk.Label(panel, text="Archivos encontrados", bg=WHITE, fg=INK,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(12, 4))
        tk.Label(panel, text="Seleccioná manualmente las filas que reconocés. La selección se vuelve a validar antes de mover un archivo.",
                 bg=WHITE, fg=MUTED, wraplength=830, justify="left",
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 8))
        holder = tk.Frame(panel, bg=WHITE)
        holder.pack(fill="x", padx=16, pady=(0, 7))
        self.candidate_tree = ttk.Treeview(holder, columns=("path", "source", "size", "age"),
                                           show="headings", selectmode="extended", height=4)
        for column, heading, width, anchor in (
            ("path", "Archivo o ruta", 390, "w"), ("source", "Tipo", 145, "w"),
            ("size", "Tamaño", 85, "e"), ("age", "Antigüedad", 80, "e"),
        ):
            self.candidate_tree.heading(column, text=heading)
            self.candidate_tree.column(column, width=width, minwidth=70, anchor=anchor)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.candidate_tree.yview)
        self.candidate_tree.configure(yscrollcommand=scroll.set)
        self.candidate_tree.pack(side="left", fill="x", expand=True)
        scroll.pack(side="right", fill="y")
        self.candidate_note = tk.Label(panel, text="Elegí «Archivos temporales» o «Caché del navegador» para cargar la lista.",
                                       bg=WHITE, fg=MUTED, font=("Segoe UI", 9),
                                       justify="left", wraplength=830)
        self.candidate_note.pack(anchor="w", padx=16, pady=(0, 7))
        buttons = tk.Frame(panel, bg=WHITE)
        buttons.pack(fill="x", padx=16, pady=(0, 13))
        self.candidate_action = self._button(buttons, "Poner selección en cuarentena (reversible)", self._quarantine_selection, small=True)
        self.candidate_action.configure(state="disabled")
        self.candidate_action.pack(side="left")
        self._button(buttons, "Limpiar selección", lambda: self.candidate_tree.selection_remove(self.candidate_tree.selection()), small=True).pack(side="left", padx=(8, 0))
        self.candidate_lookup = {}
        self.candidate_report = None
        self.candidate_kind = None

    def _choose_project(self) -> None:
        current = self.project_path.get().strip()
        folder = filedialog.askdirectory(title="Elegir carpeta del proyecto",
            initialdir=current if Path(current).is_dir() else str(Path.home()))
        if folder:
            self.project_path.set(folder)

    def _analyze_storage(self) -> None:
        folder = filedialog.askdirectory(title="Elegir una carpeta para analizar almacenamiento", initialdir=str(Path.home()))
        if folder:
            self.run_action("Archivos más grandes", ["--pc-storage", folder], key="storage")

    def _find_duplicates(self) -> None:
        folder = filedialog.askdirectory(title="Elegir carpeta para buscar duplicados", initialdir=str(Path.home()))
        if folder and messagebox.askyesno("Buscar duplicados",
            "BOTPYTON leerá los archivos elegibles para calcular SHA-256 y agrupar copias idénticas. No borrará ni moverá nada.\n\n¿Continuar?"):
            self.run_action("Buscar duplicados", ["--pc-duplicates", folder], key="duplicates")

    def _run_project_check(self) -> None:
        project = self.project_path.get().strip() or "."
        check_id = simpledialog.askstring("Ejecutar una prueba",
            "Escribí el ID exacto que apareció en «Detectar pruebas».\nEjemplo: python-unittest", parent=self.root)
        if not check_id or not check_id.strip():
            return
        if messagebox.askyesno("Confirmar ejecución",
            f"Se ejecutará «{check_id.strip()}» en:\n{project}\n\nUn test/build es código del proyecto y puede cambiar archivos o usar recursos. Se aplican límites de tiempo y memoria cuando están disponibles.\n\n¿Continuar?"):
            self.run_action("Ejecutar prueba", ["--run-project-check", check_id.strip(),
                "--project-root", project, "--execute-project-code"], key="project_run")

    def _enqueue_task(self) -> None:
        path = filedialog.askopenfilename(title="Elegir tarea JSON",
            filetypes=[("Archivo JSON", "*.json"), ("Todos los archivos", "*.*")])
        if path and messagebox.askyesno("Agregar tarea",
            "BOTPYTON validará la tarea contra su lista de acciones permitidas y la guardará en la cola. No la ejecutará ahora.\n\n¿Agregarla?"):
            self.run_action("Agregar tarea", ["--enqueue", path], key="enqueue")

    def _work_once(self) -> None:
        project = self.project_path.get().strip() or "."
        if messagebox.askyesno("Procesar una tarea",
            f"Se intentará ejecutar como máximo una tarea lista en:\n{project}\n\nLa tarea puede generar informes o copias de seguridad. No se ejecutan tareas en segundo plano.\n\n¿Continuar?"):
            self.run_action("Procesar una tarea", ["--work-once", "--project-root", project], key="work_once")

    def _run_self_tests(self) -> None:
        if not messagebox.askyesno("Pruebas de BOTPYTON",
            "Se ejecutará la suite local de unittest. Puede consumir CPU y RAM mientras corre.\n\n¿Continuar?"):
            return
        def run_tests():
            import unittest
            buffer = StringIO()
            root = Path.cwd()
            suite = unittest.defaultTestLoader.discover(str(root / "tests"), top_level_dir=str(root))
            result = unittest.TextTestRunner(stream=buffer, verbosity=2).run(suite)
            return {"product": "BOTPYTON", "status": "completed" if result.wasSuccessful() else "error",
                    "tests_run": result.testsRun, "failures": len(result.failures),
                    "errors": len(result.errors), "details": buffer.getvalue()[-7000:],
                    "note": "Pruebas ejecutadas en el equipo local."}
        self._run_callable("Pruebas de BOTPYTON", run_tests, key="self_tests")

    def _package_source(self) -> None:
        target = filedialog.askdirectory(title="Elegir carpeta de destino del ZIP", initialdir=str(Path.home()))
        if target:
            self.run_action("Crear ZIP portátil", ["--package-portable", target, "--project-root", str(Path.cwd())], key="package")

    def _check_updates(self) -> None:
        if messagebox.askyesno("Consulta de actualizaciones",
            "Esta acción consulta WinGet/Microsoft por Internet para listar actualizaciones. No instala ni actualiza ningún programa.\n\n¿Permitir la consulta?"):
            self.run_action("Consultar actualizaciones", ["--pc-update-check"], key="updates")

    def _choose_restore_manifest(self, kind: str) -> None:
        path = filedialog.askopenfilename(title="Elegir manifest.json de una cuarentena",
            filetypes=[("Manifiesto JSON", "manifest.json"), ("Archivos JSON", "*.json"), ("Todos los archivos", "*.*")],
            initialdir=str(Path.home()))
        if not path:
            return
        self._pending_restore_manifest = path
        key = "temp_restore_preview" if kind == "temp" else "browser_restore_preview"
        option = "--pc-cleanup-restore-preview" if kind == "temp" else "--pc-app-cache-restore-preview"
        self.run_action("Revisar restauración", [option, path], key=key)

    def run_action(self, title: str, argv: list[str], *, key: str = "") -> None:
        if self.busy:
            messagebox.showinfo("BOTPYTON está trabajando", "Hay una operación en curso. Esperá a que termine antes de iniciar otra.")
            return
        self._start_work(title)
        threading.Thread(target=self._command_worker, args=(title, argv, key), daemon=True).start()

    def _run_callable(self, title: str, callback, *, key: str) -> None:
        if self.busy:
            messagebox.showinfo("BOTPYTON está trabajando", "Hay una operación en curso. Esperá a que termine antes de iniciar otra.")
            return
        self._start_work(title)
        threading.Thread(target=self._callable_worker, args=(title, callback, key), daemon=True).start()

    def _start_work(self, title: str) -> None:
        self.busy = True
        self.top_status.configure(text="● TRABAJANDO", fg=BLUE)
        self.result_badge.configure(text="En curso…", bg=SOFT_BLUE, fg=BLUE)
        self.progress.start(10)
        self.restore_button.configure(state="disabled")
        self._set_output(f"{title}\n\nBOTPYTON está trabajando en una sola operación. Esperá al informe.")
        tree = getattr(self, "candidate_tree", None)
        if tree is not None:
            try:
                if tree.winfo_exists():
                    for item in tree.get_children():
                        tree.delete(item)
            except tk.TclError:
                pass
        self.candidate_lookup.clear()
        self.candidate_kind = None
        self.candidate_report = None
        self.restore_ready = 0

    def _command_worker(self, title: str, argv: list[str], key: str) -> None:
        code, output = _call_main(argv)
        self.root.after(0, lambda: self._finish_work(title, key, code, output))

    def _callable_worker(self, title: str, callback, key: str) -> None:
        try:
            report = callback()
            code = 0
            if isinstance(report, dict) and "status" in report and report.get("product") == "BOTPYTON PC Care":
                from .operation_guard import assess_operation_result
                assessment = assess_operation_result(report)
                code = int(assessment["exit_code"])
                report = dict(report)
                report["operation_assessment"] = assessment
            elif isinstance(report, dict) and report.get("status") == "error":
                code = 1
            output = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception as exc:
            code = 1
            output = f"Error: {type(exc).__name__}: {str(exc)[:600]}"
        self.root.after(0, lambda: self._finish_work(title, key, code, output))

    def _finish_work(self, title: str, key: str, code: int, output: str) -> None:
        self.busy = False
        self.progress.stop()
        self.top_status.configure(text="● LISTO" if code == 0 else ("● REVISAR" if code == 2 else "● ATENCIÓN"),
                                  fg=TEAL_DARK if code == 0 else ("#94600A" if code == 2 else RED))
        self.last_raw = output
        self.last_exit_code = code
        self.technical_view = False
        try:
            data = json.loads(output)
            self.last_report = data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            data = None
            self.last_report = None
        self._set_output(format_report(output, code))
        if code == 0:
            self.result_badge.configure(text="Completado", bg=SOFT_TEAL, fg=TEAL_DARK)
        elif code == 2:
            self.result_badge.configure(text="Parcial · revisar", bg=SOFT_AMBER, fg="#80500A")
        else:
            self.result_badge.configure(text="Revisar resultado", bg="#FCE9EA", fg=RED)

        self.restore_button.configure(state="disabled", text="Restaurar elementos revisados")
        if key in {"temp_preview", "app_cache_preview"} and isinstance(self.last_report, dict):
            if not hasattr(self, "candidate_tree") or not self.candidate_tree.winfo_exists():
                self.show_page("Cuidado del PC")
            self._show_candidates(key, self.last_report)
        elif key in {"temp_restore_preview", "browser_restore_preview"} and isinstance(self.last_report, dict):
            self.restore_kind = "temp" if key == "temp_restore_preview" else "browser"
            self.restore_manifest = self._pending_restore_manifest
            ready = self.last_report.get("ready_count", 0)
            self.restore_ready = ready if isinstance(ready, int) and ready > 0 else 0
            if self.restore_ready:
                self.restore_button.configure(state="normal", text=f"Restaurar {self.restore_ready} elemento(s) verificado(s)")
            else:
                self.restore_button.configure(text="No hay elementos listos para restaurar")
        else:
            self.restore_kind = None
            self.restore_manifest = None
            self.restore_ready = 0
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _show_candidates(self, key: str, report: dict) -> None:
        self.candidate_report = report
        self.candidate_kind = "temp" if key == "temp_preview" else "browser"
        candidates = report.get("candidates")
        complete = (report.get("status") == "preview_complete"
                    and report.get("scan_limited") is not True
                    and report.get("access_errors", 0) == 0)
        if not isinstance(candidates, list):
            candidates = []
        for item in self.candidate_tree.get_children():
            self.candidate_tree.delete(item)
        self.candidate_lookup.clear()
        for index, item in enumerate(candidates, start=1):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or item.get("location") or "(ruta no disponible)")
            source = str(item.get("root_label") or "Temporales") if self.candidate_kind == "temp" else " / ".join(str(item.get(k, "")) for k in ("browser", "cache_area") if item.get(k))
            size = format_bytes(item.get("size_bytes", 0))
            age = f"{item.get('age_days', '?')} días"
            iid = str(index)
            self.candidate_tree.insert("", "end", iid=iid, values=(path, source, size, age))
            self.candidate_lookup[iid] = item
        if complete and candidates:
            self.candidate_action.configure(state="normal")
            self.candidate_note.configure(text=f"{len(candidates)} archivo(s) elegible(s). Seleccioná solo los que reconocés (Ctrl o Shift para varios). No se borra nada definitivamente.")
        elif candidates:
            self.candidate_action.configure(state="disabled")
            self.candidate_note.configure(text="El análisis tuvo límites o errores. Por seguridad no se habilita la limpieza con una lista parcial.")
        else:
            self.candidate_action.configure(state="disabled")
            self.candidate_note.configure(text="No se encontraron archivos elegibles.")
        if self.candidate_kind == "browser" and candidates:
            self.candidate_note.configure(text=self.candidate_note.cget("text") + " Cerrá el navegador antes de mover caché.")

    def _quarantine_selection(self) -> None:
        if self.busy or not self.candidate_report or not self.candidate_kind:
            return
        ids = self.candidate_tree.selection()
        selected = [self.candidate_lookup[iid] for iid in ids if iid in self.candidate_lookup]
        if not selected:
            messagebox.showinfo("Seleccioná archivos", "Elegí una o más filas de la lista primero.")
            return
        if self.candidate_kind == "temp":
            phrase = "QUARANTINE OLD TEMP FILES"
            title = "Cuarentena de temporales"
            description = "Los archivos seleccionados se moverán a una cuarentena recuperable. Se volverá a verificar ruta, antigüedad, tamaño y SHA-256. No se borrarán definitivamente."
        else:
            phrase = "QUARANTINE SELECTED BROWSER CACHE"
            title = "Cuarentena de caché del navegador"
            description = "La caché seleccionada se moverá a una cuarentena recuperable. No se toca historial, contraseñas, cookies ni sesiones. Cerrá el navegador primero."
        if not messagebox.askyesno(title, description + "\n\n¿Continuar?"):
            return
        typed = simpledialog.askstring("Confirmación explícita", f"Para confirmar, escribí exactamente:\n\n{phrase}", parent=self.root)
        if typed != phrase:
            messagebox.showinfo("Cancelado", "La confirmación no coincidió. No se movió ningún archivo.")
            return
        if self.candidate_kind == "temp":
            preview = dict(self.candidate_report)
            preview["candidates"] = selected
            preview["candidate_count"] = len(selected)
            preview["candidate_bytes"] = sum(int(x.get("size_bytes", 0)) for x in selected if isinstance(x.get("size_bytes", 0), int))
            preview["candidate_mib"] = round(preview["candidate_bytes"] / (1024 * 1024), 2)
            def action():
                from .pc_care import quarantine_cleanup_candidates
                return quarantine_cleanup_candidates(preview, confirmation=phrase)
            self._run_callable("Poner temporales en cuarentena", action, key="temp_quarantine")
        else:
            paths = [str(item["path"]) for item in selected if isinstance(item.get("path"), str)]
            def action():
                from .application_cache_quarantine import quarantine_application_cache_candidates
                return quarantine_application_cache_candidates(paths, confirmation=phrase)
            self._run_callable("Poner caché en cuarentena", action, key="browser_quarantine")

    def restore_after_preview(self) -> None:
        if self.busy or not self.restore_manifest or not self.restore_kind or self.restore_ready < 1:
            return
        kind, manifest = self.restore_kind, self.restore_manifest
        phrase = "RESTORE BOTPYTON QUARANTINE" if kind == "temp" else "RESTORE BOTPYTON BROWSER CACHE"
        message = (f"Se intentará restaurar los {self.restore_ready} elemento(s) que pasaron la comprobación. "
                   "No se sobrescriben archivos existentes.")
        if not messagebox.askyesno("Confirmar restauración", message + "\n\n¿Continuar?"):
            return
        typed = simpledialog.askstring("Confirmación explícita", f"Para confirmar, escribí exactamente:\n\n{phrase}", parent=self.root)
        if typed != phrase:
            messagebox.showinfo("Cancelado", "La confirmación no coincidió. No se restauró ningún archivo.")
            return
        if kind == "temp":
            def action():
                from .pc_care import restore_quarantined_files
                return restore_quarantined_files(manifest, confirmation=phrase)
        else:
            def action():
                from .application_cache_quarantine import restore_application_cache_quarantine
                return restore_application_cache_quarantine(manifest, confirmation=phrase)
        self._run_callable("Restaurar desde cuarentena", action, key="restore")

    def toggle_details(self) -> None:
        if self.last_raw:
            self.technical_view = not self.technical_view
            self.detail_button.configure(text="Ver resumen" if self.technical_view else "Ver JSON técnico")
            self._set_output(format_report(self.last_raw, self.last_exit_code, technical=self.technical_view))

    def close(self) -> None:
        if self.busy and not messagebox.askyesno("Operación en curso",
            "Hay una tarea en ejecución. Cerrarla puede interrumpirla.\n\n¿Cerrar de todos modos?"):
            return
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    try:
        app = BotPytonApp()
        app.run()
        return 0
    except tk.TclError as exc:
        try:
            messagebox.showerror("No se pudo abrir BOTPYTON",
                f"No se pudo iniciar la interfaz de escritorio.\n\n{exc}\n\nUsá BOTPYTON-CLI.bat como alternativa.")
        except tk.TclError:
            print(f"No se pudo abrir BOTPYTON: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
