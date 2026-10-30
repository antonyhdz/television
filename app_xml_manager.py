import os
import subprocess
import threading
from pathlib import Path
import xml.etree.ElementTree as ET
import shutil
import tkinter as tk
from tkinter import messagebox, ttk

# Directorios de trabajo actualizados
BASE_DIR = Path(__file__).resolve().parent
GATOTV_DIR = BASE_DIR / "gatotv"
MITV_DIR = BASE_DIR / "mitv"
UNIFIED_XML = BASE_DIR / "guia_unificada.xml"

class EPGManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Gestor y Unificador EPG - XMLTV")
        self.root.geometry("1000x740")
        self.root.minsize(900, 620)

        # Estilos visuales
        style = ttk.Style()
        style.theme_use("clam")
        
        # Contenedor principal de 3 columnas
        main_frame = ttk.Frame(root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        for i in range(3):
            main_frame.columnconfigure(i, weight=1, uniform="col")
        main_frame.rowconfigure(0, weight=1)

        # ==========================================
        # COLUMNA 1: GatoTV
        # ==========================================
        col1_frame = ttk.LabelFrame(main_frame, text=" 1. GatoTV (Masivo) ", padding=10)
        col1_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        
        ttk.Label(col1_frame, text="Guarda en carpeta:\n'gatotv/'", justify=tk.CENTER).pack(pady=5)
        self.btn_gatotv = ttk.Button(col1_frame, text="Actualizar GatoTV", command=self.run_gatotv_thread)
        self.btn_gatotv.pack(fill=tk.X, pady=5)
        
        ttk.Label(col1_frame, text="Archivos en gatotv/:", font=("Arial", 9, "bold")).pack(anchor="w", pady=(10, 2))
        self.txt_gatotv_status = tk.Text(col1_frame, height=20, width=25, state=tk.DISABLED, bg="#f4f4f4")
        self.txt_gatotv_status.pack(fill=tk.BOTH, expand=True)

        # ==========================================
        # COLUMNA 2: MiTV
        # ==========================================
        col2_frame = ttk.LabelFrame(main_frame, text=" 2. MiTV (El Salvador) ", padding=10)
        col2_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)

        ttk.Label(col2_frame, text="Guarda en carpeta:\n'mitv/'", justify=tk.CENTER).pack(pady=5)
        self.btn_mitv = ttk.Button(col2_frame, text="Actualizar MiTV", command=self.run_mitv_thread)
        self.btn_mitv.pack(fill=tk.X, pady=5)

        ttk.Label(col2_frame, text="Archivos en mitv/:", font=("Arial", 9, "bold")).pack(anchor="w", pady=(10, 2))
        self.txt_mitv_status = tk.Text(col2_frame, height=20, width=25, state=tk.DISABLED, bg="#f4f4f4")
        self.txt_mitv_status.pack(fill=tk.BOTH, expand=True)

        # ==========================================
        # COLUMNA 3: Unificar y GitHub
        # ==========================================
        col3_frame = ttk.LabelFrame(main_frame, text=" 3. Unificar y GitHub ", padding=10)
        col3_frame.grid(row=0, column=2, sticky="nsew", padx=5, pady=5)

        ttk.Label(col3_frame, text="Repositorio (usuario/repo):").pack(anchor="w")
        self.ent_repo = ttk.Entry(col3_frame)
        self.ent_repo.insert(0, "antonyhdz/television")
        self.ent_repo.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(col3_frame, text="GitHub Token (PAT) [Opcional]:").pack(anchor="w")
        self.ent_token = ttk.Entry(col3_frame, show="*")
        self.ent_token.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(col3_frame, text="Ruta destino en repo:").pack(anchor="w")
        self.ent_path = ttk.Entry(col3_frame)
        self.ent_path.insert(0, "update_guia/guia_unificada.xml")
        self.ent_path.pack(fill=tk.X, pady=(0, 8))

        self.btn_unify = ttk.Button(col3_frame, text="Generar Archivo Unificado", command=self.unify_xmls)
        self.btn_unify.pack(fill=tk.X, pady=4)

        self.btn_upload = ttk.Button(col3_frame, text="Subir a GitHub (Git)", command=self.upload_to_github_thread)
        self.btn_upload.pack(fill=tk.X, pady=4)

        ttk.Label(col3_frame, text="Enlace RAW:", font=("Arial", 9, "bold")).pack(anchor="w", pady=(6, 2))
        self.txt_raw_link = tk.Text(col3_frame, height=5, width=25, bg="#f4f4f4")
        self.txt_raw_link.pack(fill=tk.BOTH, expand=True)

        # Escaneo inicial
        self.scan_local_files()

    def set_text(self, text_widget, message):
        text_widget.config(state=tk.NORMAL)
        text_widget.delete("1.0", tk.END)
        text_widget.insert(tk.END, message)
        text_widget.config(state=tk.DISABLED)

    # ==========================================
    # GATOTV THREAD
    # ==========================================
    def run_gatotv_thread(self):
        self.btn_gatotv.config(state=tk.DISABLED)
        self.set_text(self.txt_gatotv_status, "Actualizando GatoTV...\nGuardando en gatotv/")
        threading.Thread(target=self._execute_gatotv, daemon=True).start()

    def _execute_gatotv(self):
        try:
            result = subprocess.run(["py", "gatotv.py", "--output-dir", "gatotv"], capture_output=True, text=True, cwd=BASE_DIR)
            if result.returncode == 0:
                self.root.after(0, lambda: self.set_text(self.txt_gatotv_status, "¡GatoTV Actualizado!\nRevisar carpeta gatotv/"))
            else:
                self.root.after(0, lambda: self.set_text(self.txt_gatotv_status, f"Error:\n{result.stderr[-200:]}"))
        except Exception as e:
            self.root.after(0, lambda: self.set_text(self.txt_gatotv_status, f"Excepción:\n{str(e)}"))
        self.root.after(0, lambda: self.btn_gatotv.config(state=tk.NORMAL))
        self.root.after(0, self.scan_local_files)

    # ==========================================
    # MITV THREAD
    # ==========================================
    def run_mitv_thread(self):
        self.btn_mitv.config(state=tk.DISABLED)
        self.set_text(self.txt_mitv_status, "Actualizando MiTV...\nGuardando en mitv/")
        threading.Thread(target=self._execute_mitv, daemon=True).start()

    def _execute_mitv(self):
        try:
            MITV_DIR.mkdir(parents=True, exist_ok=True)
            target_file = MITV_DIR / "mi_tv.xml"
            result = subprocess.run(["py", "mitv.py"], capture_output=True, text=True, cwd=BASE_DIR)
            default_mitv = BASE_DIR / "mi_tv.xml"
            if default_mitv.exists():
                if target_file.exists(): target_file.unlink()
                default_mitv.rename(target_file)

            if result.returncode == 0:
                self.root.after(0, lambda: self.set_text(self.txt_mitv_status, "¡MiTV Actualizado!\nGuardado en mitv/mi_tv.xml"))
            else:
                self.root.after(0, lambda: self.set_text(self.txt_mitv_status, f"Error:\n{result.stderr[-200:]}"))
        except Exception as e:
            self.root.after(0, lambda: self.set_text(self.txt_mitv_status, f"Excepción:\n{str(e)}"))
        self.root.after(0, lambda: self.btn_mitv.config(state=tk.NORMAL))
        self.root.after(0, self.scan_local_files)

    # ==========================================
    # ESCANEAR ARCHIVOS LOCALES
    # ==========================================
    def scan_local_files(self):
        if GATOTV_DIR.exists():
            files = list(GATOTV_DIR.glob("*.xml"))
            self.set_text(self.txt_gatotv_status, f"{len(files)} archivos XML\nen /gatotv")
        else:
            self.set_text(self.txt_gatotv_status, "Carpeta gatotv/ vacía")

        mitv_file = MITV_DIR / "mi_tv.xml"
        if mitv_file.exists():
            self.set_text(self.txt_mitv_status, f"mi_tv.xml presente\n({mitv_file.stat().st_size // 1024} KB)")
        else:
            self.set_text(self.txt_mitv_status, "mi_tv.xml no encontrado")

    # ==========================================
    # UNIFICAR XMLs DE GATOTV Y MITV (Streaming optimizado)
    # ==========================================
    def unify_xmls(self):
        try:
            total_channels = 0
            total_programmes = 0
            folders_to_scan = [GATOTV_DIR, MITV_DIR]

            with open(UNIFIED_XML, "wb") as f_out:
                f_out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
                f_out.write(b'<tv generator-info-name="Unified EPG Manager Master" generator-info-url="https://github.com/antonyhdz">\n')

                for folder in folders_to_scan:
                    if folder.exists():
                        for xml_f in folder.glob("*.xml"):
                            try:
                                context = ET.iterparse(xml_f, events=("end",))
                                for event, elem in context:
                                    if elem.tag in ("channel", "programme"):
                                        elem_str = ET.tostring(elem, encoding="utf-8")
                                        f_out.write(b"  " + elem_str + b"\n")
                                        
                                        if elem.tag == "channel":
                                            total_channels += 1
                                        else:
                                            total_programmes += 1
                                            
                                        elem.clear()
                            except Exception:
                                continue

                f_out.write(b"</tv>\n")

            messagebox.showinfo("Éxito Unificación", f"Archivo unificado optimizado con éxito.\nCanales totales: {total_channels}\nProgramas totales: {total_programmes}")
            self.scan_local_files()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo unificar:\n{str(e)}")

    # ==========================================
    # SUBIR A GITHUB VÍA GIT LOCAL (Método definitivo sin bloqueos)
    # ==========================================
    def upload_to_github_thread(self):
        repo = self.ent_repo.get().strip()
        token = self.ent_token.get().strip()
        path_in_repo = self.ent_path.get().strip()

        if not repo or not path_in_repo:
            messagebox.showwarning("Incompleto", "Ingresa el repositorio y la ruta destino.")
            return

        if not UNIFIED_XML.exists():
            messagebox.showwarning("Falta archivo", "Primero debes generar el archivo unificado.")
            return

        self.btn_upload.config(state=tk.DISABLED)
        threading.Thread(target=self._execute_git_upload, args=(repo, token, path_in_repo), daemon=True).start()

    def _execute_git_upload(self, repo, token, path_in_repo):
        try:
            # 1. Configurar token remoto si existe
            if token:
                remote_url = f"https://{token}@github.com/{repo}.git"
                subprocess.run(["git", "remote", "set-url", "origin", remote_url], cwd=BASE_DIR)

            # 2. Forzar la rama main y limpiar cualquier bloqueo residual de rebase o merge
            subprocess.run(["git", "checkout", "-B", "main"], capture_output=True, text=True, cwd=BASE_DIR)
            rebase_dir = BASE_DIR / ".git" / "rebase-merge"
            if rebase_dir.exists():
                shutil.rmtree(rebase_dir, ignore_errors=True)

            # 3. Copiar el archivo unificado a su destino local exacto
            dest_file = BASE_DIR / path_in_repo
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(UNIFIED_XML, dest_file)

            # 4. Agregar TODO (-A) para que no queden archivos pendientes o modificados sin registrar
            subprocess.run(["git", "add", "-A"], capture_output=True, text=True, cwd=BASE_DIR)

            # 5. Hacer commit (si no hay cambios nuevos, ignora el error y continúa)
            subprocess.run(["git", "commit", "-m", "Actualización automática EPG Unificada"], capture_output=True, text=True, cwd=BASE_DIR)

            # 6. Empujar a GitHub usando push forzado (-f) para evitar cualquier error de divergencia o commits adelantados
            push_process = subprocess.run(["git", "push", "-f", "origin", "main"], capture_output=True, text=True, cwd=BASE_DIR)

            if push_process.returncode != 0:
                error_msg = push_process.stderr or push_process.stdout
                self.root.after(0, lambda: messagebox.showerror("Error Git", f"No se pudo completar la subida:\n{error_msg}"))
            else:
                raw_link = f"https://raw.githubusercontent.com/{repo}/main/{path_in_repo}"
                self.root.after(0, lambda: self.set_text(self.txt_raw_link, raw_link))
                self.root.after(0, lambda: messagebox.showinfo("Éxito", "¡Guía subida a GitHub correctamente!"))

        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Excepción", f"Error ejecutando Git:\n{str(e)}"))
        
        self.root.after(0, lambda: self.btn_upload.config(state=tk.NORMAL))

if __name__ == "__main__":
    root = tk.Tk()
    app = EPGManagerApp(root)
    root.mainloop()