# Guía: conectar AVAMET Monitor con N.I.N.A. (y con programas que lean el fichero Boltwood)

Esta guía explica cómo hacer que **N.I.N.A.** (o un programa que lea el fichero `boltwood.dat`) reciba el estado
«seguro / no seguro» de AVAMET Monitor, y cómo montar una secuencia que cierre el techo cuando deje de ser seguro.

> **Qué está verificado y qué no.** Lo que hace *AVAMET Monitor* sale de su código y de sus tests. Lo que hace
> *N.I.N.A.*, *ASCOM* o tu *controlador de techo* lo he contrastado solo con la documentación pública que cito al final;
> donde no hay documentación suficiente aparece **«No puedo confirmar»**. No he podido probar esta guía con una
> instalación real de N.I.N.A. ni con tu techo: **haz siempre las pruebas de la sección 5 antes de dejarlo desatendido.**

## 0. Elige el camino

| Quieres conectar... | Vía | Estado |
|---|---|---|
| **N.I.N.A.** | **ASCOM Alpaca** (dispositivo «Safety Monitor») | Documentada abajo (secciones 1-6). |
| Un programa que lea el fichero Boltwood | `boltwood.dat` | Sección 7. La documentación de N.I.N.A. **no** lista el fichero Boltwood como fuente (su pestaña *Weather* admite «cualquier dispositivo ASCOM», OpenWeatherMap y Pegasus Ultimate Powerbox V2). |
| **Meteowatcher** | — | **No puedo confirmar** qué formatos lee: no he encontrado documentación fiable de ese programa. Si tiene una opción de «Boltwood / Single Line Data File», usa la sección 7; si no, esa vía no sirve. |

## 1. Antes de empezar

1. **AVAMET Monitor en marcha** y mostrando datos en `http://127.0.0.1:8000` (en la Raspberry Pi: `http://IP-DE-LA-PI:8000`).
2. **Dirección y puerto** que usará N.I.N.A.:
   * Mismo PC: `127.0.0.1` y puerto `8000` (`AVAMET_PORT` si lo has cambiado).
   * Otro equipo: la IP del equipo de AVAMET Monitor (`ipconfig` en Windows, `hostname -I` en la Pi). Por defecto la
     aplicación escucha **solo en este equipo** (`127.0.0.1`); en modo `--headless` escucha en `0.0.0.0`. Para el modo
     escritorio en red: `python run.py --host 0.0.0.0` (o `AVAMET_HOST=0.0.0.0`) y abre el puerto en el cortafuegos.
   * ⚠️ Los endpoints Alpaca **no tienen autenticación**: úsalo solo en una red de confianza.
3. **Comprueba que responde.** Abre en un navegador (cambia la IP):
   * `http://IP:8000/management/apiversions` → `{"Value":[1],"ErrorNumber":0,"ErrorMessage":""}`
   * `http://IP:8000/management/v1/configureddevices` → debe listar `AVAMET Safety Monitor`, tipo `SafetyMonitor`, número `0`.

## 2. Cómo funciona la conexión (importante)

El dispositivo cumple la regla de ASCOM de que `IsSafe` falla si el dispositivo **no está conectado**:

* Al arrancar la aplicación el dispositivo está **desconectado**.
* El cliente (N.I.N.A.) lo conecta con `PUT .../connected` y `Connected=True`; N.I.N.A. lo hace al pulsar «Conectar».
* Si se consulta `IsSafe` sin estar conectado, la respuesta es el error ASCOM **1031 (NotConnected)**, **sin** `Value`:

  ```json
  {"ErrorNumber": 1031, "ErrorMessage": "El monitor de seguridad no está conectado: envía PUT connected=true antes de consultar IsSafe.",
   "ClientTransactionID": 0, "ServerTransactionID": 481516}
  ```
* **Si reinicias AVAMET Monitor, la conexión se pierde** y hay que **volver a conectar** el Safety Monitor en N.I.N.A.
  (con `systemctl restart avamet-monitor` en la Pi, igual). Qué hace N.I.N.A. mientras recibe el 1031 **no puedo
  confirmarlo**; trátalo como «sin monitor de seguridad» hasta reconectar.
* Si tu cliente gestiona la conexión por su cuenta y nunca envía `PUT connected`, pon `AVAMET_ALPACA_ESTRICTO=0` en
  `.env` y reinicia: el dispositivo vuelve a estar siempre «conectado». Es una salida de emergencia, no la opción recomendada.

### 2.1 Prueba manual del protocolo (sin N.I.N.A.)

Para ver el comportamiento antes de tocar N.I.N.A. (cambia `IP`). En **PowerShell**:

```powershell
$u = "http://IP:8000/api/v1/safetymonitor/0"
Invoke-RestMethod "$u/issafe"                                   # 1) sin conectar: ErrorNumber 1031
Invoke-RestMethod -Method Put "$u/connected" -Body "Connected=True" -ContentType "application/x-www-form-urlencoded"
Invoke-RestMethod "$u/connected"                                # 2) Value: True
Invoke-RestMethod "$u/issafe"                                   # 3) Value: True (seguro) o False (no seguro)
Invoke-RestMethod -Method Put "$u/connected" -Body "Connected=False" -ContentType "application/x-www-form-urlencoded"
```

En **Linux / macOS** con curl:

```bash
U=http://IP:8000/api/v1/safetymonitor/0
curl "$U/issafe"                                  # 1) error 1031
curl -X PUT -d "Connected=True" "$U/connected"    # conectar
curl "$U/issafe"                                  # 3) {"Value": true/false, ...}
curl -X PUT -d "Connected=False" "$U/connected"   # desconectar
```

> Estos comandos están escritos a partir del código y de sus tests (que simulan FastAPI); no he podido arrancar el
> servidor real en mi entorno. Si algo no responde como se describe, dímelo con la salida exacta.

## 3. Conectar N.I.N.A. por ASCOM Alpaca

AVAMET Monitor **no implementa el descubrimiento automático de Alpaca** (UDP 32227), por lo que N.I.N.A. no lo
encontrará solo: hay que dar de alta el servidor a mano.

**3.1 Crear el dispositivo Alpaca en ASCOM** (procedimiento general de ASCOM, tomado de la guía de Starfront, que lo
describe para su servicio; aquí con tus valores):

1. Abre **ASCOM Driver Connect** (32 o 64 bits) y elige el tipo de dispositivo **SafetyMonitor**.
2. Pulsa **Choose**, abre el menú **Alpaca** y elige **Create Alpaca Driver**.
3. Ponle un nombre, p. ej. `AVAMET Monitor` (es el que verás en N.I.N.A.). Acepta el aviso de administrador de Windows.
4. Con el dispositivo nuevo seleccionado, pulsa **Properties** y rellena:

   | Campo | Valor |
   |---|---|
   | HTTP Service Type | `http` |
   | Host Name or IP | `127.0.0.1` (mismo PC) o la IP del equipo con AVAMET Monitor |
   | Alpaca Port | `8000` |
   | Remote Device number | `0` |
   | Enable rediscovery | sin marcar |
   | Connect / Disconnect Options | ver la nota siguiente |

5. Pulsa **OK** y luego **Connect** en la ventana de ASCOM Driver Connect para probar.

> **Nota sobre «Connect / Disconnect Options».** La guía de Starfront usa «Managed connect / disconnect locally».
> **No puedo confirmar qué hace exactamente cada opción** de ese desplegable. Compruébalo así: con el test
> conectado en ASCOM Driver Connect, abre `http://IP:8000/api/v1/safetymonitor/0/connected` en el navegador.
> * `"Value": true` → el cliente **sí** conectó el dispositivo remoto. Correcto.
> * `"Value": false` → ese modo **no** envía la conexión a AVAMET Monitor. Prueba otra opción del desplegable y
>   repite; si ninguna lo logra, usa `AVAMET_ALPACA_ESTRICTO=0` (sección 2).

**3.2 Conectar en N.I.N.A.** (según la documentación de N.I.N.A.: cada pestaña de equipo tiene un desplegable con los
dispositivos detectados y botones para configurar, refrescar y conectar):

1. Abre la pestaña **Equipment** y la pestaña de **Safety Monitor**.
2. Pulsa **Refresh** y selecciona `AVAMET Monitor` en el desplegable.
3. Pulsa **Connect**.
4. El icono de engranaje abre los ajustes del dispositivo seleccionado.

> Los nombres exactos de botones y menús pueden variar entre versiones de N.I.N.A.; los que cito son los de la guía de
> Starfront y la documentación de N.I.N.A. **No puedo confirmar** que coincidan con tu versión.

**3.3 Comprobar que funciona**

* En la interfaz web de AVAMET Monitor, el indicador **N.I.N.A.** de la cabecera se pone **verde** mientras se está
  respondiendo a consultas de `IsSafe` (si la última respuesta fue hace menos de 15 s). Si N.I.N.A. recibe el error
  1031, el indicador **no** se enciende (no se está respondiendo con datos).
* En el log de AVAMET Monitor verás `Alpaca: cliente CONECTADO` al conectar y, si algo consulta sin estar conectado,
  `Alpaca: IsSafe rechazado (NotConnected 0x407)...` (como mucho una vez por minuto).
* En N.I.N.A. el estado del monitor debe verse como seguro o no seguro (la guía de Backyard Astronomy dice que se
  muestra en el panel de imagen).

## 4. Qué significa «seguro» para N.I.N.A.

`IsSafe` devuelve **`true`** cuando el estado es **SEGURO** o **VIGILANCIA**, y **`false`** en **PELIGRO**,
**SIN_DATOS**, **INICIANDO**, **ERROR**, **APAGADO**, con la lectura de la estación de más de 10 minutos, o si el bucle
principal lleva más de 5 minutos sin actualizarse. Todo el detalle está en `MANUAL_TECNICO.md` (secciones 2 y 4).

## 5. Pruebas antes de fiarte (hazlas con el techo en un momento controlado)

1. **Seguro → no seguro por falta de datos.** Corta la red del equipo de AVAMET Monitor (o desenchufa el cable). En el
   siguiente ciclo (≤ ~1-2 min) el estado pasa a SIN_DATOS y `IsSafe` pasa a `false`; N.I.N.A. debe mostrarlo como no
   seguro. Restablece la red y comprueba que vuelve a seguro.
2. **No seguro por dato caducado, sin tocar la red.** En `.env` pon `AVAMET_EDAD_MAX_DATOS_S=1`, reinicia AVAMET
   Monitor, **vuelve a conectar** el monitor en N.I.N.A.: cualquier lectura tendrá más de 1 s, así que debe aparecer
   como no seguro. **Quita la línea y reinicia de nuevo** al terminar (¡no la dejes!).
3. **Reinicio.** Reinicia AVAMET Monitor y comprueba qué ve N.I.N.A. hasta que reconectas. Anota cómo reacciona tu
   secuencia: no puedo anticiparlo.
4. **Secuencia completa.** Con la secuencia de la sección 6 cargada, provoca (1) o (2) con el techo abierto y
   comprueba que **se cierra**. Mide cuánto tarda.

## 6. Secuencia en N.I.N.A. que cierra el techo cuando `IsSafe` es falso

### 6.1 Piezas que ofrece N.I.N.A. (según su documentación)

| Pieza | Qué dice la documentación |
|---|---|
| Condición **Loop While Safe** | «Loop for as long as the safety monitor is reporting safe conditions.» Interrumpe el conjunto de instrucciones cuando el monitor da *no seguro*. Se recomienda usarla junto a otra condición. Requiere un Safety Monitor conectado. |
| Condición **Loop While Unsafe** | Igual, pero en bucle mientras es *no seguro*. |
| Instrucción **Wait Until Safe** | Espera hasta que el monitor vuelva a dar condiciones seguras. |
| Instrucción **Close Dome Shutter** / **Open Dome Shutter** | Cierra / abre la compuerta de la cúpula. **Requiere un dispositivo de cúpula con compuerta controlable.** |
| Instrucción **Park Scope** | Aparca la montura (una montura aparcada no acepta slews). Requiere un driver que pueda aparcar. |
| Evaluación | Las condiciones se evalúan después de cada instrucción; si una falla, se omite el resto de instrucciones del conjunto. Los bloques se ejecutan de arriba abajo. |
| Áreas del secuenciador | Inicio (*Start*), Objetivos (*Targets*) y Fin (*End*). |

Sobre una versión antigua: en el *issue* #740 de N.I.N.A., con una condición del Safety Monitor la exposición **en curso
se terminaba** antes de salir del bucle; el mantenedor indicó que una versión nocturna posterior cancelaría de
inmediato las instrucciones en ejecución al pasar a no seguro. **No puedo confirmar desde qué versión estable ocurre**:
mantén N.I.N.A. actualizado y comprueba el comportamiento en la prueba 4 de la sección 5.

### 6.2 Estructura recomendada (es una propuesta mía, basada en esas piezas; no es una receta oficial)

```
SECUENCIA AVANZADA
├── INICIO (Start)
│     1. Wait Until Safe                  ← no se abre nada hasta que sea seguro
│     2. Open Dome Shutter                ← (o la instrucción que abra tu techo)
│     3. ... (conectar/desaparcar/enfriar cámara, según tu equipo)
│
├── OBJETIVOS (Targets)
│     Conjunto de instrucciones secuencial
│        Condiciones:  Loop While Safe            ← se interrumpe al pasar a NO SEGURO
│                      Loop Until Time (amanecer astronómico)   ← la otra condición recomendada
│        Contenido:    tus objetivos y exposiciones
│
└── FIN (End)
      1. Park Scope
      2. Close Dome Shutter                ← (o la instrucción que cierre tu techo)
      3. ... (calentar/apagar cámara...)
```

Cómo debería encajar, **según lo que dice la documentación**: cuando `IsSafe` pasa a `false`, *Loop While Safe* falla,
se omite el resto del conjunto de *Targets* y, como los bloques se ejecutan de arriba abajo, el flujo llega al área de
**Fin**, que aparca y cierra. **Lo que no puedo confirmar** es que el área de Fin se ejecute en *todos* los casos de
interrupción (la página del secuenciador no describe qué ocurre con Inicio/Fin al interrumpir o detener): por eso la
prueba 4 de la sección 5 es obligatoria.

Notas:

* **Tu techo debe ser una «cúpula» para N.I.N.A.** (un dispositivo ASCOM Dome con compuerta controlable) para poder usar
  *Close Dome Shutter*. Si tu techo se mueve de otra forma (relé, Arduino, interruptor ASCOM...), el cierre se hace con
  la instrucción que corresponda a *ese* dispositivo: **no puedo decirte cuál sin conocer tu montaje**.
* Si quieres que **reanude** cuando vuelva a ser seguro, hace falta añadir lógica de espera (por ejemplo *Wait Until
  Safe* y una repetición del bloque). Es más delicado: pruébalo con calma y no lo montes sin necesidad.
* *Loop While Safe* tiene sentido porque **VIGILANCIA cuenta como seguro**: no cerrará por un aviso de viento moderado
  ni por humedad alta; sí por PELIGRO (lluvia, viento fuerte), SIN_DATOS o dato caducado.

### 6.3 La red de seguridad que NO depende de este programa

Si AVAMET Monitor, el PC o la red caen del todo, **nadie** escribe «no seguro»: `IsSafe` deja de responder y el fichero
Boltwood queda con la última línea. Qué hace N.I.N.A. ante un monitor que no responde **no puedo confirmarlo**.
Para un techo que debe proteger tu equipo, añade una protección independiente (sensor de lluvia local con corte directo,
temporizador, final de carrera, SAI...). No confíes solo en software.

## 7. Programas que leen el fichero Boltwood (`boltwood.dat`)

AVAMET Monitor escribe una línea en el **formato nuevo del *Single Line Data File* del Cloud Sensor II** (104 columnas;
detalle columna a columna en `MANUAL_TECNICO.md`, sección 4.4). Lo importante para quien lo lee:

* **Columna 102 (`C`) = 1**: cierre de techo solicitado (no seguro). **Columna 104 (`A`) = 1**: alerta (no seguro o VIGILANCIA).
* En fallo de datos: `Wind = -4.0` y `C = A = 1`. En el cierre de la aplicación: línea `APAGADO` con `C = A = 1`.
* El fichero se reescribe cada ciclo (≈ 60 s) y de forma atómica. Su fecha/hora (`Date`, `Time`, `Now()`) permite
  detectar un fichero **viejo**: un programa sensato debe tratar un fichero sin actualizar como no seguro.
* AVAMET **no mide** nubes, luz ni temperatura del cielo: `SkyT`, `c` y `d` son valores fijos (0 = desconocido por
  defecto). Si tu programa los usa para decidir, **no** servirán.

**Dónde está el fichero:**

| Instalación | Ruta por defecto |
|---|---|
| Windows, desde código fuente | carpeta del proyecto: `boltwood.dat` |
| Windows, `.exe` | `%APPDATA%\AVAMETMonitor\boltwood.dat` |
| Raspberry Pi / Linux | `<proyecto>/data/boltwood.dat` (o `AVAMET_DATA_DIR`) |
| Cualquiera | la que indiques en `AVAMET_BOLTWOOD_PATH` |

**Pasos genéricos** para un programa con soporte de Boltwood por fichero (**no verificados con ningún programa
concreto**; los nombres de las opciones cambian de uno a otro):

1. Elige el sensor/fuente «Boltwood» en modo **fichero** (*Single Line Data File*), formato **nuevo**.
2. Indica la ruta del `boltwood.dat`.
3. Si permite elegir qué campo cierra el techo, usa la **columna `C`** (cierre solicitado).
4. Si el programa lee desde otro equipo, el fichero tiene que estar compartido por red (Samba/carpeta compartida);
   **no he probado esa configuración**.

Si tu programa no lee ese formato, la vía Alpaca (sección 3) es la documentada para N.I.N.A.

## 8. Problemas frecuentes

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| N.I.N.A. no encuentra «AVAMET Monitor» | No hay descubrimiento automático | Crear el dispositivo a mano (sección 3.1). |
| Error 1031 / «no conectado» | El cliente no envió `PUT connected=True`, o se reinició la aplicación | Reconectar el Safety Monitor en N.I.N.A.; comprobar `.../connected`; ver la nota de «Connect / Disconnect Options». |
| `connected` es `false` aunque N.I.N.A. dice «conectado» | Ese modo del cliente no envía la conexión | Cambiar la opción en Properties o `AVAMET_ALPACA_ESTRICTO=0`. |
| No responde desde otro equipo | La app escucha solo en `127.0.0.1`, o el cortafuegos bloquea el 8000 | `--host 0.0.0.0` y regla de cortafuegos. |
| `IsSafe` siempre `false` | Estado SIN_DATOS / PELIGRO / dato caducado | Mira el motivo en el panel de alertas (clic en el semáforo de la web). |
| El indicador N.I.N.A. no se enciende | Nadie consulta, o se le está rechazando | Ver el log (`IsSafe rechazado`) y la sección 2. |
| Tras actualizar la aplicación, todo sigue «viejo» | Caché del navegador | Recarga; el cache-busting suele hacerlo solo. |

## 9. Lista final antes de dejarlo desatendido

- [ ] `GET .../connected` es `true` con N.I.N.A. conectado.
- [ ] Probé el corte de red (sección 5, prueba 1) y el techo se cerró.
- [ ] Probé el reinicio de AVAMET Monitor y sé qué hace mi secuencia hasta reconectar.
- [ ] El cortafuegos solo permite el puerto 8000 a equipos de confianza (nada hacia internet).
- [ ] Tengo una protección independiente del software para la lluvia (sección 6.3).
- [ ] Quité `AVAMET_EDAD_MAX_DATOS_S=1` de `.env` si la usé para probar.

## Fuentes

* N.I.N.A. — Instrucciones del secuenciador avanzado (Wait Until Safe, Close/Open Dome Shutter, Park Scope):
  <https://nighttime-imaging.eu/docs/master/site/sequencer/advanced/instructions/>
* N.I.N.A. — Condiciones de bucle (Loop While Safe / Loop While Unsafe):
  <https://nighttime-imaging.eu/docs/master/site/sequencer/advanced/conditions/>
* N.I.N.A. — Estructura del secuenciador avanzado (Start / Targets / End, evaluación de condiciones):
  <https://nighttime-imaging.eu/docs/master/site/sequencer/advanced/advanced/>
* N.I.N.A. — Triggers (no hay ninguno de seguridad; solo *Synchronize Dome* en cúpula):
  <https://nighttime-imaging.eu/docs/master/site/sequencer/advanced/triggers/>
* N.I.N.A. — Pestaña Equipment (desplegable de dispositivos, botones de configurar/refrescar/conectar):
  <https://nighttime-imaging.eu/docs/master/site/tabs/equipment>
* N.I.N.A. — Pestaña Weather (fuentes admitidas):
  <https://nighttime-imaging.eu/docs/master/site/tabs/equipment/weather/>
* N.I.N.A. — *issue* #740 (comportamiento al pasar a no seguro):
  <https://bitbucket.org/Isbeorn/nina/issues/740/loop-when-safetymonitor-true-does-not>
* Starfront — Creación de un dispositivo Alpaca en ASCOM Driver Connect (procedimiento general):
  <https://docs.starfront.space/guides/ascom_alpaca/safety/>
* Backyard Astronomy — *NINA Weather and Safety Monitoring*:
  <https://www.backyardastronomy.net/2026/08/30/nina-weather-and-safety-monitoring-protecting-your-equipment-while-you-sleep/>
* ASCOM — `ISafetyMonitor.IsSafe` (lanza NotConnected si no está conectado):
  <https://ascom-standards.org/Help/Platform/html/P_ASCOM_DeviceInterface_ISafetyMonitor_IsSafe.htm>
* ASCOM — Referencia de la API Alpaca (HTTP 200 con `ErrorNumber`; NotConnected = 0x407; claves sin distinción de mayúsculas):
  <https://ascom-standards.org/AlpacaDeveloper/ASCOMAlpacaAPIReference.html>
* ASCOM — Excepciones Alpaca (NotConnected 0x407 = 1031):
  <https://ascom-standards.org/alpyca/alpaca.exceptions.html>
