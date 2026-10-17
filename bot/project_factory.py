"""Fixed-template project factory. No AI, shell commands, or user-provided source execution."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

CATALOG = {
 "telegram-bot": {
  "description": "Telegram polling bot with environment-provided token.",
  "files": {
   "bot.py": "import os\nfrom telegram import Update\nfrom telegram.ext import Application, CommandHandler, ContextTypes\n\nasync def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:\n    if update.message:\n        await update.message.reply_text('Bot listo. Sin IA.')\n\ndef main() -> None:\n    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()\n    if not token:\n        raise SystemExit('Configura TELEGRAM_BOT_TOKEN en el entorno.')\n    app = Application.builder().token(token).build()\n    app.add_handler(CommandHandler('start', start))\n    app.run_polling()\n\nif __name__ == '__main__':\n    main()\n",
   "requirements.txt": "python-telegram-bot>=21,<23\n",
   ".env.example": "TELEGRAM_BOT_TOKEN=configure_me_in_environment\n",
   "README.md": "# {name}\n\nBot de Telegram determinista, sin IA. Requiere Python 3.11+. Instala dependencias con python -m pip install -r requirements.txt, configura TELEGRAM_BOT_TOKEN como variable de entorno y ejecuta python bot.py. No guardes tokens en Git.\n"
  }
 },
 "card-rpg-web": {
  "description": "Playable browser card RPG prototype with turn-based elemental combat.",
  "files": {
   "index.html": "<!doctype html>\n<html lang='es'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{name}</title><style>body{font:16px system-ui;max-width:760px;margin:2rem auto;padding:1rem;background:#151b2b;color:#fff}section{padding:1rem;margin:1rem 0;background:#252f45;border-radius:12px}button{padding:1rem;margin:.3rem;border-radius:8px;cursor:pointer}</style><h1>{name}</h1><p>RPG de cartas por turnos. Sin IA ni conexión externa.</p><section><h2>Jugador: <span id='p'>30</span> HP</h2><h2>Enemigo: <span id='e'>24</span> HP</h2><p>Fuego vence a Bosque, Bosque a Agua y Agua a Fuego.</p><div id='cards'></div><button onclick='reset()'>Nueva batalla</button><p id='log' aria-live='polite'>¡Comienza!</p></section><script>const cards=[['Llama','Fuego',8],['Raíces','Bosque',6],['Marea','Agua',7]],beats={Fuego:'Bosque',Bosque:'Agua',Agua:'Fuego'};let p,e,type,done;function reset(){p=30;e=24;type=['Bosque','Agua','Fuego'][Math.random()*3|0];done=false;document.querySelector('#log').textContent='Enemigo: '+type;draw()}function draw(){document.querySelector('#p').textContent=p;document.querySelector('#e').textContent=e;document.querySelector('#cards').replaceChildren(...cards.map(c=>{let b=document.createElement('button');b.textContent=c[0]+' ('+c[1]+')';b.disabled=done;b.onclick=()=>play(c);return b}))}function play(c){if(done)return;let d=c[2]*(beats[c[1]]===type?2:1);e=Math.max(0,e-d);if(!e){done=true;document.querySelector('#log').textContent='Victoria. Daño: '+d;draw();return}let hit=4+(Math.random()*4|0);p=Math.max(0,p-hit);if(!p)done=true;document.querySelector('#log').textContent=c[0]+' causa '+d+' daño; recibes '+hit+'. '+(done?'Derrota.':'');draw()}reset()</script></html>\n",
   "README.md": "# {name}\n\nPrototipo local jugable en navegador: combate por turnos, tres elementos, ventaja elemental, vida, victoria/derrota y reinicio. Abre index.html. No necesita instalación ni servicios externos. No incluye todavía progresión, guardado ni arte final.\n"
  }
 },
 "desktop-utility": {
  "description": "Cross-platform desktop UI starter using PySide6.",
  "files": {
   "app.py": "import sys\nfrom PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget\n\napp = QApplication(sys.argv)\nwindow = QMainWindow()\nwindow.setWindowTitle('{name}')\nwindow.setMinimumSize(480, 300)\nlabel = QLabel('Aplicación local preparada.')\nbutton = QPushButton('Comprobar estado')\nbutton.clicked.connect(lambda: label.setText('Estado: funcionando'))\nlayout = QVBoxLayout()\nlayout.addWidget(label)\nlayout.addWidget(button)\nroot = QWidget()\nroot.setLayout(layout)\nwindow.setCentralWidget(root)\nwindow.show()\nraise SystemExit(app.exec())\n",
   "requirements.txt": "PySide6>=6.7,<7\n",
   "README.md": "# {name}\n\nBase de aplicación de escritorio Python/PySide6, sin IA. Instala requirements.txt y ejecuta python app.py. Es una ventana funcional inicial, no un reproductor ni una aplicación de captura completa.\n"
  }
 }
}

def safe_name(raw: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", raw.strip()).strip("-_")
    if not value or len(value) > 64:
        raise ValueError("El nombre debe tener entre 1 y 64 caracteres válidos.")
    return value

def list_templates() -> list[dict[str, str]]:
    return [{"id": key, "description": item["description"]} for key, item in sorted(CATALOG.items())]

def create_project(template_id: str, name: str, destination: str | Path) -> dict:
    if template_id not in CATALOG:
        raise ValueError("Plantilla desconocida.")
    project_name = safe_name(name)
    root = Path(destination).expanduser().resolve()
    if root.exists():
        raise FileExistsError(f"El destino ya existe; no se sobrescribe: {root}")
    files = CATALOG[template_id]["files"]
    for rel in files:
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts or len(p.parts) != 1:
            raise ValueError(f"Ruta insegura en catálogo: {rel}")
    root.mkdir(parents=True, exist_ok=False)
    try:
        for rel, text in files.items():
            (root / rel).write_text(text.replace("{name}", project_name), encoding="utf-8", newline="\n")
        manifest = {"generator": "botpyton.project_factory", "ai_enabled": False,
                    "template": template_id, "project_name": project_name, "files": sorted(files)}
        (root / "botpyton-project.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n",
                                                     encoding="utf-8", newline="\n")
    except Exception:
        for child in root.iterdir():
            if child.is_file(): child.unlink()
        root.rmdir()
        raise
    return {"status": "created", "path": str(root), **manifest}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BotPyton fixed-template project factory; no AI")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--template", choices=sorted(CATALOG))
    parser.add_argument("--name")
    parser.add_argument("--destination")
    args = parser.parse_args(argv)
    try:
        if args.list:
            print(json.dumps(list_templates(), ensure_ascii=False, indent=2)); return 0
        if not all((args.template, args.name, args.destination)):
            parser.error("--template, --name y --destination son obligatorios")
        print(json.dumps(create_project(args.template, args.name, args.destination), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(f"PROJECT FACTORY FAIL: {exc}", file=sys.stderr); return 2

if __name__ == "__main__":
    raise SystemExit(main())
