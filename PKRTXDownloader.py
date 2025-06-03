import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import cloudscraper       # to bypass Cloudflare
import re
import os
import shutil
import tempfile
import zipfile
import py7zr               # to extract .7z files
import threading
import certifi
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

# ——————————————————————————————————————————————————————
# STEP 1: FILL IN THESE URLS
#
# Replace each placeholder URL below with the actual ModDB “start download” URL.
# Example start URL: https://www.moddb.com/downloads/start/289043
#
# You need:
#   • One archive for the Base (contains Bin/, Data/, and HUD FIX/ subfolders)
#   • One archive per Chapter (Bin + Data, except Chapter 3 may only have Bin)
#
MOD_ZIP_URLS = {
    "Base":      "https://www.moddb.com/downloads/start/289043",  # ← Replace with your Base .7z or .zip start URL
    "Chapter 1": "https://www.moddb.com/downloads/start/289067",  # ← Replace
    "Chapter 2": "https://www.moddb.com/downloads/start/289074",  # ← Replace
    "Chapter 3": "https://www.moddb.com/downloads/start/289047",  # ← Replace
    "Chapter 4": "https://www.moddb.com/downloads/start/289053",  # ← Replace
    "Chapter 5": "https://www.moddb.com/downloads/start/289049",  # ← Replace
    "Chapter 6": "https://www.moddb.com/downloads/start/289069",  # ← Replace
}

# HUD resolutions (match folder names under “HUD FIX” exactly)
HUD_RESOLUTIONS = [
    "1920x1080",
    "2560x1080",
    "2560x1440",
    "3440x1440",
    "3840x1440",
    "3840x2160",
]

# Common headers to emulate a browser
COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.moddb.com/",
}

class PKRTXDownloader:
    def __init__(self, root):
        self.root = root
        self.root.title("Painkiller RTX Mod Downloader (ModDB)")
        self.root.geometry("480x650")
        self.root.resizable(False, False)

        self.install_dir = None
        self.scraper = cloudscraper.create_scraper()  # to bypass Cloudflare
        self.cancel_event = threading.Event()
        self.download_thread = None

        # Chapter selection frame
        frame_ch = tk.LabelFrame(root, text="Select Chapters to Install", padx=10, pady=10)
        frame_ch.pack(fill="x", padx=20, pady=(20, 10))

        self.part_vars = {}
        for part in ["Base", "Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4", "Chapter 5", "Chapter 6"]:
            var = tk.IntVar()
            chk = tk.Checkbutton(frame_ch, text=part, variable=var, command=self.toggle_resolution)
            chk.pack(anchor="w")
            self.part_vars[part] = var

        # HUD resolution frame (only for Base)
        frame_res = tk.LabelFrame(root, text="Select HUD Resolution (Base only)", padx=10, pady=10)
        frame_res.pack(fill="x", padx=20, pady=(10, 10))
        self.res_var = tk.StringVar()
        self.res_combo = ttk.Combobox(
            frame_res, values=HUD_RESOLUTIONS, textvariable=self.res_var, state="disabled", width=20
        )
        self.res_combo.pack(pady=5)
        self.res_combo.current(0)

        # Install folder selection
        frame_install = tk.Frame(root)
        frame_install.pack(fill="x", padx=20, pady=(10, 20))
        self.install_label = tk.Label(frame_install, text="No game installation folder selected.")
        self.install_label.pack(side="left", fill="x", expand=True)
        btn_browse = tk.Button(frame_install, text="Select Game Folder", command=self.select_install_folder)
        btn_browse.pack(side="right")

        # Download and Cancel buttons
        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=(0, 10))
        self.download_btn = tk.Button(btn_frame, text="Download & Install", command=self.start_download)
        self.download_btn.pack(side="left", padx=(0, 10))
        self.cancel_btn = tk.Button(btn_frame, text="Cancel", command=self.cancel_download, state="disabled")
        self.cancel_btn.pack(side="left")

        # Filename/percentage label
        self.status_label = tk.Label(root, text="", fg="#333333")
        self.status_label.pack(pady=(5, 0))

        # Progress bar
        self.progress = ttk.Progressbar(root, orient="horizontal", length=440, mode="determinate")
        self.progress.pack(pady=(5, 20))

        # Note label
        note = (
            "Note:\n"
            "- Archives can be large (the Full mod is about 50GB). Ensure enough disk space.\n"
            "- If you have a slow connection, be patient.\n"
            "- If you find it stuck at 100%, it's NOT stuck. It's extracting the archives. Just wait a bit."
        )
        tk.Label(root, text=note, justify="left", wraplength=440, fg="#555555").pack(padx=20)

    def toggle_resolution(self):
        # Enable HUD resolution only if Base is selected
        if self.part_vars["Base"].get() == 1:
            self.res_combo.configure(state="readonly")
        else:
            self.res_combo.configure(state="disabled")

    def select_install_folder(self):
        folder = filedialog.askdirectory(title="Select Game Installation Folder")
        if folder:
            self.install_dir = folder
            self.install_label.config(text=folder)

    def start_download(self):
        # Disable download button, enable cancel
        self.download_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.cancel_event.clear()
        # Start background thread
        self.download_thread = threading.Thread(target=self.download_and_install, daemon=True)
        self.download_thread.start()

    def cancel_download(self):
        self.cancel_event.set()
        self._update_status("Cancelling...", 0)

    def download_and_install(self):
        selected_parts = [p for p, v in self.part_vars.items() if v.get() == 1]
        if not selected_parts:
            self._finish_with_message("Error: Select at least one part (Base or Chapter).", error=True)
            return

        if "Base" in selected_parts:
            resolution = self.res_var.get()
            if not resolution:
                self._finish_with_message("Error: Select a HUD resolution for Base.", error=True)
                return
        else:
            resolution = None

        if not self.install_dir:
            self._finish_with_message("Error: Select your game installation folder.", error=True)
            return

        tasks = []
        if "Base" in selected_parts:
            base_url = MOD_ZIP_URLS.get("Base")
            if not base_url:
                self._finish_with_message("Config Error: Set the Base archive URL in script.", error=True)
                return
            tasks.append(("Base", base_url))
        for chap in [f"Chapter {i}" for i in range(1, 7)]:
            if chap in selected_parts:
                chap_url = MOD_ZIP_URLS.get(chap)
                if not chap_url:
                    self._finish_with_message(f"Config Error: Set URL for {chap} archive in script.", error=True)
                    return
                tasks.append((chap, chap_url))

        temp_dir = tempfile.mkdtemp(prefix="pk_mod_")
        total_tasks = len(tasks)

        for idx, (label, start_url) in enumerate(tasks, start=1):
            if self.cancel_event.is_set():
                break
            self._update_status(f"Resolving {label}...", int(((idx - 1) / total_tasks) * 100))
            try:
                self._download_extract_merge(label, start_url, resolution, temp_dir)
            except Exception as e:
                self._finish_with_message(f"Error ({label}): {e}", error=True)
                try: shutil.rmtree(temp_dir)
                except: pass
                return
            # Update overall progress after each part
            progress_percent = int((idx / total_tasks) * 100)
            self._update_status(f"Completed {label}", progress_percent)

        try:
            shutil.rmtree(temp_dir)
        except:
            pass

        if self.cancel_event.is_set():
            self._finish_with_message("Download canceled.", error=False)
        else:
            self._finish_with_message("All selected parts installed successfully.", error=False)

    def _update_status(self, message: str, progress_percent: int):
        """
        Schedule an update of the status label and progress bar on the main thread.
        """
        self.root.after(0, lambda: self.status_label.config(text=message))
        self.root.after(0, lambda: self.progress.config(value=progress_percent))

    def _finish_with_message(self, msg: str, error: bool = False):
        """
        Re-enable buttons, reset progress, and show a final messagebox.
        """
        def finish():
            self.download_btn.config(state="normal")
            self.cancel_btn.config(state="disabled")
            self.status_label.config(text="")
            self.progress.config(value=0)
            if error:
                messagebox.showerror("Downloader", msg)
            else:
                messagebox.showinfo("Downloader", msg)
        self.root.after(0, finish)

    def _resolve_moddb_url(self, start_url: str) -> str:
        """
        Fetch the ModDB start page, find a mirror link, and follow redirects to get the direct .7z or .zip link.
        """
        resp = self.scraper.get(start_url, headers=COMMON_HEADERS)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Find mirror links with regex: /downloads/mirror/<id>/<server>/<hash>
        mirror_links = soup.find_all('a', href=re.compile(r'/downloads/mirror/\d+/\d+/[a-f0-9]+'))
        if not mirror_links:
            # Fallback: try the "/all" mirrors page
            resp2 = self.scraper.get(start_url + "/all", headers=COMMON_HEADERS)
            resp2.raise_for_status()
            soup2 = BeautifulSoup(resp2.text, 'html.parser')
            mirror_links = soup2.find_all('a', href=re.compile(r'/downloads/mirror/\d+/\d+/[a-f0-9]+'))

        if not mirror_links:
            raise Exception("Could not find mirror links on ModDB page.")

        mirror_href = mirror_links[0]['href']
        mirror_url = mirror_href if mirror_href.startswith('http') else urljoin("https://www.moddb.com", mirror_href)

        current_url = mirror_url
        max_redirects = 10

        for _ in range(max_redirects):
            if self.cancel_event.is_set():
                raise Exception("Canceled by user")
            resp = self.scraper.get(
                current_url,
                headers={**COMMON_HEADERS, "Referer": start_url},
                allow_redirects=False
            )

            if resp.status_code in (301, 302) and 'Location' in resp.headers:
                location = resp.headers['Location']
                parsed = urlparse(location)
                if parsed.path.lower().endswith('.zip') or parsed.path.lower().endswith('.7z'):
                    return location
                current_url = urljoin(current_url, location)
            else:
                parsed = urlparse(current_url)
                if parsed.path.lower().endswith('.zip') or parsed.path.lower().endswith('.7z'):
                    return current_url
                else:
                    raise Exception(f"Final URL does not point to a .7z or .zip file: {current_url}")

        raise Exception("Too many redirects without finding a .7z or .zip file.")

    def _download_extract_merge(self, label: str, start_url: str, resolution: str, temp_dir: str):
        real_url = self._resolve_moddb_url(start_url)

        filename = os.path.basename(urlparse(real_url).path)
        self._update_status(f"Downloading {label}: 0%", 0)

        temp_archive = os.path.join(temp_dir, f"{label}.archive")
        self._download_file(real_url, temp_archive, label)

        extract_to = os.path.join(temp_dir, f"{label}_extracted")
        os.makedirs(extract_to, exist_ok=True)

        parsed = urlparse(real_url)
        if parsed.path.lower().endswith('.7z'):
            with py7zr.SevenZipFile(temp_archive, mode='r') as zf:
                zf.extractall(extract_to)
        else:
            with zipfile.ZipFile(temp_archive, "r") as zf:
                zf.extractall(extract_to)

        entries = os.listdir(extract_to)
        if len(entries) == 1 and os.path.isdir(os.path.join(extract_to, entries[0])):
            root_folder = os.path.join(extract_to, entries[0])
        else:
            root_folder = extract_to

        # Merge into game folder—Chapter 3 has no Data, so only Bin
        if label == "Base":
            self._merge(root_folder, "Bin")
            self._merge(root_folder, "Data")

            hud_path = os.path.join(root_folder, "HUD FIX", resolution)
            if not os.path.isdir(hud_path):
                raise Exception(f"HUD FIX/{resolution} not found in Base archive.")
            dest_bin = os.path.join(self.install_dir, "Bin")
            os.makedirs(dest_bin, exist_ok=True)
            for fname in os.listdir(hud_path):
                shutil.copy2(os.path.join(hud_path, fname), os.path.join(dest_bin, fname))
        else:
            # Find and merge any "Bin" subfolder under root_folder
            bin_src = self._find_subdir(root_folder, "Bin")
            if bin_src:
                self._merge_custom(bin_src, os.path.join(self.install_dir, "Bin"))
            # Find and merge any "Data" subfolder if it exists
            data_src = self._find_subdir(root_folder, "Data")
            if data_src:
                self._merge_custom(data_src, os.path.join(self.install_dir, "Data"))

    def _find_subdir(self, root: str, subname: str) -> str:
        """
        Recursively search for the first directory named `subname` under `root`.
        Return its full path, or None if not found.
        """
        for dirpath, dirnames, _ in os.walk(root):
            if os.path.basename(dirpath).lower() == subname.lower():
                return dirpath
        return None

    def _download_file(self, url: str, output_path: str, label: str):
        """
        Download a file from `url` to `output_path`, streaming in chunks.
        Raises Exception if HTTP status != 200 or if size mismatches.
        """
        CHUNK_SIZE = 1024 * 1024
        r = self.scraper.get(url, headers=COMMON_HEADERS, stream=True)
        if r.status_code != 200:
            raise Exception(f"Failed to download {label}: HTTP {r.status_code}")

        total_size = int(r.headers.get("Content-Length", 0))
        downloaded = 0

        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                if self.cancel_event.is_set():
                    raise Exception("Canceled by user")
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size:
                        percent = int((downloaded / total_size) * 100)
                        self._update_status(f"Downloading {label}: {percent}%", percent)

        if total_size and downloaded != total_size:
            raise Exception(f"Size mismatch for {label} ({downloaded}/{total_size} bytes)." )

    def _merge(self, root_folder: str, subfolder: str):
        """
        Copy everything from `root_folder/subfolder` into `install_dir/subfolder`,
        creating folders if needed, and merging (overwriting) files.
        """
        src_dir = os.path.join(root_folder, subfolder)
        if not os.path.isdir(src_dir):
            return

        dst = os.path.join(self.install_dir, subfolder)
        os.makedirs(dst, exist_ok=True)
        for dirpath, _, files in os.walk(src_dir):
            if self.cancel_event.is_set():
                return
            rel = os.path.relpath(dirpath, src_dir)
            tgt_dir = os.path.join(dst, rel) if rel != "." else dst
            os.makedirs(tgt_dir, exist_ok=True)
            for fn in files:
                try:
                    shutil.copy2(os.path.join(dirpath, fn), os.path.join(tgt_dir, fn))
                except Exception as ex:
                    print(f"Warn: could not copy {fn}: {ex}")

    def _merge_custom(self, src_folder: str, dst_folder: str):
        """
        Copy everything from `src_folder` into `dst_folder`,
        creating folders if needed, and merging (overwriting) files.
        """
        os.makedirs(dst_folder, exist_ok=True)
        for dirpath, _, files in os.walk(src_folder):
            if self.cancel_event.is_set():
                return
            rel = os.path.relpath(dirpath, src_folder)
            tgt_dir = os.path.join(dst_folder, rel) if rel != "." else dst_folder
            os.makedirs(tgt_dir, exist_ok=True)
            for fn in files:
                try:
                    shutil.copy2(os.path.join(dirpath, fn), os.path.join(tgt_dir, fn))
                except Exception as ex:
                    print(f"Warn: could not copy {fn}: {ex}")

if __name__ == "__main__":
    root = tk.Tk()
    app = PKRTXDownloader(root)
    root.mainloop()
