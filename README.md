# Robot recolector de tomates

Panel web del proyecto integrador de la materia optativa de Inteligencia Artificial de la Facultad de Ingeniería de la UNA (FIUNA), desarrollado por Danis González.

## Publicar en GitHub Pages

1. Sube `index.html` y este README a la rama principal del repositorio.
2. En GitHub, abre **Settings → Pages**.
3. En **Build and deployment**, selecciona **Deploy from a branch**, elige la rama principal y la carpeta `/(root)`, y guarda.
4. Abre la URL que GitHub muestra en esa sección. La página se sirve desde `index.html`.

La simulación local calcula la pose con el modelo cinemático y las dimensiones compartidas para el brazo. El modo **Control manual** solo mueve la simulación del navegador: no envía comandos a los motores. El modo **Autónomo** es de solo lectura y muestra la pose, detecciones e imágenes enviadas por el controlador del robot. En **Visión estereoscópica**, selecciona dos cámaras conectadas a la computadora y pulsa **Activar cámaras**; el navegador solicitará permiso. Para que el navegador pueda acceder, abre la página desde `localhost` o HTTPS. Esta vista muestra el video local, pero no calcula detecciones de tomates. Las cámaras del robot y sus detecciones requieren el gateway WebSocket; desde GitHub Pages debe ser `wss://`.

## Formato del flujo de sensores

Envía un objeto JSON por mensaje. `joints_deg` contiene q1–q4 en grados. `target_cm` es opcional y usa coordenadas cartesianas con origen en la base. `tomatoes` contiene las detecciones de visión estéreo con posición en cm, diámetro aproximado y confianza opcional. Las cámaras pueden enviar imágenes JPEG/PNG/WebP como URL `data:image/...;base64,...` o como URL HTTPS.

```json
{
	"joints_deg": [0.0, -12.4, 120.0, -107.6],
	"target_cm": { "x": 50.0, "y": 0.0, "z": 30.0 },
	"tomatoes": [
		{ "id": 1, "x_cm": 52.4, "y_cm": 8.1, "z_cm": 22.7, "diameter_cm": 5.6, "confidence": 0.91 }
	],
	"camera_left": "data:image/jpeg;base64,...",
	"camera_right": "data:image/jpeg;base64,..."
}
```

Introduce la dirección del gateway en **Flujo de sensores por WebSocket** y pulsa **Conectar**. En control manual, selecciona **Usar coordenada** para llevar un tomate detectado al objetivo de la simulación. En modo autónomo la página no permite emitir órdenes: el controlador del robot debe decidir y ejecutar la recolección. Sin gateway no hay telemetría ni detecciones del robot; el video local de las cámaras conectadas a la computadora puede mostrarse por separado.
