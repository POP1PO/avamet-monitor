# AVAMET Monitor en Raspberry Pi (headless, 24/7)

Guía para instalar AVAMET Monitor como servicio en **Raspberry Pi OS (Bookworm)**, sin pantalla ni navegador.
Todos los comandos se ejecutan en la Raspberry Pi (por SSH o con teclado). Sustituye `pi` por tu usuario si es otro.

> **Qué está probado y qué no.** La lógica de la app (tests, bucle principal, rutas de datos, arranque/cierre de
> `run.py`) se probó en un Linux de pruebas con FastAPI/uvicorn simulados. **No se ha probado en una Raspberry Pi
> real**, ni la instalación de dependencias con piwheels. Los pasos de abajo siguen la documentación citada al final;
> si algún comando falla, mira la sección *Problemas frecuentes*.

---

## 0. Antes de empezar: comprueba tu sistema

```bash
cat /etc/os-release | grep PRETTY_NAME     # Debian GNU/Linux 12 (bookworm) ...
dpkg --print-architecture                   # armhf (32 bits)  ó  arm64 (64 bits)
python3 --version                           # en Bookworm: Python 3.11.x
```

El resultado de `dpkg --print-architecture` importa para el paso 4 (ver nota sobre piwheels).

## 1. Paquetes del sistema

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip unzip
```

Desde Bookworm, `pip install` fuera de un entorno virtual está bloqueado ("externally-managed-environment"),
así que siempre se usa un `venv` (paso 4).

## 2. Usuario de servicio y carpeta de la aplicación

El servicio se ejecuta con un usuario sin privilegios ni shell. La app va en `/opt/avamet-monitor`
(**no** dentro de `/home`: el servicio usa `ProtectHome=true`).

```bash
sudo useradd --system --home-dir /opt/avamet-monitor --shell /usr/sbin/nologin avamet
sudo mkdir -p /opt/avamet-monitor
```

## 3. Copiar la aplicación a la Raspberry Pi

En tu PC, comprime la carpeta del proyecto **sin** `venv/`, `__pycache__/`, `historial.db`, `.env` ni `data/`
(incluye `coordenadas.json` si lo tienes: evita descargar el catálogo de estaciones en el primer arranque),
y envíala:

```bash
# En tu PC (Windows 10/11 ya trae scp en PowerShell)
scp avamet.zip pi@raspberrypi.local:/tmp/
```

En la Raspberry:

```bash
sudo unzip -o /tmp/avamet.zip -d /opt/avamet-monitor
ls /opt/avamet-monitor/run.py               # debe existir en esta ruta; si quedó en una subcarpeta, súbela un nivel
sudo chown -R avamet:avamet /opt/avamet-monitor
```

## 4. Entorno virtual y dependencias (piwheels)

**piwheels** ofrece paquetes ya compilados para Raspberry Pi y evita compilar `lxml` y similares (que tardaría mucho).
Según su FAQ, se activa con `extra-index-url=https://www.piwheels.org/simple` en `/etc/pip.conf`.

```bash
cat /etc/pip.conf        # ¿aparece la línea extra-index-url=https://www.piwheels.org/simple ?
```

Si el fichero **no existe o no tiene esa línea**, créalo:

```bash
printf '[global]\nextra-index-url=https://www.piwheels.org/simple\n' | sudo tee /etc/pip.conf
```

> ⚠️ Según la FAQ de piwheels, **no dan soporte a 64 bits (aarch64)**. Si `dpkg --print-architecture` devolvió
> `arm64`, piwheels no te aporta nada y pip usará los paquetes de PyPI. No puedo confirmar que PyPI tenga paquete
> precompilado de cada dependencia para tu caso: por eso se usa `--only-binary=:all:` (si falta uno, **falla al
> instante en vez de ponerse a compilar durante media hora**).

Crear el entorno e instalar:

```bash
cd /opt/avamet-monitor
sudo -u avamet python3 -m venv venv
sudo -u avamet venv/bin/pip install --no-cache-dir --upgrade pip
sudo -u avamet venv/bin/pip install --no-cache-dir --only-binary=:all: -r requirements.txt
```

**Si falla** con `uvicorn[standard]` (uvloop, httptools o watchfiles sin paquete para tu plataforma), usa la lista
alternativa, con uvicorn en Python puro (más lento, pero suficiente para esta app):

```bash
sudo -u avamet venv/bin/pip install --no-cache-dir --only-binary=:all: -r requirements-rpi.txt
```

**Si falla `lxml`**, instálalo desde Debian y haz el entorno con acceso a los paquetes del sistema:

```bash
sudo apt install -y python3-lxml
sudo rm -rf venv
sudo -u avamet python3 -m venv --system-site-packages venv
sudo -u avamet venv/bin/pip install --no-cache-dir --only-binary=:all: -r requirements-rpi.txt
```

Comprobación:

```bash
sudo -u avamet venv/bin/python -c "import fastapi, uvicorn, lxml, bs4, requests, dotenv; print('dependencias OK')"
```

## 5. Configuración (`.env`)

Token y contraseña de Telegram **solo** van aquí, nunca en el código ni en el `.service`.

```bash
cd /opt/avamet-monitor
sudo cp .env.example .env
sudo nano .env                      # rellena AVAMET_TELEGRAM_TOKEN y AVAMET_TELEGRAM_CLAVE (mín. 8 caracteres)
sudo chown avamet:avamet .env
sudo chmod 600 .env
```

Sin esas dos variables la app arranca igualmente, pero **el bot de Telegram queda desactivado** y lo dice en el log.

## 6. Reloj y zona horaria

La Raspberry Pi no tiene reloj con batería (RTC) de serie, y la app compara la hora del dato con el reloj del equipo
para decidir si el dato está caducado. Comprueba que la hora es correcta y se sincroniza:

```bash
timedatectl                          # "System clock synchronized: yes" y "NTP service: active"
sudo timedatectl set-timezone Europe/Madrid
```

Tras un corte de luz sin internet la hora puede ser incorrecta hasta que vuelva la red; en ese caso la comparación
de edad de los datos no es fiable (no he probado ese escenario). Para evitarlo, instala un reloj con batería:
ver el **Apéndice A (RTC DS3231)**.

## 7. Prueba manual antes de crear el servicio

```bash
cd /opt/avamet-monitor
sudo -u avamet venv/bin/python run.py --headless
```

Debes ver `Modo: HEADLESS | escuchando en http://0.0.0.0:8000 | datos en: /opt/avamet-monitor/data`.
Desde otro equipo de la red abre `http://IP_DE_LA_RASPBERRY:8000` (la IP: `hostname -I`).
Para parar, **Ctrl+C**: debe cerrar con `AVAMET Monitor detenido.` y sin trazas de error.

## 8. Instalar el servicio

```bash
sudo cp /opt/avamet-monitor/deploy/avamet-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now avamet-monitor
systemctl status avamet-monitor              # debe poner "active (running)"
```

Si instalaste en otra ruta o con otro usuario, edita antes `User`, `Group`, `WorkingDirectory` y `ExecStart`.

## 9. Ver los logs (journalctl)

```bash
journalctl -u avamet-monitor -f                       # en directo (Ctrl+C para salir)
journalctl -u avamet-monitor -n 100 --no-pager        # últimas 100 líneas
journalctl -u avamet-monitor --since "1 hour ago"
journalctl -u avamet-monitor -p warning -b            # solo avisos/errores desde el último arranque
```

Si dice que no hay permisos para leer el journal, antepón `sudo`.

## 10. Uso diario

| Qué | Comando |
|---|---|
| Estado | `systemctl status avamet-monitor` |
| Reiniciar (tras cambiar `.env`) | `sudo systemctl restart avamet-monitor` |
| Parar / arrancar | `sudo systemctl stop avamet-monitor` / `sudo systemctl start avamet-monitor` |
| Quitar el arranque automático | `sudo systemctl disable avamet-monitor` |
| Token de la API (para el botón de apagado o `curl`) | `sudo cat /opt/avamet-monitor/data/api_token.txt` |
| Fichero Boltwood | `/opt/avamet-monitor/data/boltwood.dat` |

El servicio usa `Restart=on-failure`: se reinicia solo si se cae por error, **pero no si lo apagas con el botón
"Apagar" de la web** (sale con código 0). Para volver a arrancarlo: `sudo systemctl start avamet-monitor`.

## 11. Dónde se guardan los datos

Todo en una sola carpeta, `/opt/avamet-monitor/data/` (cámbiala con `AVAMET_DATA_DIR` en `.env` o en el `.service`):

`historial.db`, `api_token.txt`, `lluvia_estado.json`, `viento_estado.json`, `boltwood.dat`, `suscriptores.json`,
`coordenadas.json`.

Copia de seguridad recomendada (parar no es imprescindible, pero es lo más seguro con la base de datos):

```bash
sudo systemctl stop avamet-monitor
sudo tar czf ~/avamet-backup-$(date +%F).tgz -C /opt/avamet-monitor data .env
sudo systemctl start avamet-monitor
```

## 12. Actualizar la aplicación

```bash
sudo systemctl stop avamet-monitor
# copia los archivos nuevos encima (sin tocar data/, .env ni venv/):
sudo unzip -o /tmp/avamet-nuevo.zip -d /opt/avamet-monitor
sudo chown -R avamet:avamet /opt/avamet-monitor
sudo -u avamet /opt/avamet-monitor/venv/bin/pip install --no-cache-dir --only-binary=:all: -r /opt/avamet-monitor/requirements.txt
sudo systemctl start avamet-monitor
```

## 13. Seguridad en la red local

- `run.py --headless` escucha en `0.0.0.0`: cualquier equipo de tu red puede abrir la web.
- Solo `/api/shutdown` y `/api/ubicacion` exigen el token (`api_token.txt`). **El resto de endpoints, incluidos los de
  ASCOM Alpaca, no tienen autenticación.** No abras el puerto 8000 en el router ni lo expongas a internet.
- Si quieres limitarlo a un único equipo de confianza, usa el cortafuegos de la Raspberry o de tu router.
- Alpaca: la app no implementa el descubrimiento automático (UDP 32227; no hay código para ello). En N.I.N.A. habría que
  añadir el servidor a mano (`http://IP:8000`). **No puedo confirmar** el nombre exacto de esa opción en tu versión de
  N.I.N.A.
- Alpaca exige que el cliente **se conecte** (`PUT connected=True`) antes de `IsSafe`; si no, responde el error 1031
  «no conectado». Tras reiniciar la aplicación (p. ej. `systemctl restart`) hay que **volver a conectar en N.I.N.A.**
  Paso a paso en `GUIA_NINA.md`.
- `boltwood.dat` es un fichero local de la Raspberry: un N.I.N.A. en otro PC no puede leerlo salvo que lo compartas
  por red (Samba u otro). No he probado esa configuración.

## Apéndice A (opcional): reloj con batería RTC DS3231 por I2C

Sirve para que la Raspberry Pi conserve la hora tras un corte de luz **aunque no haya internet**. Aplica a las
Raspberry Pi sin RTC propio (3, 4, Zero 2 W, ...). La Raspberry Pi 5 trae RTC integrado (necesita su batería en el
conector J5; no uses una CR2032 ahí), así que no necesita este apéndice.

> Los pasos 1–6 siguen la guía de raspberry.tips (ver fuentes). **No los he probado en hardware.** El punto 7
> (`hwclock -s` al arrancar) es una unidad systemd que escribo yo basándome en el funcionamiento general de
> `hwclock`; esa guía no lo cubre y no he podido confirmar si tu versión de Raspberry Pi OS ya carga la hora del RTC
> automáticamente al arrancar. Por eso la unidad es una garantía explícita, no un parche de algo que haya visto roto.

**1. Conexión (con la Pi apagada y desenchufada, 3,3 V):**

| Módulo DS3231 | Pin de la Raspberry Pi |
|---|---|
| VCC | Pin 1 (3,3 V) |
| SDA | Pin 3 (GPIO 2, SDA) |
| SCL | Pin 5 (GPIO 3, SCL) |
| GND | Pin 6 (GND) |

Deja sin conectar `32K` y `SQW`. Pon una pila **CR2032 normal** con el + hacia arriba (no una LIR2032 recargable:
a 3,3 V el circuito de carga del módulo apenas la carga).

**2. Habilitar I2C con raspi-config** (solo hace falta para poder usar `i2cdetect`; el overlay del punto 3 activa
el bus por sí mismo):

```bash
sudo raspi-config        # 3 Interface Options  ->  I5 I2C  ->  Yes  ->  Finish
sudo apt install -y i2c-tools
sudo i2cdetect -y 1      # el módulo debe aparecer en la dirección 68 (fila 60, columna 8)
```

**3. Añadir el overlay en `config.txt`.** En Bookworm el fichero es `/boot/firmware/config.txt`
(en imágenes antiguas era `/boot/config.txt`):

```bash
sudo nano /boot/firmware/config.txt
```

Añade esta línea **al final** del fichero, debajo de `[all]`:

```
dtoverlay=i2c-rtc,ds3231
```

Guarda y reinicia: `sudo reboot`.

**4. Comprobar que el kernel lo ha reconocido:**

```bash
ls /dev/rtc*                              # debe existir /dev/rtc0
cat /sys/class/rtc/rtc0/name              # rtc-ds1307 (ese controlador también sirve al DS3231)
sudo i2cdetect -y 1                       # ahora en la dirección 68 debe poner "UU" (controlador en uso)
```

Si sigue saliendo `68` en vez de `UU`, revisa la línea de `config.txt` y reinicia. Si sale `--`, revisa el cableado.

**5. Quitar `fake-hwclock` si estuviera instalado** (en imágenes recientes ya no viene; en antiguas puede
sobrescribir la hora buena del RTC con una guardada más vieja):

```bash
dpkg -l fake-hwclock                      # si no aparece nada, no está instalado
sudo apt remove -y fake-hwclock           # solo si estaba instalado
```

**6. Poner el RTC en hora.** Si `hwclock` no existe: `sudo apt install -y util-linux-extra`.

Con internet (la hora del sistema ya es correcta: compruébala con `date`):

```bash
sudo hwclock -w         # escribe la hora del sistema en el RTC
sudo hwclock -r         # lee el RTC: debe coincidir con `date`
```

Sin internet, fijándola a mano:

```bash
sudo timedatectl set-ntp false
sudo timedatectl set-time "2026-11-01 14:30:00"
sudo hwclock -w
sudo hwclock -r
sudo timedatectl set-ntp true
```

**7. Forzar la lectura del RTC al arrancar (`hwclock -s`).** `hwclock -s` (`--hctosys`) copia la hora del RTC al
reloj del sistema. Este servicio lo ejecuta en cada arranque y **antes** de AVAMET Monitor
(comprueba la ruta con `which hwclock`; si no es `/sbin/hwclock`, cámbiala en `ExecStart`):

```bash
sudo tee /etc/systemd/system/rtc-sync.service >/dev/null <<'UNIT'
[Unit]
Description=Copiar la hora del RTC DS3231 al sistema (hwclock -s)
ConditionPathExists=/dev/rtc0
Before=avamet-monitor.service

[Service]
Type=oneshot
ExecStart=/sbin/hwclock -s
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now rtc-sync.service
systemctl status rtc-sync.service            # "active (exited)" y sin errores
```

**8. Probar el corte de luz.** Con la Pi conectada a la red y en hora: `sudo hwclock -w`, apaga, **desconecta también
el cable de red**, espera un par de minutos, enciende y comprueba con `date` que la hora es correcta (más los minutos
transcurridos).

Avisos:
- Si el RTC nunca se puso en hora o la pila está agotada, `hwclock -s` copiará una hora **incorrecta** al sistema hasta
  que se sincronice por NTP. Comprueba `sudo hwclock -r` tras el primer arranque con la pila nueva.
- Con internet, la hora del sistema se corrige sola por NTP, pero **no he podido confirmar** que eso actualice también
  el RTC. Repite `sudo hwclock -w` de vez en cuando (por ejemplo, tras cambiar la pila o si notas deriva).

## Problemas frecuentes

| Síntoma | Causa probable / solución |
|---|---|
| `error: externally-managed-environment` | Estás usando el `pip` del sistema. Usa siempre `venv/bin/pip`. |
| `pip` empieza a compilar y tarda mucho | Falta el paquete precompilado. Usa `--only-binary=:all:` y la alternativa del paso 4. |
| `ERROR: el puerto 8000 ya está en uso` | Otra instancia en marcha (`systemctl status avamet-monitor`) o otro programa en ese puerto. Cambia con `AVAMET_PORT`. |
| `PermissionError` al escribir en `data/` | `sudo chown -R avamet:avamet /opt/avamet-monitor`. |
| El servicio no arranca (`status=203/EXEC`) | Ruta de `ExecStart` incorrecta o `venv` sin crear. |
| Telegram: "desactivado: falta AVAMET_TELEGRAM_TOKEN" | Revisa `.env` (paso 5) y reinicia el servicio. |
| Estado SIN_DATOS con "Fallo interno: ..." (p. ej. la Pi arrancó antes que el router) | Es el vigilante: el estado es NO SEGURO y reintenta solo con espera creciente (5 s, 10 s ... máx. 5 min). Cuando vuelve la red se recupera sin reiniciar nada. El detalle del fallo está en `journalctl -u avamet-monitor`. |
| Datos caducados / SIN_DATOS tras reiniciar la Pi | Revisa la hora del sistema (paso 6) y la red. |
| Al parar el servicio tarda | Puede haber peticiones de red en curso (hasta unos segundos; `TimeoutStopSec=20` fija el máximo). |

---

## Fuentes consultadas

- piwheels, FAQ: <https://piwheels.org/faq.html> — configuración `/etc/pip.conf`, soporte solo para Raspberry Pi OS,
  Python 3.11 en Bookworm, **sin soporte para 64 bits (aarch64)**, uso de entornos virtuales desde Bookworm.
- Pimoroni, *Python Virtual Environments on Raspberry Pi Bookworm*: <https://pimoroni.github.io/venv-python> — error
  "externally managed" al usar `pip` fuera de un entorno virtual y creación del `venv`.
- raspberry.tips, *Raspberry Pi RTC DS3231 Setup – Timekeeping Without Internet*: <https://raspberry.tips/en/?p=9746> —
  cableado, `raspi-config`, `dtoverlay=i2c-rtc,ds3231` en `/boot/firmware/config.txt`, `i2cdetect` (`UU`),
  `fake-hwclock`, `hwclock -w`/`-r`, tipo de pila. (No cubre `hwclock -s`.)
- Comprobación local del fichero de servicio: `systemd-analyze verify` (solo avisó de que la ruta del `venv` no existe en el
  equipo de pruebas, como era de esperar).
