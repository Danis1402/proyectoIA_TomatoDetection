import asyncio
import base64
import binascii
import json
from pathlib import Path

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse


ROOT = Path(__file__).resolve().parent
CALIBRATION_FILE = ROOT / "stereo_calibration.npz"
CALIBRATION_IMAGES = ROOT / "calibration_images"
app = FastAPI(title="Tomato Stereo Vision API")


def load_calibration() -> dict | None:
    if not CALIBRATION_FILE.exists():
        return None
    with np.load(CALIBRATION_FILE) as data:
        width, height = (int(value) for value in data["image_size"])
        left_map_x, left_map_y = cv2.initUndistortRectifyMap(
            data["camera_matrix_left"],
            data["distortion_left"],
            data["rectification_left"],
            data["projection_left"],
            (width, height),
            cv2.CV_32FC1,
        )
        right_map_x, right_map_y = cv2.initUndistortRectifyMap(
            data["camera_matrix_right"],
            data["distortion_right"],
            data["rectification_right"],
            data["projection_right"],
            (width, height),
            cv2.CV_32FC1,
        )
        return {
            "image_size": (width, height),
            "q_matrix": data["q_matrix"].copy(),
            "focal_length_px": float(data["projection_left"][0, 0]),
            "left_map": (left_map_x, left_map_y),
            "right_map": (right_map_x, right_map_y),
        }


def decode_frame(data_url: str) -> np.ndarray:
    _, separator, encoded = data_url.partition(",")
    if not separator:
        raise ValueError("El cuadro debe ser una imagen JPEG codificada en base64.")
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except binascii.Error as error:
        raise ValueError("La imagen recibida no tiene base64 válido.") from error
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("OpenCV no pudo decodificar la imagen recibida.")
    return image


def detect_tomatoes(image: np.ndarray) -> list[dict]:
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    red_low = cv2.inRange(hsv, np.array([0, 65, 45]), np.array([12, 255, 255]))
    red_high = cv2.inRange(hsv, np.array([168, 65, 45]), np.array([179, 255, 255]))
    mask = cv2.bitwise_or(red_low, red_high)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    minimum_area = max(80, width * height * 0.00008)
    detections = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < minimum_area or area > width * height * 0.2:
            continue
        x, y, box_width, box_height = cv2.boundingRect(contour)
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            continue
        detections.append({
            "bbox": [int(x), int(y), int(box_width), int(box_height)],
            "center_px": [moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]],
            "area_px": float(area),
            "diameter_px": float(np.sqrt(4 * area / np.pi)),
        })
    return sorted(detections, key=lambda item: item["area_px"], reverse=True)[:20]


def process_pair(left: np.ndarray, right: np.ndarray, calibration: dict | None) -> dict:
    height, width = left.shape[:2]
    calibration_matches = calibration is not None and calibration["image_size"] == (width, height)
    if calibration_matches:
        left = cv2.remap(left, *calibration["left_map"], cv2.INTER_LINEAR)
        right = cv2.remap(right, *calibration["right_map"], cv2.INTER_LINEAR)

    left_detections = detect_tomatoes(left)
    right_detections = detect_tomatoes(right)
    available_right = set(range(len(right_detections)))
    tomatoes = []
    next_id = 1
    max_vertical_error = max(12, height * (0.04 if calibration_matches else 0.08))

    for left_detection in left_detections:
        left_x, left_y = left_detection["center_px"]
        best_match = None
        best_score = float("inf")
        for right_index in available_right:
            right_detection = right_detections[right_index]
            right_x, right_y = right_detection["center_px"]
            disparity = left_x - right_x
            vertical_error = abs(left_y - right_y)
            area_ratio = left_detection["area_px"] / right_detection["area_px"]
            if disparity <= 0 or vertical_error > max_vertical_error or not 0.35 <= area_ratio <= 2.85:
                continue
            score = vertical_error + 12 * abs(np.log(area_ratio))
            if score < best_score:
                best_match = (right_index, right_x, right_y, disparity)
                best_score = score
        tomato = {
            "id": next_id,
            "x_px": round(left_x, 1),
            "y_px": round(left_y, 1),
            "diameter_px": round(left_detection["diameter_px"], 1),
        }
        next_id += 1
        if best_match is None:
            tomatoes.append(tomato)
            continue

        right_index, right_x, right_y, disparity = best_match
        available_right.remove(right_index)
        tomato["disparity_px"] = round(disparity, 2)

        if calibration_matches:
            point = np.array([[[left_x, (left_y + right_y) / 2, disparity]]], dtype=np.float32)
            xyz_mm = cv2.perspectiveTransform(point, calibration["q_matrix"])[0, 0]
            if np.isfinite(xyz_mm).all() and xyz_mm[2] > 0:
                tomato.update({
                    "x_cm": round(float(xyz_mm[0] / 10), 2),
                    "y_cm": round(float(xyz_mm[1] / 10), 2),
                    "z_cm": round(float(xyz_mm[2] / 10), 2),
                    "diameter_cm": round(float(left_detection["diameter_px"] * xyz_mm[2] / calibration["focal_length_px"] / 10), 2),
                })
        tomatoes.append(tomato)

    if calibration is None:
        status = "Detección activa; falta calibrar el par estéreo para obtener profundidad."
    elif not calibration_matches:
        status = f"La calibración requiere {calibration['image_size'][0]}x{calibration['image_size'][1]} px; el flujo usa {width}x{height} px."
    else:
        status = "Detección y profundidad estéreo activas; coordenadas relativas a la cámara izquierda."

    return {
        "type": "detections",
        "calibrated": calibration_matches,
        "status": status,
        "left_detections": left_detections,
        "right_detections": right_detections,
        "tomatoes": tomatoes,
    }


def save_calibration_pair(left: np.ndarray, right: np.ndarray) -> int:
    left_dir = CALIBRATION_IMAGES / "left"
    right_dir = CALIBRATION_IMAGES / "right"
    left_dir.mkdir(parents=True, exist_ok=True)
    right_dir.mkdir(parents=True, exist_ok=True)
    pair_number = len(list(left_dir.glob("pair_*.jpg"))) + 1
    left_path = left_dir / f"pair_{pair_number:03d}.jpg"
    right_path = right_dir / f"pair_{pair_number:03d}.jpg"
    if not cv2.imwrite(str(left_path), left) or not cv2.imwrite(str(right_path), right):
        left_path.unlink(missing_ok=True)
        right_path.unlink(missing_ok=True)
        raise OSError("No se pudo guardar el par de imágenes de calibración.")
    return pair_number


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True, "calibrated": CALIBRATION_FILE.exists()}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(ROOT / "index.html")


@app.websocket("/ws")
async def vision_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    calibration = load_calibration()
    last_pair: tuple[np.ndarray, np.ndarray] | None = None
    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "capture_calibration":
                if last_pair is None:
                    await websocket.send_json({"type": "error", "status": "Espera a que llegue el primer par de imágenes."})
                    continue
                pair_number = await asyncio.to_thread(save_calibration_pair, *last_pair)
                await websocket.send_json({"type": "calibration_saved", "pair": pair_number})
                continue

            if message.get("type") != "frames":
                await websocket.send_json({"type": "error", "status": "Tipo de mensaje no reconocido."})
                continue
            try:
                left = decode_frame(message["left"])
                right = decode_frame(message["right"])
                if left.shape[:2] != right.shape[:2]:
                    raise ValueError("Los cuadros izquierdo y derecho deben tener la misma resolución.")
                last_pair = (left, right)
                result = await asyncio.to_thread(process_pair, left, right, calibration)
                await websocket.send_json(result)
            except (KeyError, TypeError, ValueError) as error:
                await websocket.send_json({"type": "error", "status": str(error)})
    except WebSocketDisconnect:
        return


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)