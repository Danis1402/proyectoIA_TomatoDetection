import argparse
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
IMAGE_ROOT = ROOT / "calibration_images"
OUTPUT_FILE = ROOT / "stereo_calibration.npz"


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibra el par estéreo del panel de tomates.")
    parser.add_argument("--columns", type=int, default=9, help="Esquinas interiores horizontales del tablero.")
    parser.add_argument("--rows", type=int, default=6, help="Esquinas interiores verticales del tablero.")
    parser.add_argument("--square-mm", type=float, required=True, help="Tamaño real de cada casilla en milímetros.")
    args = parser.parse_args()
    if args.columns < 2 or args.rows < 2 or args.square_mm <= 0:
        parser.error("El tablero debe tener al menos 2x2 esquinas y las casillas deben medir más de cero.")

    pattern = (args.columns, args.rows)
    object_template = np.zeros((args.columns * args.rows, 3), dtype=np.float32)
    object_template[:, :2] = np.mgrid[0:args.columns, 0:args.rows].T.reshape(-1, 2)
    object_template *= args.square_mm

    left_paths = {path.name: path for path in (IMAGE_ROOT / "left").glob("pair_*.jpg")}
    right_paths = {path.name: path for path in (IMAGE_ROOT / "right").glob("pair_*.jpg")}
    pair_names = sorted(left_paths.keys() & right_paths.keys())
    if len(pair_names) < 8:
        raise SystemExit(f"Se necesitan al menos 8 pares; solo hay {len(pair_names)}.")

    object_points = []
    left_points = []
    right_points = []
    image_size = None
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.001)
    for pair_name in pair_names:
        left_image = cv2.imread(str(left_paths[pair_name]))
        right_image = cv2.imread(str(right_paths[pair_name]))
        if left_image is None or right_image is None:
            print(f"Se omite {pair_name}: no se pudo leer una imagen.")
            continue
        if left_image.shape[:2] != right_image.shape[:2]:
            print(f"Se omite {pair_name}: las resoluciones no coinciden.")
            continue
        current_size = (left_image.shape[1], left_image.shape[0])
        if image_size is not None and current_size != image_size:
            print(f"Se omite {pair_name}: la resolución cambia entre pares.")
            continue

        left_gray = cv2.cvtColor(left_image, cv2.COLOR_BGR2GRAY)
        right_gray = cv2.cvtColor(right_image, cv2.COLOR_BGR2GRAY)
        left_found, left_corners = cv2.findChessboardCornersSB(left_gray, pattern)
        right_found, right_corners = cv2.findChessboardCornersSB(right_gray, pattern)
        if not left_found or not right_found:
            print(f"Se omite {pair_name}: no se encontró el tablero en ambas imágenes.")
            continue

        left_corners = cv2.cornerSubPix(left_gray, left_corners, (5, 5), (-1, -1), criteria)
        right_corners = cv2.cornerSubPix(right_gray, right_corners, (5, 5), (-1, -1), criteria)
        object_points.append(object_template.copy())
        left_points.append(left_corners)
        right_points.append(right_corners)
        image_size = current_size

    if len(object_points) < 8 or image_size is None:
        raise SystemExit(f"Solo se detectaron {len(object_points)} pares válidos; se necesitan al menos 8.")

    left_rms, camera_matrix_left, distortion_left, _, _ = cv2.calibrateCamera(
        object_points, left_points, image_size, None, None
    )
    right_rms, camera_matrix_right, distortion_right, _, _ = cv2.calibrateCamera(
        object_points, right_points, image_size, None, None
    )
    stereo_rms, _, _, _, _, rotation, translation, _, _ = cv2.stereoCalibrate(
        object_points,
        left_points,
        right_points,
        camera_matrix_left,
        distortion_left,
        camera_matrix_right,
        distortion_right,
        image_size,
        criteria=criteria,
        flags=cv2.CALIB_FIX_INTRINSIC,
    )
    rectification_left, rectification_right, projection_left, projection_right, q_matrix, _, _ = cv2.stereoRectify(
        camera_matrix_left,
        distortion_left,
        camera_matrix_right,
        distortion_right,
        image_size,
        rotation,
        translation,
        flags=cv2.CALIB_ZERO_DISPARITY,
        alpha=0,
    )
    np.savez_compressed(
        OUTPUT_FILE,
        image_size=np.array(image_size, dtype=np.int32),
        camera_matrix_left=camera_matrix_left,
        distortion_left=distortion_left,
        camera_matrix_right=camera_matrix_right,
        distortion_right=distortion_right,
        rectification_left=rectification_left,
        rectification_right=rectification_right,
        projection_left=projection_left,
        projection_right=projection_right,
        q_matrix=q_matrix,
        rotation=rotation,
        translation=translation,
    )
    baseline_mm = float(np.linalg.norm(translation))
    print(f"Calibración guardada en: {OUTPUT_FILE}")
    print(f"Pares válidos: {len(object_points)} · resolución: {image_size[0]}x{image_size[1]} px")
    print(f"RMS izquierda/derecha/estéreo: {left_rms:.3f} / {right_rms:.3f} / {stereo_rms:.3f} px")
    print(f"Separación estimada entre cámaras: {baseline_mm:.2f} mm")


if __name__ == "__main__":
    main()