"""Simple, consumer-facing dashboard for BOTPYTON PC diagnostics.

The UI deliberately hides command-line terminology. The analysis remains deterministic,
local, sequential, read-only, and bounded by each underlying diagnostic command.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from .gui import _call_main

BG = "#F3F6FA"
WHITE = "#FFFFFF"
NAVY = "#15243A"
NAVY_LIGHT = "#20334F"
INK = "#19273A"
MUTED = "#68778C"
LINE = "#DCE5EF"
TEAL = "#0A9488"
TEAL_DARK = "#08786F"
PALE_TEAL = "#E3F6F2"
PALE_BLUE = "#EAF1FC"
PALE_AMBER = "#FFF3DC"
PALE_RED = "#FCE9EA"
BLUE = "#2F69C8"
AMBER = "#87560A"
RED = "#B83B45"

# User-facing stage names only. The corresponding CLI switches never appear in the UI.
ANALYSIS_STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Revisando el estado general del equipo", ("--pc-check",)),
    ("Comprobando recursos y requisitos básicos", ("--doctor",)),
    ("Revisando qué aplicaciones consumen memoria", ("--pc-processes",)),
    ("Comprobando qué programas se inician con Windows", ("--pc-startup-advisor",)),
    ("Consultando el inventario de aplicaciones", ("--pc-apps",)),
    ("Revisando la información disponible de los discos", ("--pc-disk-health",)),
    ("Consultando los controladores detectados", ("--pc-drivers",)),
    ("Buscando archivos temporales antiguos, sin tocarlos", ("--pc-cleanup-preview",)),
)

_STATUS_TEXT = {
    "collected": ("Revisado", "ok"),
    "completed": ("Completado", "ok"),
    "measured": ("Medido", "ok"),
    "scan_complete": ("Revisión completa", "ok"),
    "preview_complete": ("Lista preparada", "ok"),
    "diagnostic_only": ("Diagnóstico listo", "ok"),
    "no_candidates": ("No hay elementos elegibles", "ok"),
    "no_cache_candidates": ("No hay elementos elegibles", "ok"),
    "not_present": ("No encontrado", "neutral"),
    "not_checked": ("No comprobado", "neutral"),
    "unsupported_platform": ("No disponible en este sistema", "neutral"),
    "unavailable": ("No disponible", "warning"),
    "access_limited": ("Acceso limitado", "warning"),
    "partial_limit": ("Revisión limitada", "warning"),
    "scan_partial": ("Revisión parcial", "warning"),
    "preview_partial": ("Vista previa parcial", "warning"),
    "partial": ("Resultado parcial", "warning"),
    "timeout": ("Se agotó el tiempo disponible", "warning"),
    "error": ("Necesita revisión", "error"),
    "integrity_error": ("No se pudo verificar la integridad", "error"),
    "blocked": ("Acción bloqueada por seguridad", "warning"),
}

def format_bytes(value: object) -> str:
    """Format byte counts without leaking programmer-oriented units."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "No disponible"
    size = max(0.0, float(value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size):,} B" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"


def parse_report(raw: str) -> Any:
    try:
        return json.loads(raw.strip())
    except (json.JSONDecodeError, TypeError):
        return None


def report_status(report: Any, exit_code: int = 0) -> tuple[str, str]:
    """Return a plain-language status and visual category for one diagnostic."""
    status = report.get("status") if isinstance(report, dict) else None
    if isinstance(status, str):
        if status in _STATUS_TEXT:
            return _STATUS_TEXT[status]
        if "partial" in status or "limit" in status:
            return ("Revisión parcial", "warning")
        if "error" in status or "failed" in status:
            return ("Necesita revisión", "error")
    if exit_code != 0:
        return ("Necesita revisión", "error")
    return ("Resultado recibido", "neutral")


def summarize_report(title: str, report: Any, exit_code: int = 0) -> str:
    """Turn machine output into a compact user-facing summary."""
    state, _category = report_status(report, exit_code)
    lines = [f"{title}: {state}."]
    if not isinstance(report, dict):
        return lines[0] + (" No se pudo interpretar el detalle de esta comprobación." if exit_code else "")

    # Surface only useful, understandable facts. Technical fields remain in the detail window.
    labels = (
        ("available_memory_mb", "RAM disponible", "mb"),
        ("physical_available_mb", "RAM física disponible", "mb"),
        ("total_memory_mb", "RAM instalada", "mb"),
        ("free_gib", "Espacio libre en disco", "gib"),
        ("free_percent", "Disco libre", "percent"),
        ("candidate_count", "Archivos temporales elegibles", "count"),
        ("candidate_bytes", "Tamaño de esos archivos", "bytes"),
        ("files_examined", "Archivos revisados", "count"),
        ("entries_examined", "Elementos revisados", "count"),
        ("items_count", "Elementos detectados", "count"),
        ("returned_count", "Elementos mostrados", "count"),
        ("access_errors", "Problemas de acceso", "count"),
    )
    for key, label, kind in labels:
        value = report.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if kind == "mb":
                shown = f"{value:,.0f} MB"
            elif kind == "gib":
                shown = f"{value:,.1f} GB"
            elif kind == "percent":
                shown = f"{value:.1f}%"
            elif kind == "bytes":
                shown = format_bytes(value)
            else:
                shown = f"{int(value):,}"
            lines.append(f"  • {label}: {shown}")

    # Use recommendations written by the underlying checks; do not invent an issue score.
    for key in ("recommendation", "next_action", "message", "note"):
        value = report.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"  {value.strip()[:340]}")
            break

    findings = report.get("findings")
    if isinstance(findings, list):
        shown = 0
        for item in findings:
            if not isinstance(item, dict):
                continue
            name = item.get("title") or item.get("name") or item.get("label")
            detail = item.get("detail") or item.get("recommendation")
            if name:
                lines.append("  • " + str(name)[:160] + (": " + str(detail)[:240] if detail else ""))
                shown += 1
                if shown >= 3:
                    break
    return "\n".join(lines)


class SimpleBotPytonApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("BOTPYTON | Tu técnico informático")
        self.root.geometry("940x760")
        self.root.minsize(780, 650)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.busy = False
        self.last_technical = ""
        self.last_title = ""
        self._build()

    def _label(self, parent: tk.Misc, text: str, *, size: int = 10, color: str = INK,
               bold: bool = False, bg: str = WHITE, **kwargs: Any) -> tk.Label:
        return tk.Label(parent, text=text, bg=bg, fg=color,
                        font=("Segoe UI", size, "bold" if bold else "normal"),
                        **kwargs)

    def _button(self, parent: tk.Misc, text: str, command, *, primary: bool = False,
                compact: bool = False) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, cursor="hand2", relief="flat", bd=0,
            bg=TEAL if primary else PALE_BLUE,
            fg=WHITE if primary else BLUE,
            activebackground=TEAL_DARK if primary else "#DDE9FA",
            activeforeground=WHITE if primary else BLUE,
            font=("Segoe UI", 10 if primary else 9, "bold"),
            padx=22 if primary else 14, pady=13 if primary else (8 if compact else 11),
        )

    def _build(self) -> None:
        header = tk.Frame(self.root, bg=NAVY, padx=24, pady=15)
        header.pack(fill="x")
        mark = tk.Frame(header, bg="#24415C", width=42, height=42)
        mark.pack(side="left")
        mark.pack_propagate(False)
        self._label(mark, "B", size=21, color="#70E1D0", bold=True, bg="#24415C").place(relx=.5, rely=.47, anchor="center")
        brand = tk.Frame(header, bg=NAVY)
        brand.pack(side="left", padx=(12, 0))
        self._label(brand, "BOTPYTON", size=16, color=WHITE, bold=True, bg=NAVY).pack(anchor="w")
        self._label(brand, "Tu técnico informático personal", size=9, color="#BDCADB", bg=NAVY).pack(anchor="w", pady=(2, 0))
        self.status_label = self._label(header, "● LISTO", size=9, color="#72E0C9", bold=True, bg=NAVY)
        self.status_label.pack(side="right", anchor="center")

        page = tk.Frame(self.root, bg=BG, padx=24, pady=18)
        page.pack(fill="both", expand=True)

        self._label(page, "Cuidar tu PC no debería ser complicado.", size=21, bold=True, bg=BG).pack(anchor="w")
        self._label(
            page,
            "BOTPYTON revisa el equipo por dentro y te explica lo importante en palabras simples.",
            size=10, color=MUTED, bg=BG, wraplength=850, justify="left"
        ).pack(anchor="w", pady=(5, 14))

        hero = tk.Frame(page, bg=NAVY, padx=22, pady=19)
        hero.pack(fill="x")
        hero.grid_columnconfigure(0, weight=1)
        left = tk.Frame(hero, bg=NAVY)
        left.grid(row=0, column=0, sticky="ew")
        self._label(left, "ANÁLISIS AMPLIO DEL PC", size=8, color="#73E1D0", bold=True, bg=NAVY).pack(anchor="w")
        self._label(left, "Encontrá qué conviene revisar", size=16, color=WHITE, bold=True, bg=NAVY).pack(anchor="w", pady=(6, 4))
        self._label(
            left,
            "Comprueba memoria, programas, inicio de Windows, discos y archivos temporales. Solo consulta: no cambia nada.",
            size=9, color="#D0DCEB", bg=NAVY, wraplength=560, justify="left"
        ).pack(anchor="w")
        self.scan_button = self._button(hero, "ANALIZAR MI PC", self.start_full_scan, primary=True)
        self.scan_button.grid(row=0, column=1, rowspan=2, sticky="e", padx=(12, 0), pady=3)

        chips = tk.Frame(page, bg=BG)
        chips.pack(fill="x", pady=(11, 16))
        for title, detail in (
            ("SOLO LECTURA", "No limpia ni repara por su cuenta"),
            ("A DEMANDA", "No queda ejecutándose"),
            ("SIN IA EXTERNA", "No requiere servicios de IA"),
        ):
            chip = tk.Frame(chips, bg=WHITE, highlightbackground=LINE, highlightthickness=1, padx=12, pady=9)
            chip.pack(side="left", fill="x", expand=True, padx=(0, 7))
            self._label(chip, title, size=8, color=TEAL_DARK, bold=True, bg=WHITE).pack(anchor="w")
            self._label(chip, detail, size=8, color=MUTED, bg=WHITE, wraplength=190, justify="left").pack(anchor="w", pady=(4, 0))

        actions = tk.Frame(page, bg=BG)
        actions.pack(fill="x")
        for title, desc, label, cmd in (
            ("Espacio y limpieza", "Primero revisá los temporales elegibles; no se borra nada.", "REVISAR ARCHIVOS", self.start_cleanup_preview),
            ("Más herramientas", "Selección de archivos, cuarentena, restauración y funciones avanzadas.", "ABRIR HERRAMIENTAS", self.open_advanced),
        ):
            card = tk.Frame(actions, bg=WHITE, highlightbackground=LINE, highlightthickness=1, padx=15, pady=13)
            card.pack(side="left", fill="both", expand=True, padx=(0, 8))
            self._label(card, title, size=11, bold=True, bg=WHITE).pack(anchor="w")
            self._label(card, desc, size=9, color=MUTED, bg=WHITE, wraplength=360, justify="left").pack(anchor="w", pady=(5, 11))
            button = self._button(card, label, cmd, compact=True)
            button.pack(anchor="w")

        results = tk.Frame(page, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        results.pack(fill="both", expand=True, pady=(16, 0))
        result_header = tk.Frame(results, bg=WHITE, padx=16, pady=12)
        result_header.pack(fill="x")
        self._label(result_header, "Resultado", size=11, bold=True, bg=WHITE).pack(side="left")
        self.result_badge = self._label(result_header, "Todavía no analizamos el equipo", size=8,
                                        color=BLUE, bold=True, bg=PALE_BLUE, padx=9, pady=5)
        self.result_badge.pack(side="left", padx=(10, 0))
        self.details_button = self._button(result_header, "VER DETALLE TÉCNICO", self.show_technical, compact=True)
        self.details_button.pack(side="right")
        self.details_button.configure(state="disabled")

        self.progress = ttk.Progressbar(results, mode="indeterminate", length=300)
        self.progress.pack(fill="x", padx=16, pady=(0, 5))
        self.progress.stop()
        self.stage_label = self._label(results, "Cuando estés listo, presioná «Analizar mi PC».", size=9,
                                       color=MUTED, bg=WHITE)
        self.stage_label.pack(anchor="w", padx=16, pady=(0, 6))
        self.result_text = tk.Text(results, height=7, wrap="word", bg=WHITE, fg=INK,
                                   bd=0, padx=16, pady=4, font=("Segoe UI", 9),
                                   relief="flat", state="disabled")
        self.result_text.pack(fill="both", expand=True, padx=(0, 5), pady=(0, 8))
        scroll = ttk.Scrollbar(self.result_text.master, orient="vertical", command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=scroll.set)
        scroll.place(relx=1.0, rely=0.45, relheight=0.52, anchor="ne")

    def _set_result(self, text: str) -> None:
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", text)
        self.result_text.configure(state="disabled")

    def _start(self, title: str) -> bool:
        if self.busy:
            messagebox.showinfo("BOTPYTON está trabajando", "La revisión actual todavía no terminó.", parent=self.root)
            return False
        self.busy = True
        self.last_title = title
        self.last_technical = ""
        self.scan_button.configure(state="disabled")
        self.status_label.configure(text="● TRABAJANDO", fg="#8EC8FF")
        self.result_badge.configure(text="En curso", bg=PALE_BLUE, fg=BLUE)
        self.stage_label.configure(text="Preparando la revisión…", fg=BLUE)
        self._set_result(f"{title}\n\nBOTPYTON está trabajando en una operación a la vez. No cierres la ventana hasta que termine.")
        self.progress.start(10)
        return True

    def _post(self, callback) -> None:
        try:
            self.root.after(0, callback)
        except tk.TclError:
            # The user closed the window after explicitly accepting interruption.
            pass

    def start_full_scan(self) -> None:
        if not self._start("Análisis amplio del PC"):
            return
        thread = threading.Thread(target=self._full_scan_worker, daemon=True)
        thread.start()

    def _full_scan_worker(self) -> None:
        reports: list[tuple[str, int, str, Any]] = []
        total = len(ANALYSIS_STEPS)
        for index, (label, argv) in enumerate(ANALYSIS_STEPS, start=1):
            self._post(lambda label=label, index=index, total=total: self.stage_label.configure(
                text=f"Revisión {index} de {total}: {label}", fg=BLUE
            ))
            code, output = _call_main(list(argv))
            reports.append((label, code, output, parse_report(output)))
        self._post(lambda: self._finish_reports("Análisis amplio del PC", reports))

    def start_cleanup_preview(self) -> None:
        if not self._start("Revisión de archivos temporales"):
            return
        self._post(lambda: self.stage_label.configure(text="Buscando archivos temporales antiguos…", fg=BLUE))
        threading.Thread(target=self._cleanup_worker, daemon=True).start()

    def _cleanup_worker(self) -> None:
        code, output = _call_main(["--pc-cleanup-preview"])
        reports = [("Archivos temporales", code, output, parse_report(output))]
        self._post(lambda: self._finish_reports("Revisión de archivos temporales", reports))

    def _finish_reports(self, title: str, reports: list[tuple[str, int, str, Any]]) -> None:
        if not self.root.winfo_exists():
            return
        self.busy = False
        self.progress.stop()
        self.scan_button.configure(state="normal")
        bad = sum(1 for _label, code, _raw, parsed in reports if report_status(parsed, code)[1] in {"warning", "error"})
        good = len(reports) - bad
        all_raw = []
        summaries = []
        for label, code, raw, parsed in reports:
            summaries.append(summarize_report(label, parsed, code))
            all_raw.append(f"===== {label} (código {code}) =====\n{raw.strip()[:12000]}")
        self.last_technical = "\n\n".join(all_raw)[:50000]
        self.details_button.configure(state="normal")
        if bad:
            self.status_label.configure(text="● REVISAR RESULTADOS", fg="#FFD080")
            self.result_badge.configure(text=f"{bad} comprobación(es) a revisar", bg=PALE_AMBER, fg=AMBER)
            heading = f"Revisión terminada: {good} comprobación(es) sin aviso de ejecución y {bad} con resultado limitado o que requiere atención."
        else:
            self.status_label.configure(text="● LISTO", fg="#72E0C9")
            self.result_badge.configure(text="Revisión finalizada", bg=PALE_TEAL, fg=TEAL_DARK)
            heading = f"Revisión terminada: se recibieron resultados de {len(reports)} comprobación(es)."
        tail = (
            "\n\nImportante: este informe es un diagnóstico, no una reparación ni un análisis antivirus. "
            "BOTPYTON no limpió archivos, no cerró programas y no cambió configuraciones."
        )
        if title == "Revisión de archivos temporales":
            tail += "\n\nPara revisar y seleccionar archivos de forma individual antes de moverlos a cuarentena, abrí «Más herramientas»."
        self.stage_label.configure(text="Listo. Podés consultar el detalle técnico si lo necesitás.", fg=MUTED)
        self._set_result(heading + "\n\n" + "\n\n".join(summaries) + tail)

    def show_technical(self) -> None:
        if not self.last_technical:
            return
        window = tk.Toplevel(self.root)
        window.title("BOTPYTON | Detalle técnico")
        window.geometry("880x620")
        window.configure(bg=WHITE)
        top = tk.Frame(window, bg=NAVY, padx=15, pady=12)
        top.pack(fill="x")
        self._label(top, "DETALLE TÉCNICO", size=10, color=WHITE, bold=True, bg=NAVY).pack(anchor="w")
        self._label(top, "Solo para revisar datos detallados; no contiene acciones que se ejecuten al abrir esta ventana.", size=9, color="#D0DCEB", bg=NAVY).pack(anchor="w", pady=(4, 0))
        body = tk.Frame(window, bg=WHITE, padx=12, pady=12)
        body.pack(fill="both", expand=True)
        text = tk.Text(body, wrap="word", font=("Consolas", 9), bg="#F7F9FC", fg=INK, bd=0, padx=12, pady=10)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        text.insert("1.0", self.last_technical[:50000])
        text.configure(state="disabled")

    def open_advanced(self) -> None:
        if self.busy:
            messagebox.showinfo("BOTPYTON está trabajando", "Esperá a que termine la comprobación antes de cambiar de pantalla.", parent=self.root)
            return
        if not messagebox.askyesno(
            "Herramientas avanzadas",
            "Se abrirá la interfaz completa, que incluye selección individual de archivos, cuarentena reversible, restauración y herramientas para proyectos.\n\n¿Continuar?",
            parent=self.root,
        ):
            return
        # Replace this Python process instead of running two copies of the application.
        os.execv(sys.executable, [sys.executable, "-m", "bot.gui"])

    def close(self) -> None:
        if self.busy and not messagebox.askyesno(
            "Revisión en curso", "Cerrar ahora puede interrumpir el análisis. ¿Querés cerrar igualmente?", parent=self.root
        ):
            return
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    try:
        os.chdir(Path(__file__).resolve().parents[1])
        app = SimpleBotPytonApp()
        app.run()
        return 0
    except tk.TclError as exc:
        try:
            messagebox.showerror(
                "No se pudo abrir BOTPYTON",
                f"No se pudo iniciar la interfaz simplificada.\n\n{exc}\n\nUsá BOTPYTON-CLI.bat como alternativa.",
            )
        except tk.TclError:
            print(f"No se pudo abrir BOTPYTON: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
