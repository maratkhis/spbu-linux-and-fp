import sys
import os
import cv2
import numpy as np
import pandas as pd
import threading
from concurrent.futures import ThreadPoolExecutor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QLineEdit,
    QPushButton, QListWidget, QLabel, QScrollArea, QDialog, QProgressBar,
    QMessageBox, QFileDialog, QSpinBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt5.QtGui import QPixmap, QWheelEvent, QMouseEvent, QDesktopServices


class ZoomableLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom_factor = 1.0
        self.pan_start = None
        self.setMouseTracking(True)
        self.original_pixmap = None
    def setPixmap(self, pixmap):
        self.original_pixmap = pixmap
        self.update_display()
    def update_display(self):
        if not self.original_pixmap:
            return
        scaled = self.original_pixmap.scaled(
            int(self.original_pixmap.width() * self.zoom_factor),
            int(self.original_pixmap.height() * self.zoom_factor),
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        super().setPixmap(scaled)
    def wheelEvent(self, event: QWheelEvent):
        self.zoom_factor = min(3.0, self.zoom_factor * 1.2) if event.angleDelta().y() > 0 else max(0.5, self.zoom_factor / 1.2)
        self.update_display()
        event.accept()
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.pan_start = event.pos()
    def mouseMoveEvent(self, event: QMouseEvent):
        if self.pan_start and event.buttons() & Qt.LeftButton:
            self.pan_start = event.pos()
    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.pan_start = None
    def reset_zoom(self):
        self.zoom_factor = 1.0
        self.update_display()


def _to_uint8(img: np.ndarray) -> np.ndarray:
    if img.dtype == np.uint8:
        return img
    img = img.astype(np.float32)
    mn, mx = float(img.min()), float(img.max())
    if mx <= mn:
        return np.zeros_like(img, dtype=np.uint8)
    return ((img - mn) * (255.0 / (mx - mn))).astype(np.uint8)


def classify_object(area, circ, ar_axis, ar_rot, w, h, mean_int, thr, mean_bgr):
    b, g, r = mean_bgr
    blue_dom = (b > 1.25 * r) and (b > 1.15 * g)
    bright = mean_int >= thr + 8
    if max(w, h) >= 12 and area >= 180 and circ >= 0.80 and ar_rot <= 1.35 and bright:
        return "Planet", (255, 255, 0)
    if ar_rot >= 5.0 and circ <= 0.26 and area >= 80 and max(w, h) >= 20 and (area / (max(w, h) ** 2)) <= 0.33:
        return "Comet", (255, 0, 255)
    if (area < 240 and circ >= 0.52 and ar_rot <= 2.3 and bright) or (blue_dom and circ >= 0.45 and ar_rot <= 3.0 and bright):
        return "Star", (0, 255, 255)
    if area >= 1500 and circ < 0.55 and ar_rot < 3.2 and not blue_dom:
        return "Nebula", (0, 255, 0)
    if area >= 140 and 0.35 < circ < 0.85 and ar_rot < 5.0:
        return "Galaxy", (0, 165, 255)
    return "Galaxy", (0, 165, 255)


def process_tile(tile, tile_index, original_coords, lock, results):
    tile_gray, tile_color = tile
    x_start, y_start, x_end, y_end = original_coords
    g8 = _to_uint8(tile_gray)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    g8 = clahe.apply(g8)
    blur = cv2.GaussianBlur(g8, (3, 3), 0)
    tophat = cv2.morphologyEx(blur, cv2.MORPH_TOPHAT, np.ones((5, 5), np.uint8))
    mix = cv2.addWeighted(blur, 0.65, tophat, 0.35, 0)
    otsu_thr, _ = cv2.threshold(mix, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = min(255, int(otsu_thr) + 4)
    _, binary = cv2.threshold(mix, thr, 255, cv2.THRESH_BINARY)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    MIN_AREA = 10
    tile_results = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < MIN_AREA:
            continue
        perim = cv2.arcLength(cnt, True)
        if perim <= 0:
            continue
        circ = 4.0 * np.pi * area / (perim * perim)
        x, y, w, h = cv2.boundingRect(cnt)
        if w == 0 or h == 0:
            continue
        ar_axis = max(w, h) / float(min(w, h))
        rect = cv2.minAreaRect(cnt)
        rw, rh = rect[1]
        ar_rot = (max(rw, rh) / float(min(rw, rh))) if rw > 0 and rh > 0 else ar_axis
        mask = np.zeros(tile_gray.shape, np.uint8)
        cv2.drawContours(mask, [cnt], -1, 255, -1)
        mean_int = cv2.mean(g8, mask=mask)[0]
        mean_bgr = cv2.mean(tile_color, mask=mask)[:3]
        name, color = classify_object(area, circ, ar_axis, ar_rot, w, h, mean_int, thr, mean_bgr)
        global_bbox = (y_start + y, x_start + x, y_start + y + h, x_start + x + w)
        global_cx = x_start + (x + w / 2.0)
        global_cy = y_start + (y + h / 2.0)
        row = {
            "file": "", "tile_index": int(tile_index), "object_type": name,
            "area": float(area), "circularity": float(circ),
            "aspect_ratio_axis": float(ar_axis), "aspect_ratio_rot": float(ar_rot),
            "width": int(w), "height": int(h), "mean_intensity": float(mean_int),
            "threshold_used": int(thr), "mean_b": float(mean_bgr[0]),
            "mean_g": float(mean_bgr[1]), "mean_r": float(mean_bgr[2]),
            "centroid_x": float(global_cx), "centroid_y": float(global_cy), "bbox": global_bbox
        }
        tile_results.append(row)
        cv2.rectangle(tile_color, (x, y), (x + w, y + h), color, 2)
        cv2.putText(tile_color, name, (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    with lock:
        results.extend(tile_results)
    return tile_color, tile_results


def process_single_image(image_path, num_tiles=4, num_workers=4):
    image_gray = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    image_color = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if image_gray is None or image_color is None:
        return [], None, None
    h, w = image_gray.shape
    base = os.path.splitext(os.path.basename(image_path))[0]
    tiles_dir = os.path.join(os.path.dirname(image_path), "tiles", base)
    os.makedirs(tiles_dir, exist_ok=True)
    tps = int(np.ceil(np.sqrt(max(1, num_tiles))))
    th = h // tps
    tw = w // tps
    tiles, coords = [], []
    for i in range(tps):
        for j in range(tps):
            y0, y1 = i * th, (i + 1) * th if i < tps - 1 else h
            x0, x1 = j * tw, (j + 1) * tw if j < tps - 1 else w
            tg = image_gray[y0:y1, x0:x1]
            tc = image_color[y0:y1, x0:x1].copy()
            tiles.append((tg, tc))
            coords.append((x0, y0, x1, y1))
            cv2.imwrite(os.path.join(tiles_dir, f"tile_{i}_{j}_original.tif"), tc)
    results = []
    lock = threading.Lock()
    processed_tiles = [None] * len(tiles)
    workers = max(1, min(int(num_workers), len(tiles)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(process_tile, t, idx, c, lock, results) for idx, (t, c) in enumerate(zip(tiles, coords))]
        for idx, fut in enumerate(futures):
            pt, _ = fut.result()
            processed_tiles[idx] = pt
            i, j = idx // tps, idx % tps
            cv2.imwrite(os.path.join(tiles_dir, f"tile_{i}_{j}_processed.tif"), pt)
    recon = np.zeros_like(image_color)
    for idx, pt in enumerate(processed_tiles):
        i, j = idx // tps, idx % tps
        y0, y1 = i * th, (i + 1) * th if i < tps - 1 else h
        x0, x1 = j * tw, (j + 1) * tw if j < tps - 1 else w
        recon[y0:y1, x0:x1] = pt
    for r in results:
        r["file"] = os.path.basename(image_path)
    out_dir = os.path.join(os.path.dirname(image_path), "processed")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"processed_{base}.tif")
    cv2.imwrite(out_path, recon)
    info = {"original": image_path, "processed": out_path, "filename": os.path.basename(image_path), "tiles_dir": tiles_dir}
    return results, recon, info


class ImageProcessor(QThread):
    progress_updated = pyqtSignal(int, str)
    processing_finished = pyqtSignal(list)
    def __init__(self, folder, num_tiles=4, num_workers=4):
        super().__init__()
        self.folder = folder
        self.num_tiles = num_tiles
        self.num_workers = num_workers
    def run(self):
        exts = (".tif", ".tiff")
        paths = [os.path.join(self.folder, f) for f in sorted(os.listdir(self.folder)) if f.lower().endswith(exts)]
        if not paths:
            self.processing_finished.emit([])
            return
        total = len(paths)
        all_rows, processed = [], []
        for i, p in enumerate(paths):
            self.progress_updated.emit(int(i / total * 100), f"Processing: {os.path.basename(p)}")
            rows, _, info = process_single_image(p, self.num_tiles, self.num_workers)
            if info:
                processed.append(info)
                all_rows.extend(rows)
            self.progress_updated.emit(int((i + 1) / total * 100), f"Finished: {os.path.basename(p)}")
        if all_rows:
            df = pd.DataFrame(all_rows)
            xlsx = os.path.join(self.folder, "astro_data_stats.xlsx")
            with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="objects", index=False)
        self.processing_finished.emit(processed)


class ImageViewerDialog(QDialog):
    def __init__(self, original_path, processed_path, filename, tiles_dir=None):
        super().__init__()
        self.setWindowTitle(f"Viewer: {filename}")
        self.setGeometry(100, 100, 1400, 700)
        self.tiles_dir = tiles_dir
        main = QVBoxLayout()
        imgs = QHBoxLayout()
        lw = QWidget(); ll = QVBoxLayout(lw)
        ll.addWidget(QLabel("Original image"))
        self.orig = ZoomableLabel(); self.orig.setAlignment(Qt.AlignCenter); self.orig.setStyleSheet("border: 1px solid gray;")
        so = QScrollArea(); so.setWidget(self.orig); so.setWidgetResizable(True); ll.addWidget(so)
        rw = QWidget(); rl = QVBoxLayout(rw)
        rl.addWidget(QLabel("Processed image"))
        self.proc = ZoomableLabel(); self.proc.setAlignment(Qt.AlignCenter); self.proc.setStyleSheet("border: 1px solid gray;")
        sp = QScrollArea(); sp.setWidget(self.proc); sp.setWidgetResizable(True); rl.addWidget(sp)
        imgs.addWidget(lw); imgs.addWidget(rw); main.addLayout(imgs)
        ctr = QHBoxLayout()
        b1 = QPushButton("Reset zoom (original)"); b1.clicked.connect(self.orig.reset_zoom)
        b2 = QPushButton("Reset zoom (processed)"); b2.clicked.connect(self.proc.reset_zoom)
        ctr.addWidget(b1); ctr.addWidget(b2); ctr.addStretch()
        if self.tiles_dir and os.path.exists(self.tiles_dir):
            bt = QPushButton("Open tiles folder"); bt.clicked.connect(self.show_tiles); ctr.addWidget(bt)
        main.addLayout(ctr)
        self.setLayout(main)
        self.load_images(original_path, processed_path)
    def load_images(self, original_path, processed_path):
        p = QPixmap(original_path);  self.orig.setPixmap(p) if not p.isNull() else None
        p2 = QPixmap(processed_path); self.proc.setPixmap(p2) if not p2.isNull() else None
    def show_tiles(self):
        if self.tiles_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.tiles_dir))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Parallel Analyzer for Large Astro TIFs")
        self.setGeometry(100, 100, 980, 740)
        central = QWidget(); self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        s = QHBoxLayout()
        s.addWidget(QLabel("Tiles:")); self.tiles = QSpinBox(); self.tiles.setRange(1, 64); self.tiles.setValue(4); s.addWidget(self.tiles)
        s.addWidget(QLabel("Threads:")); self.threads = QSpinBox(); self.threads.setRange(1, max(1, os.cpu_count() or 4)); self.threads.setValue(min(8, max(1, os.cpu_count() or 4))); s.addWidget(self.threads)
        s.addStretch()
        f = QHBoxLayout()
        self.folder = QLineEdit(); self.folder.setPlaceholderText("Select a folder with .tif images...")
        self.browse = QPushButton("Browse"); self.browse.clicked.connect(self.browse_folder)
        self.runbtn = QPushButton("Process"); self.runbtn.clicked.connect(self.process_images); self.runbtn.setEnabled(False)
        f.addWidget(self.folder); f.addWidget(self.browse); f.addWidget(self.runbtn)
        self.pbar = QProgressBar(); self.pbar.setVisible(False)
        self.plabel = QLabel(""); self.plabel.setVisible(False)
        self.list = QListWidget(); self.list.itemDoubleClicked.connect(self.show_image)
        layout.addLayout(s); layout.addLayout(f); layout.addWidget(self.pbar); layout.addWidget(self.plabel); layout.addWidget(QLabel("Processed images:")); layout.addWidget(self.list)
        self.setStyleSheet("QMainWindow{background:#f0f0f0;} QLineEdit{padding:8px;border:1px solid #ccc;border-radius:4px;background:white;} QPushButton{padding:8px 16px;border:1px solid #0078d4;border-radius:4px;background:#0078d4;color:white;} QPushButton:hover{background:#106ebe;} QPushButton:disabled{background:#ccc;border-color:#ccc;} QListWidget{border:1px solid #ccc;border-radius:4px;background:white;} QProgressBar{border:1px solid #ccc;border-radius:4px;text-align:center;} QProgressBar::chunk{background:#0078d4;}")
        self.proc_imgs = []; self.worker = None
    def browse_folder(self):
        cur = os.path.dirname(os.path.abspath(__file__)); imgs = os.path.join(cur, "imgs")
        folder = QFileDialog.getExistingDirectory(self, "Select images folder", imgs) if os.path.exists(imgs) else QFileDialog.getExistingDirectory(self, "Select images folder")
        if folder: self.folder.setText(folder); self.runbtn.setEnabled(True)
    def process_images(self):
        path = self.folder.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Error", "Folder does not exist!"); return
        self.list.clear(); self.proc_imgs = []; self.pbar.setVisible(True); self.plabel.setVisible(True); self.pbar.setValue(0); self.runbtn.setEnabled(False); self.browse.setEnabled(False)
        self.worker = ImageProcessor(path, self.tiles.value(), self.threads.value())
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.processing_finished.connect(self.on_done)
        self.worker.start()
    def update_progress(self, v, t): self.pbar.setValue(v); self.plabel.setText(t)
    def on_done(self, imgs):
        self.pbar.setVisible(False); self.plabel.setVisible(False); self.runbtn.setEnabled(True); self.browse.setEnabled(True); self.proc_imgs = imgs
        if imgs:
            for info in imgs: self.list.addItem(info["filename"])
            QMessageBox.information(self, "Success", f"Processed {len(imgs)} image(s). Tiles saved under 'tiles'.")
        else:
            QMessageBox.warning(self, "Warning", "No .tif images found to process!")
    def show_image(self, item):
        name = item.text()
        for img in self.proc_imgs:
            if img["filename"] == name:
                ImageViewerDialog(img["original"], img["processed"], name, img.get("tiles_dir")).exec_()
                break


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
