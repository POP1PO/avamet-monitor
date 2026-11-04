# frontend/vendor — librerías del navegador (sin internet)

La interfaz usa **Leaflet** (mapa) y **Chart.js** (gráficas). Para que funcionen sin internet (Raspberry Pi en el
observatorio) deben estar en esta carpeta:

```
vendor/leaflet/leaflet.js
vendor/leaflet/leaflet.css
vendor/leaflet/images/{layers,layers-2x,marker-icon,marker-icon-2x,marker-shadow}.png
vendor/chart/chart.umd.js
```

## Cómo se rellena

Desde la carpeta del proyecto, **en un PC con internet** (una sola vez):

```
python scripts/descargar_vendor.py
```

Descarga las versiones fijadas (Leaflet 1.9.4, Chart.js 4.4.1), comprueba cada fichero y escribe `VERSIONS.txt` con
las huellas SHA-256. Después **commitea esta carpeta** y cópiala a la Raspberry Pi junto con el resto de la aplicación.

Comprobar qué falta sin descargar: `python scripts/descargar_vendor.py --solo-comprobar`.

## Si no está

La app sigue funcionando, pero `index.html` recurre al CDN (unpkg / jsdelivr): sin internet no habría mapa ni gráficas.
Al arrancar, la consola avisa de qué ficheros faltan.

## Mapa sin internet

Estas carpetas solo contienen el **código** del mapa. Las **teselas** (el dibujo del terreno) las sirve ArcGIS Online
por internet; sin conexión se ven los marcadores y círculos sobre fondo vacío.

## Licencias

Leaflet: BSD-2-Clause. Chart.js: MIT. El script descarga también sus ficheros de licencia.
