# AVAMET Monitor (escritorio)

Aplicación local (Python + FastAPI) para vigilar el tiempo en un observatorio o un lugar de observación en la Comunitat Valenciana con datos públicos de
**AVAMET** (estaciones) y **METAR** de aeropuertos (aviationweather.gov). Calcula un estado **SEGURO / VIGILANCIA / PELIGRO**, estima hacia dónde se mueven las
nubes, avisa de lluvia en camino y puede exponer el estado a **N.I.N.A.** (Safety Monitor ASCOM Alpaca) y a un fichero **Boltwood**. Se maneja desde el navegador
en `http://127.0.0.1:8000`. Hay una app Android hermana: [AvametAviso](https://github.com/POP1PO/AvametAviso).

## Qué hace
- **Estación central + estaciones cercanas** (cada minuto / cada 3 minutos) y **METAR** de aeropuertos hasta 120 km.
- **Estación de respaldo**: si la principal deja de enviar datos, usa la más cercana que sí los envíe y vuelve a la principal cuando recupera varias lecturas.
- **Alertas** de lluvia, viento y racha, con retención configurable; **radar predictivo** con el viento (estación + aeropuertos) y tiempo estimado de llegada.
- **Mapa** (Leaflet) con estaciones, aeropuertos y dirección de las nubes, más la capa **«Nubes IR»** de Meteosat (EUMETView, sin claves) con **animación** de las
  últimas 2 h (botón ▶).
- **Satélite en modo sombra** (opcional, necesita cuenta de EUMETSAT): compara la temperatura de brillo IR con lo que dicen las estaciones; solo informa, no
  interviene en la seguridad.
- **Telegram** (opcional): bot con contraseña, avisos y suscriptores. **Boltwood / Alpaca** para N.I.N.A. Historial en SQLite. Diagnóstico de red con histórico diario.
- Se apaga con el botón ⏻ (protegido por un token local) dejando el fichero Boltwood en «no seguro».

## Descargar (sin instalar Python)
En **Releases → «Última versión»** (se actualiza sola con cada cambio en `main`):

| Sistema | Archivo | Uso |
|---|---|---|
| Windows 10/11 (x64) | `AVAMET_Monitor-windows-x64.zip` | Descomprime y ejecuta `AVAMET_Monitor.exe`. Abre el navegador solo. |
| Linux x86_64 | `AVAMET_Monitor-linux-x86_64.tar.gz` | `tar xzf …; ./AVAMET_Monitor` (o `--headless` en un servidor). Incluye una unidad `systemd` de ejemplo. |

El binario de Linux se compila en Ubuntu 22.04 (glibc 2.35): debería funcionar en distribuciones de esa época o más nuevas; **no lo he probado en cada
distribución**. Raspberry Pi / ARM: no hay binario; usa la instalación desde código (`INSTALACION_RPI.md`).
Windows puede mostrar el aviso de SmartScreen porque el `.exe` no está firmado digitalmente.

## Desde el código
    python -m pip install -r requirements.txt            # y, opcional, requirements-satelite.txt
    python run.py                                        # escritorio: abre el navegador
    python run.py --headless                             # servidor: sin navegador, escucha en 0.0.0.0

Configuración: copia `.env.example` a `.env` (Telegram, contraseñas, rutas…). **El `.env`, `api_token.txt`, la base de datos y los registros no se suben nunca**
(están en `.gitignore`). Ubicación por defecto y estaciones: ver `MANUAL_TECNICO.md`; la ubicación se cambia desde la propia app.

## Documentación
- `MANUAL_TECNICO.md` — arquitectura, fuentes de datos, seguridad, fallos, límites conocidos.
- `GUIA_NINA.md` — conexión con N.I.N.A. · `INSTALACION_RPI.md` — Raspberry Pi · `DISTRIBUCION_WINDOWS.md` — compilar el `.exe` a mano.

## Compilación automática
`.github/workflows/build.yml`: ejecuta las pruebas (`tests/`), compila con PyInstaller el `.exe` (Windows) y el binario (Linux), en Linux comprueba que el
binario arranca y responde, y publica ambos en Releases. Para compilar a mano: `python -m PyInstaller --clean AVAMET_Monitor.spec`.

## Avisos honestos
- Es un proyecto personal de aficionados. **Los umbrales de seguridad son un criterio propio, no un estudio validado**: no sustituye a un sensor de lluvia/viento
  propio ni a un sensor de nubes real al decidir cerrar o no un techo.
- Los datos de AVAMET y de aviationweather.gov se leen de sus webs/APIs públicas; **no he podido confirmar las condiciones de uso de AVAMET** para este uso.
  `coordenadas.json` es una copia local del catálogo público de estaciones de AVAMET.
- La capa de nubes IR y la animación usan el WMS público de EUMETView (© EUMETSAT); la imagen llega con ~25–30 min de retraso y no es una medida de temperatura.

## Datos y licencias
- **Capa «Nubes IR»** (sin claves): muestra imágenes del visor público EUMETView de EUMETSAT. Solo se muestran, con la atribución «© EUMETSAT Meteosat data» y el año; no se guardan ni se redistribuyen datos numéricos.
- **Satélite en modo sombra** (opcional, desactivado por defecto): descarga datos Meteosat con la cuenta y las claves de cada usuario. Los datos recientes (menos de 1 hora) pueden requerir licencia o tasas y el uso operacional o comercial puede estar restringido. Antes de activarlo, lee la [Data Policy de EUMETSAT](https://www-cdn.eumetsat.int/files/2026-01/45173%20-%20Data_Policy.pdf) y la página de [registro y licencias](https://user.eumetsat.int/resources/user-guides/data-registration-and-licensing). Este proyecto no puede darte una licencia ni decidir si tu uso está permitido.
- Estaciones: AVAMET; METAR: aviationweather.gov; cada fuente con sus propias condiciones.

---

Creado por Roberto Ferrando.
