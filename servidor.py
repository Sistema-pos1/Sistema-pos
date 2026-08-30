from flask import Flask, request, jsonify
import os
import smtplib
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
@app.route('/productos', methods=['GET', 'POST'])
def manejar_productos():
    productos = leer_json("server_productos", [])
    if request.method == 'POST':
        nuevo_prod = request.json
        codigo = str(nuevo_prod.get("codigo", "")).strip().lower()
        
        encontrado = False
        for i, p in enumerate(productos):
            if str(p.get("codigo", "")).strip().lower() == codigo:
                productos[i] = nuevo_prod
                encontrado = True
                break
        if not encontrado:
            nuevo_prod["id"] = len(productos) + 1
            productos.append(nuevo_prod)
            
        guardar_json("server_productos", productos)
        return jsonify({"status": "success", "message": "Producto guardado"}), 201
    else:
        return jsonify(productos), 200

@app.route('/productos/<prod_id>', methods=['DELETE'])
def eliminar_producto_servidor(prod_id):
    productos = leer_json("server_productos", [])
    productos_filtrados = []
    encontrado = False

    for p in productos:
        # Validar coincidencia por id (numérico o string) o por código
        match_id = (str(p.get("id", "")) == str(prod_id))
        match_cod = (str(p.get("codigo", "")).strip().lower() == str(prod_id).strip().lower())
        
        if match_id or match_cod:
            encontrado = True
        else:
            productos_filtrados.append(p)

    if not encontrado:
        return jsonify({"error": "Producto no encontrado en la nube"}), 404

    guardar_json("server_productos", productos_filtrados)
    return jsonify({"status": "success", "message": f"Producto {prod_id} eliminado de la nube"}), 200

# --- RUTAS DE VENTAS Y REVERSIÓN ---
@app.route('/ventas', methods=['GET', 'POST'])
def manejar_ventas():
    ventas = leer_json("server_ventas", [])
    if request.method == 'POST':
        nueva_venta = request.json
        nueva_venta["id"] = len(ventas) + 1
        ventas.append(nueva_venta)
        guardar_json("server_ventas", ventas)
        
        lista_items = nueva_venta.get("items", nueva_venta.get("productos", []))
        if lista_items:
            productos_server = leer_json("server_productos", [])
            for item_vendido in lista_items:
                cod_vendido = str(item_vendido.get("codigo", "")).strip().lower()
                cant_vendida = float(item_vendido.get("cantidad", item_vendido.get("cant", 1)))
                
                for p in productos_server:
                    if str(p.get("codigo", "")).strip().lower() == cod_vendido:
                        stock_actual = float(p.get("stock", p.get("stock_disp", 0)))
                        p["stock"] = max(0.0, stock_actual - cant_vendida)
                        if "stock_disp" in p:
                            p["stock_disp"] = p["stock"]
                        break
            guardar_json("server_productos", productos_server)

        return jsonify({"status": "success", "id": nueva_venta["id"]}), 201
    else:
        return jsonify(ventas), 200

@app.route('/ventas/<int:venta_id>', methods=['DELETE'])
def eliminar_venta(venta_id):
    ventas = leer_json("server_ventas", [])
    
    venta_a_revertir = None
    ventas_filtradas = []
    
    for v in ventas:
        if int(v.get("id", 0)) == int(venta_id) or str(v.get("id")) == str(venta_id):
            venta_a_revertir = v
        else:
            ventas_filtradas.append(v)
            
    if not venta_a_revertir:
        return jsonify({"error": "Venta ya revertida o no encontrada"}), 404

    lista_items = venta_a_revertir.get("items", [])
    if not lista_items:
        lista_items = venta_a_revertir.get("productos", [])

    if lista_items:
        productos_server = leer_json("server_productos", [])
        
        for item_vendido in lista_items:
            cod_vendido = str(item_vendido.get("codigo", "")).strip().lower()
            id_vendido = str(item_vendido.get("id", "")).strip()
            cant_vendida = float(item_vendido.get("cantidad", item_vendido.get("cant", 1)))
            
            actualizado = False
            for p in productos_server:
                if actualizado:
                    break
                    
                p_cod = str(p.get("codigo", "")).strip().lower()
                p_id = str(p.get("id", "")).strip()
                
                match_id = (p_id and id_vendido and p_id == id_vendido)
                match_cod = (p_cod and cod_vendido and p_cod == cod_vendido)
                
                if match_id or match_cod:
                    stock_actual = float(p.get("stock", p.get("stock_disp", 0)))
                    p["stock"] = stock_actual + cant_vendida
                    if "stock_disp" in p:
                        p["stock_disp"] = p["stock"]
                    actualizado = True
                    break
                    
        guardar_json("server_productos", productos_server)

    guardar_json("server_ventas", ventas_filtradas)
    return jsonify({"status": "success", "message": f"Venta {venta_id} revertida y stock restaurado"}), 200

@app.route('/ventas/reset', methods=['DELETE'])
def reset_ventas():
    guardar_json("server_ventas", [])
    return jsonify({"status": "success", "message": "Historial de ventas reiniciado"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
