from flask import Flask, request, jsonify
import os
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from supabase import create_client

app = Flask(__name__)

# --- CONFIGURACIÓN DE SUPABASE ---
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

# Limpiar posibles comillas o espacios extras en las variables de entorno
if SUPABASE_URL.startswith('"') and SUPABASE_URL.endswith('"'):
    SUPABASE_URL = SUPABASE_URL[1:-1]
if SUPABASE_KEY.startswith('"') and SUPABASE_KEY.endswith('"'):
    SUPABASE_KEY = SUPABASE_KEY[1:-1]

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("Conexión con Supabase inicializada correctamente.")
    except Exception as e:
        print(f"Error crítico al crear el cliente de Supabase: {e}")

# --- MEMORIA LOCAL EXCLUSIVA PARA SESIONES ACTIVAS ---
sesiones_activas = {}
operaciones_lock = threading.Lock()

# Version ligera del catalogo: permite saber si cambio sin descargar todos los productos.
catalogo_version = 1

def marcar_catalogo_cambiado():
    global catalogo_version
    catalogo_version += 1
    return catalogo_version


# --- FUNCIONES AUXILIARES DE PERSISTENCIA EN SUPABASE ---
def leer_json(key_name, default):
    if not supabase:
        print(f"Advertencia: Supabase no está conectado. Usando default para {key_name}")
        return default
    try:
        response = supabase.table("app_storage").select("data").eq("Key", key_name).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]["data"]
    except Exception as e:
        print(f"Error leyendo {key_name} de Supabase: {e}")
    return default

def guardar_json(key_name, datos):
    if not supabase:
        print(f"Advertencia: Supabase no está conectado. No se pudo guardar {key_name}")
        return False
    try:
        supabase.table("app_storage").upsert({"Key": key_name, "data": datos}).execute()
        return True
    except Exception as e:
        print(f"Error guardando {key_name} en Supabase: {e}")
        return False

# --- FUNCIÓN AUXILIAR PARA ENVIAR CORREOS SMTP ---
def enviar_correo_smtp(destinatario, emisor, password, asunto, cuerpo):
    try:
        msg = MIMEMultipart()
        msg['From'] = emisor
        msg['To'] = destinatario
        msg['Subject'] = asunto
        msg.attach(MIMEText(cuerpo, 'plain'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(emisor, password)
        server.sendmail(emisor, destinatario, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Error enviando correo: {e}")
        return False

# --- RUTAS DE CONFIGURACIÓN ---
@app.route('/configuracion', methods=['GET', 'POST'])
def manejar_configuracion():
    config_default = {
        "url_servidor": "https://sistema-pos-20cm.onrender.com",
        "tasa_bcv": 36.50,
        "correo_destino": "expendiodemedicinas.lc@gmail.com",
        "correo_emisor": "reportesdeventas29@gmail.com",
        "pass_emisor": "tlibjzjfwpoddkxg",
        "actualizar_tasa_auto": "Sí"
    }
    if request.method == 'POST':
        data = request.json
        guardar_json("server_config", data)
        return jsonify({"status": "success", "message": "Configuración actualizada"}), 200
    else:
        config = leer_json("server_config", config_default)
        return jsonify(config), 200

@app.route('/correo/prueba', methods=['POST'])
def probar_correo():
    data = request.json or {}
    correo_destino = str(data.get("correo_destino", "")).strip()
    correo_emisor = str(data.get("correo_emisor", "")).strip()
    pass_emisor = str(data.get("pass_emisor", "")).strip()

    if not correo_destino or not correo_emisor or not pass_emisor:
        return jsonify({"error": "Debes indicar correo destino, correo emisor y clave de aplicación."}), 400

    asunto = "Prueba de correo - Sistema POS"
    cuerpo = (
        "Este es un correo de prueba del Sistema POS.\n\n"
        "Si recibiste este mensaje, la configuración SMTP está funcionando correctamente."
    )
    if enviar_correo_smtp(correo_destino, correo_emisor, pass_emisor, asunto, cuerpo):
        return jsonify({"status": "success", "message": f"Correo de prueba enviado correctamente a {correo_destino}."}), 200
    return jsonify({"error": "No se pudo enviar el correo. Revisa el correo emisor, la clave de aplicación y la configuración SMTP de Gmail."}), 500

@app.route('/productos/respaldo', methods=['POST'])
def respaldar_productos():
    data = request.json or {}
    productos = data.get("productos")
    if not isinstance(productos, list):
        return jsonify({"error": "El respaldo debe contener una lista de productos."}), 400

    # Este endpoint SOLO CREA/ACTUALIZA UNA COPIA DE SEGURIDAD.
    # Nunca modifica ni elimina server_productos.
    from datetime import datetime
    respaldo = {
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cantidad_productos": len(productos),
        "productos": productos
    }
    if guardar_json("server_productos_ultimo_respaldo", respaldo):
        return jsonify({
            "status": "success",
            "message": f"Respaldo de {len(productos)} productos guardado correctamente. El catálogo original no fue modificado ni borrado."
        }), 200
    return jsonify({"error": "No se pudo guardar el respaldo en Supabase."}), 500

# --- RUTAS DE USUARIOS ---
@app.route('/usuarios', methods=['GET', 'POST'])
def manejar_usuarios():
    usuarios_default = {
        "Administrador": ["admin123", "Administrador"],
        "Caja 1": ["caja123", "Cajero"],
        "Caja 2": ["caja456", "Cajero"],
        "Caja 3": ["caja789", "Cajero"]
    }
    if request.method == 'POST':
        data = request.json
        guardar_json("server_usuarios", data)
        return jsonify({"status": "success", "message": "Usuarios sincronizados"}), 200
    else:
        usuarios = leer_json("server_usuarios", usuarios_default)
        return jsonify(usuarios), 200

# --- CONTROL DE SESIONES ACTIVAS (En memoria local de Render) ---
@app.route('/sesiones/verificar', methods=['GET'])
def verificar_sesion():
    usuario = request.args.get("usuario", "")
    activo = sesiones_activas.get(usuario, False)
    return jsonify({"activo": activo}), 200

@app.route('/sesiones/iniciar', methods=['POST'])
def iniciar_sesion():
    data = request.json
    usuario = data.get("usuario")
    if usuario:
        sesiones_activas[usuario] = True
        return jsonify({"status": "success"}), 200
    return jsonify({"error": "Usuario no especificado"}), 400

@app.route('/sesiones/cerrar', methods=['POST'])
def cerrar_sesion():
    data = request.json or {}
    usuario = data.get("usuario")
    datos_cierre = data.get("datos_cierre", {}) # Totales enviados desde la caja al cerrar
    
    if not usuario:
        return jsonify({"error": "Usuario no especificado"}), 400

    # 1. Marcar siempre como inactivo en memoria local
    sesiones_activas[usuario] = False

    # Si es Administrador, cerramos de inmediato sin procesar cola de cajas de cobro
    if "admin" in usuario.lower() or usuario.lower() == "administrador":
        return jsonify({"status": "success", "message": "Sesión de administrador cerrada"}), 200

    # 2. Guardar o actualizar el cierre parcial de esta caja en Supabase (REPOSO)
    cierres_actuales = leer_json("server_cierres_turno", [])
    cierres_actuales = [c for c in cierres_actuales if c.get("usuario") != usuario]
    datos_cierre["usuario"] = usuario
    cierres_actuales.append(datos_cierre)
    guardar_json("server_cierres_turno", cierres_actuales)

    # 3. Contar cuántas cajas (excluyendo al administrador) siguen activas en memoria
    cajas_activas_restantes = 0
    for usr, activo in sesiones_activas.items():
        is_usr_admin = "admin" in usr.lower() or usr.lower() == "administrador"
        if activo and not is_usr_admin:
            cajas_activas_restantes += 1

    # 4. Validar si aún faltan cajas por cerrar
    if cajas_activas_restantes > 0:
        return jsonify({
            "status": "success", 
            "message": f"Cierre de {usuario} en reposo en la nube. Faltan {cajas_activas_restantes} caja(s) por cerrar."
        }), 200
    else:
        # ¡TODAS LAS CAJAS HAN CERRADO! Procesar correos individuales y consolidado.
        config = leer_json("server_config", {})
        correo_dest = config.get("correo_destino", "")
        correo_emisor = config.get("correo_emisor", "")
        pass_emisor = config.get("pass_emisor", "")

        if correo_dest and correo_emisor and pass_emisor:
            total_general_pto = 0
            total_general_pago_movil = 0
            total_general_biopago = 0
            total_general_efectivo = 0
            total_general_divisas = 0
            total_general_general = 0
            
            cuerpo_total_consolidado = "--- RESUMEN CONSOLIDADO GENERAL DE CIERRE DE TODAS LAS CAJAS ---\n\n"

            # A. Enviar el correo individual de cada caja en reposo
            for cierre in cierres_actuales:
                caja_nombre = cierre.get("usuario", "Caja")
                
                monto_pto = float(cierre.get("total_punto", cierre.get("punto_venta", 0)))
                monto_pago_movil = float(cierre.get("total_pago_movil", cierre.get("pago_movil", 0)))
                monto_biopago = float(cierre.get("total_biopago", cierre.get("biopago", 0)))
                monto_efectivo = float(cierre.get("total_efectivo", cierre.get("efectivo", 0)))
                monto_divisas = float(cierre.get("total_divisas", cierre.get("divisas", 0)))
                monto_total = float(cierre.get("total_venta", cierre.get("total_general", cierre.get("total_dolares", 0))))
                
                total_general_pto += monto_pto
                total_general_pago_movil += monto_pago_movil
                total_general_biopago += monto_biopago
                total_general_efectivo += monto_efectivo
                total_general_divisas += monto_divisas
                total_general_general += monto_total

                cuerpo_individual = f"Reporte de Cierre de Caja\n"
                cuerpo_individual += f"Cajero / Caja: {caja_nombre}\n"
                cuerpo_individual += f"- Punto de Venta: {monto_pto}\n"
                cuerpo_individual += f"- Pago Móvil: {monto_pago_movil}\n"
                cuerpo_individual += f"- Biopago: {monto_biopago}\n"
                cuerpo_individual += f"- Efectivo: {monto_efectivo}\n"
                cuerpo_individual += f"- Divisas: {monto_divisas}\n"
                cuerpo_individual += f"TOTAL CAJA: {monto_total}\n"

                enviar_correo_smtp(correo_dest, correo_emisor, pass_emisor, f"Cierre de Turno - {caja_nombre}", cuerpo_individual)

                cuerpo_total_consolidado += f"• {caja_nombre} -> Punto: {monto_pto} | Pago Móvil: {monto_pago_movil} | Biopago: {monto_biopago} | Total: {monto_total}\n"

            # B. Agregar sumatoria total general al consolidado
            cuerpo_total_consolidado += f"\n----------------------------------------\n"
            cuerpo_total_consolidado += f"GRAN TOTAL PUNTO DE VENTA: {total_general_pto}\n"
            cuerpo_total_consolidado += f"GRAN TOTAL PAGO MÓVIL: {total_general_pago_movil}\n"
            cuerpo_total_consolidado += f"GRAN TOTAL BIOPAGO: {total_general_biopago}\n"
            cuerpo_total_consolidado += f"GRAN TOTAL EFECTIVO: {total_general_efectivo}\n"
            cuerpo_total_consolidado += f"GRAN TOTAL DIVISAS: {total_general_divisas}\n"
            cuerpo_total_consolidado += f"----------------------------------------\n"
            cuerpo_total_consolidado += f"GRAN TOTAL GENERAL DE LA JORNADA: {total_general_general}\n"

            # C. Enviar correo consolidado final
            enviar_correo_smtp(correo_dest, correo_emisor, pass_emisor, "Consolidado Total de Cierres de Caja", cuerpo_total_consolidado)

        # 5. Limpiar los cierres temporales en la nube y vaciar sesiones locales
        guardar_json("server_cierres_turno", [])
        sesiones_activas.clear()

        return jsonify({
            "status": "success", 
            "message": "Todas las cajas cerradas. Correos individuales en reposo y consolidado total enviados con éxito."
        }), 200

# --- RUTAS DE PRODUCTOS ---
def _normalizar_codigo(valor):
    return str(valor or "").strip().casefold()

def _nuevo_id_producto():
    import uuid
    return uuid.uuid4().hex

def _asegurar_ids_productos(productos):
    """Asigna ID solo a productos antiguos que no lo tengan; nunca elimina productos."""
    cambio = False
    ids = {str(p.get("id", "")).strip() for p in productos if str(p.get("id", "")).strip()}
    for p in productos:
        if not str(p.get("id", "")).strip():
            nuevo = _nuevo_id_producto()
            while nuevo in ids:
                nuevo = _nuevo_id_producto()
            p["id"] = nuevo
            ids.add(nuevo)
            cambio = True
    return cambio

def _buscar_producto(productos, producto_id="", codigo=""):
    """Busca por ID primero. El código solo se usa como respaldo cuando no hay ID."""
    pid = str(producto_id or "").strip()
    cod = _normalizar_codigo(codigo)
    if pid:
        for i, p in enumerate(productos):
            if str(p.get("id", "")).strip() == pid:
                return i, p
        return None, None
    if cod:
        for i, p in enumerate(productos):
            if _normalizar_codigo(p.get("codigo")) == cod:
                return i, p
    return None, None

@app.route('/catalogo/version', methods=['GET'])
def obtener_version_catalogo():
    return jsonify({"catalogo_version": catalogo_version}), 200

@app.route('/productos', methods=['GET', 'POST'])
def manejar_productos():
    with operaciones_lock:
        productos = leer_json("server_productos", [])
        if _asegurar_ids_productos(productos):
            guardar_json("server_productos", productos)
            marcar_catalogo_cambiado()

        if request.method == 'GET':
            respuesta = jsonify(productos)
            respuesta.headers['X-Catalog-Version'] = str(catalogo_version)
            return respuesta, 200

        datos = request.json or {}
        codigo = _normalizar_codigo(datos.get("codigo"))
        prod_id = str(datos.get("id", "")).strip()
        nombre = str(datos.get("nombre", "")).strip()

        if not codigo or not nombre:
            return jsonify({"error": "Código y nombre son obligatorios"}), 400

        indice_id = None
        if prod_id:
            for i, p in enumerate(productos):
                if str(p.get("id", "")).strip() == prod_id:
                    indice_id = i
                    break

        # Un código identifica el producto comercialmente y no puede duplicarse.
        for i, p in enumerate(productos):
            if _normalizar_codigo(p.get("codigo")) == codigo and i != indice_id:
                return jsonify({"error": "Ya existe otro producto con ese código", "producto_existente": p}), 409

        if indice_id is not None:
            producto_actual = dict(productos[indice_id])
            producto_actual.update(datos)
            producto_actual["id"] = productos[indice_id].get("id")
            producto_actual.pop("pendiente_sync", None)
            productos[indice_id] = producto_actual
            if not guardar_json("server_productos", productos):
                return jsonify({"error": "No se pudo guardar el producto"}), 500
            marcar_catalogo_cambiado()
            return jsonify({"status": "success", "message": "Producto actualizado", "producto": producto_actual, "productos": productos, "catalogo_version": catalogo_version}), 200

        # Compatibilidad: una versión vieja puede enviar solo el código.
        for i, p in enumerate(productos):
            if _normalizar_codigo(p.get("codigo")) == codigo:
                producto_actual = dict(p)
                producto_actual.update(datos)
                producto_actual["id"] = p.get("id") or _nuevo_id_producto()
                producto_actual.pop("pendiente_sync", None)
                productos[i] = producto_actual
                guardar_json("server_productos", productos)
                marcar_catalogo_cambiado()
                return jsonify({"status": "success", "message": "Producto actualizado", "producto": producto_actual, "productos": productos, "catalogo_version": catalogo_version}), 200

        nuevo_prod = dict(datos)
        nuevo_prod["id"] = prod_id or _nuevo_id_producto()
        nuevo_prod.pop("pendiente_sync", None)
        productos.append(nuevo_prod)
        if not guardar_json("server_productos", productos):
            return jsonify({"error": "No se pudo guardar el producto"}), 500
        marcar_catalogo_cambiado()
        return jsonify({"status": "success", "message": "Producto creado", "producto": nuevo_prod, "productos": productos, "catalogo_version": catalogo_version}), 201

@app.route('/productos/<prod_id>', methods=['DELETE'])
def eliminar_producto_servidor(prod_id):
    with operaciones_lock:
        productos = leer_json("server_productos", [])
        objetivo = str(prod_id).strip()
        productos_filtrados = []
        encontrado = False
        for p in productos:
            if str(p.get("id", "")).strip() == objetivo:
                encontrado = True
            else:
                productos_filtrados.append(p)
        if not encontrado:
            return jsonify({"error": "Producto no encontrado por ID"}), 404
        if not guardar_json("server_productos", productos_filtrados):
            return jsonify({"error": "No se pudo eliminar el producto"}), 500
        marcar_catalogo_cambiado()
        return jsonify({"status": "success", "message": f"Producto {prod_id} eliminado de la nube", "productos": productos_filtrados, "catalogo_version": catalogo_version}), 200

# --- RUTAS DE VENTAS Y REVERSIÓN ---
@app.route('/ventas', methods=['GET', 'POST'])
def manejar_ventas():
    if request.method == 'GET':
        return jsonify(leer_json("server_ventas", [])), 200

    nueva_venta = request.json or {}
    operacion_id = str(nueva_venta.get("operacion_id", "")).strip()
    if not operacion_id:
        return jsonify({"error": "Falta operacion_id para garantizar idempotencia"}), 400

    with operaciones_lock:
        ventas = leer_json("server_ventas", [])
        for v in ventas:
            if str(v.get("operacion_id", "")).strip() == operacion_id:
                return jsonify({"status": "success", "id": v.get("id"), "duplicate": True}), 200

        nueva_venta["id"] = max([int(v.get("id", 0)) for v in ventas if str(v.get("id", "")).isdigit()] or [0]) + 1
        nueva_venta["operacion_id"] = operacion_id
        nueva_venta.setdefault("tipo", "Venta")

        lista_items = nueva_venta.get("items", nueva_venta.get("productos", []))
        productos_server = leer_json("server_productos", [])
        errores_stock = []

        for item_vendido in lista_items:
            cod_vendido = str(item_vendido.get("codigo", "")).strip().lower()
            id_vendido = str(item_vendido.get("id", "")).strip()
            cant_vendida = float(item_vendido.get("cantidad", item_vendido.get("cant", 1)))
            if cant_vendida <= 0:
                continue

            encontrado = False
            for prod in productos_server:
                p_cod = str(prod.get("codigo", "")).strip().lower()
                p_id = str(prod.get("id", "")).strip()
                if ((id_vendido and p_id == id_vendido) if id_vendido else (cod_vendido and p_cod == cod_vendido)):
                    stock_actual = float(prod.get("stock", prod.get("stock_disp", 0)))
                    if stock_actual < cant_vendida:
                        errores_stock.append({"codigo": cod_vendido, "stock": stock_actual, "solicitado": cant_vendida})
                    else:
                        prod["stock"] = stock_actual - cant_vendida
                        if "stock_disp" in prod:
                            prod["stock_disp"] = prod["stock"]
                    encontrado = True
                    break
            if not encontrado:
                errores_stock.append({"codigo": cod_vendido, "error": "Producto no encontrado"})

        if errores_stock:
            return jsonify({"error": "Stock insuficiente o producto no encontrado", "detalles": errores_stock}), 409

        ventas.append(nueva_venta)
        if not guardar_json("server_productos", productos_server):
            return jsonify({"error": "No se pudo guardar el inventario"}), 500
        if not guardar_json("server_ventas", ventas):
            # Rollback del inventario si la venta no pudo persistirse.
            for item in lista_items:
                cod = str(item.get("codigo", "")).strip().lower()
                pid = str(item.get("id", "")).strip()
                cant = float(item.get("cantidad", item.get("cant", 1)))
                for prod in productos_server:
                    if ((pid and str(prod.get("id", "")).strip() == pid) if pid else (cod and str(prod.get("codigo", "")).strip().lower() == cod)):
                        prod["stock"] = float(prod.get("stock", 0)) + cant
                        if "stock_disp" in prod:
                            prod["stock_disp"] = prod["stock"]
                        break
            guardar_json("server_productos", productos_server)
            return jsonify({"error": "No se pudo guardar la venta; inventario restaurado"}), 500

        marcar_catalogo_cambiado()
        return jsonify({"status": "success", "id": nueva_venta["id"], "duplicate": False, "productos": productos_server, "catalogo_version": catalogo_version}), 201


# --- RUTAS DE CLIENTES DE CRÉDITO ---
@app.route('/clientes_credito', methods=['GET', 'POST'])
def manejar_clientes_credito():
    if request.method == 'GET':
        return jsonify(leer_json("server_clientes_credito", [])), 200

    datos = request.json or {}
    nombre = str(datos.get("nombre", "")).strip()
    cedula = str(datos.get("cedula", "")).strip()
    if not nombre or not cedula:
        return jsonify({"error": "Nombre y número de cédula son obligatorios"}), 400

    with operaciones_lock:
        clientes = leer_json("server_clientes_credito", [])
        existente = next((c for c in clientes if str(c.get("cedula", "")).strip() == cedula), None)
        if existente:
            existente.update({"nombre": nombre, "cedula": cedula})
            guardar_json("server_clientes_credito", clientes)
            return jsonify({"status": "success", "cliente": existente, "updated": True}), 200

        nuevo = {
            "id": _nuevo_id_cliente_credito(),
            "nombre": nombre,
            "cedula": cedula,
            "fecha_registro": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        clientes.append(nuevo)
        if not guardar_json("server_clientes_credito", clientes):
            return jsonify({"error": "No se pudo guardar el perfil del cliente"}), 500
        return jsonify({"status": "success", "cliente": nuevo}), 201

def _nuevo_id_cliente_credito():
    import uuid
    return uuid.uuid4().hex

@app.route('/ventas_credito', methods=['GET', 'POST'])
def manejar_ventas_credito():
    if request.method == 'GET':
        return jsonify(leer_json("server_ventas_credito", [])), 200

    datos = request.json or {}
    operacion_id = str(datos.get("operacion_id", "")).strip()
    if not operacion_id:
        return jsonify({"error": "Falta operacion_id para garantizar idempotencia"}), 400

    with operaciones_lock:
        creditos = leer_json("server_ventas_credito", [])
        existente = next((c for c in creditos if str(c.get("operacion_id", "")).strip() == operacion_id), None)
        if existente:
            # Permite actualizar estado/datos del mismo crédito sin volver a tocar inventario.
            existente.update({k: v for k, v in datos.items() if k != "id"})
            guardar_json("server_ventas_credito", creditos)
            return jsonify({"status": "success", "id": existente.get("id"), "duplicate": True}), 200

        # Actualización de un crédito existente por id (ej. marcar como Pagado).
        if datos.get("id") is not None:
            for c in creditos:
                if str(c.get("id")) == str(datos.get("id")):
                    estado_anterior = c.get("estado")
                    c.update(datos)
                    guardar_json("server_ventas_credito", creditos)
                    return jsonify({"status": "success", "id": c.get("id"), "updated": True}), 200

        datos["id"] = max([int(c.get("id", 0)) for c in creditos if str(c.get("id", "")).isdigit()] or [0]) + 1
        datos["operacion_id"] = operacion_id
        datos.setdefault("estado", "Pendiente")

        lista_items = datos.get("items", datos.get("productos", []))
        productos_server = leer_json("server_productos", [])
        errores_stock = []

        for item in lista_items:
            cod = str(item.get("codigo", "")).strip().lower()
            pid = str(item.get("id", "")).strip()
            cant = float(item.get("cantidad", item.get("cant", 1)))
            if cant <= 0:
                continue
            encontrado = False
            for prod in productos_server:
                pcod = str(prod.get("codigo", "")).strip().lower()
                pid_prod = str(prod.get("id", "")).strip()
                if ((pid and pid_prod == pid) if pid else (cod and pcod == cod)):
                    stock = float(prod.get("stock", prod.get("stock_disp", 0)))
                    if stock < cant:
                        errores_stock.append({"codigo": cod, "stock": stock, "solicitado": cant})
                    else:
                        prod["stock"] = stock - cant
                        if "stock_disp" in prod:
                            prod["stock_disp"] = prod["stock"]
                    encontrado = True
                    break
            if not encontrado:
                errores_stock.append({"codigo": cod, "error": "Producto no encontrado"})

        if errores_stock:
            return jsonify({"error": "Stock insuficiente o producto no encontrado", "detalles": errores_stock}), 409

        creditos.append(datos)
        if not guardar_json("server_productos", productos_server):
            return jsonify({"error": "No se pudo guardar el inventario"}), 500
        if not guardar_json("server_ventas_credito", creditos):
            # Rollback del inventario si el crédito no pudo persistirse.
            for item in lista_items:
                cod = str(item.get("codigo", "")).strip().lower()
                pid = str(item.get("id", "")).strip()
                cant = float(item.get("cantidad", item.get("cant", 1)))
                for prod in productos_server:
                    if ((pid and str(prod.get("id", "")).strip() == pid) if pid else (cod and str(prod.get("codigo", "")).strip().lower() == cod)):
                        prod["stock"] = float(prod.get("stock", 0)) + cant
                        if "stock_disp" in prod:
                            prod["stock_disp"] = prod["stock"]
                        break
            guardar_json("server_productos", productos_server)
            return jsonify({"error": "No se pudo guardar el crédito; inventario restaurado"}), 500

        marcar_catalogo_cambiado()
        return jsonify({"status": "success", "id": datos["id"], "duplicate": False, "productos": productos_server, "catalogo_version": catalogo_version}), 201

@app.route('/ventas_credito/<int:credito_id>', methods=['DELETE'])
def revertir_venta_credito(credito_id):
    with operaciones_lock:
        creditos = leer_json("server_ventas_credito", [])
        credito = next((c for c in creditos if str(c.get("id")) == str(credito_id)), None)
        if not credito:
            return jsonify({"error": "Crédito ya revertido o no encontrado"}), 404

        items = credito.get("items", credito.get("productos", []))
        productos = leer_json("server_productos", [])
        for item in items:
            cod = str(item.get("codigo", "")).strip().lower()
            pid = str(item.get("id", "")).strip()
            cant = float(item.get("cantidad", item.get("cant", 1)))
            for prod in productos:
                if ((pid and str(prod.get("id", "")).strip() == pid) if pid else (cod and str(prod.get("codigo", "")).strip().lower() == cod)):
                    stock = float(prod.get("stock", prod.get("stock_disp", 0)))
                    prod["stock"] = stock + cant
                    if "stock_disp" in prod:
                        prod["stock_disp"] = prod["stock"]
                    break

        creditos = [c for c in creditos if str(c.get("id")) != str(credito_id)]
        guardar_json("server_productos", productos)
        guardar_json("server_ventas_credito", creditos)
        return jsonify({"status": "success", "message": f"Crédito {credito_id} revertido y stock restaurado"}), 200

@app.route('/ventas/reset', methods=['DELETE'])
def reset_ventas():
    guardar_json("server_ventas", [])
    return jsonify({"status": "success", "message": "Historial de ventas reiniciado"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)


@app.post("/correo/venta")
async def enviar_correo_venta(data: dict):
    """Envía un comprobante por correo y devuelve un resultado explícito.
    Configuración mediante variables de entorno:
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
    """
    import os
    import smtplib
    from email.message import EmailMessage

    destino = str(data.get("correo") or data.get("email") or "").strip()
    asunto = str(data.get("asunto") or "Comprobante de venta")
    cuerpo = str(data.get("cuerpo") or data.get("mensaje") or "")
    if not destino:
        raise HTTPException(status_code=400, detail="Debe indicar un correo de destino.")

    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    usuario = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    remitente = os.getenv("SMTP_FROM", usuario)

    if not usuario or not password or not remitente:
        raise HTTPException(
            status_code=503,
            detail="Correo no configurado en el servidor. Configure SMTP_USER, SMTP_PASSWORD y SMTP_FROM."
        )

    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = remitente
    msg["To"] = destino
    msg.set_content(cuerpo)

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(usuario, password)
            smtp.send_message(msg)
        return {"ok": True, "mensaje": "Correo enviado correctamente.", "destino": destino}
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(
            status_code=502,
            detail="No se pudo autenticar con el servidor de correo. En Gmail use una clave de aplicación."
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"No se pudo enviar el correo: {e}")
