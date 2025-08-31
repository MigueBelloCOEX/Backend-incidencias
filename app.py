from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import psycopg2
from psycopg2 import pool
import os
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime
import re
import requests
import zipfile
import io

app = Flask(__name__)
CORS(app)

# Configuración para Render
app.config['UPLOAD_FOLDER'] = 'static/kml_files'

# Obtener la URL de conexión de la base de datos de las variables de entorno de Render
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL no está configurada en las variables de entorno.")

# Crear directorios si no existen
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('static/fotos', exist_ok=True)
os.makedirs('P.K', exist_ok=True)

# Pool de conexiones
connection_pool = None

def init_connection_pool():
    global connection_pool
    try:
        connection_pool = pool.SimpleConnectionPool(
            1,  # min connections
            5,  # max connections (ajusta según tu plan de Render)
            DATABASE_URL
        )
        print("Connection pool created successfully")
    except Exception as e:
        print(f"Error creating connection pool: {e}")
        raise

def get_db_connection():
    if connection_pool is None:
        init_connection_pool()
    return connection_pool.getconn()

def release_db_connection(conn):
    if connection_pool:
        connection_pool.putconn(conn)

def download_kml_files_from_github():
    """
    Descarga archivos KML via GitHub Pages (no requiere token)
    """
    try:
        # URL de GitHub Pages
        github_pages_url = "https://miguebellocoex.github.io/Backend-incidencias/P.K/"
        
        # Lista de archivos KML esperados
        kml_files = ["CA-35.kml", "CA-36.kml"]  # Ajusta según tus archivos
        
        downloaded_count = 0
        for file_name in kml_files:
            try:
                download_url = f"{github_pages_url}{file_name}"
                print(f"📥 Descargando: {download_url}")
                
                file_response = requests.get(download_url)
                if file_response.status_code == 200:
                    file_path = os.path.join('P.K', file_name)
                    with open(file_path, 'wb') as f:
                        f.write(file_response.content)
                    print(f"✅ Descargado: {file_name}")
                    downloaded_count += 1
                else:
                    print(f"⚠️  No se pudo descargar: {file_name} (Status: {file_response.status_code})")
                    
            except Exception as e:
                print(f"❌ Error con {file_name}: {str(e)}")
        
        print(f"🎉 {downloaded_count} archivos KML descargados")
        return downloaded_count > 0
        
    except Exception as e:
        print(f"❌ Error descargando archivos: {str(e)}")
        return False

def setup_database():
    """
    Crea las tablas de la base de datos si no existen y carga los datos iniciales.
    """
    conn = None
    try:
        # Primero intentar descargar los archivos KML desde GitHub
        print("🔄 Intentando descargar archivos KML desde GitHub...")
        
        kml_downloaded = download_kml_files_from_github()
        
        if not kml_downloaded:
            print("⚠️  No se pudieron descargar los archivos KML desde GitHub")
            print("🔍 Verificando si existen archivos KML locales en la carpeta P.K...")
            
            # Verificar si ya existen archivos KML en la carpeta P.K
            if os.path.exists('P.K'):
                kml_files = [f for f in os.listdir('P.K') if f.endswith('.kml')]
                if kml_files:
                    print(f"✅ Se encontraron {len(kml_files)} archivos KML locales")
                else:
                    print("❌ No hay archivos KML disponibles en la carpeta P.K")
                    print("💡 La aplicación funcionará pero necesitará archivos KML para interpolar coordenadas")
            else:
                print("❌ La carpeta P.K no existe")
                os.makedirs('P.K', exist_ok=True)
                print("📁 Carpeta P.K creada")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Crear tablas si no existen
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS puntos_carretera (
                id SERIAL PRIMARY KEY,
                carretera TEXT,
                kilometro REAL,
                kilometro_texto TEXT,
                latitud REAL,
                longitud REAL,
                UNIQUE (kilometro_texto, carretera)
            );
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS incidencias (
                id TEXT PRIMARY KEY,
                carretera TEXT,
                kilometro TEXT,
                latitud REAL,
                longitud REAL,
                tipo TEXT,
                fecha TEXT,
                descripcion TEXT,
                kml_file TEXT
            );
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fotos_incidencia (
                id SERIAL PRIMARY KEY,
                incidencia_id TEXT,
                ruta_foto TEXT,
                FOREIGN KEY(incidencia_id) REFERENCES incidencias(id)
            );
        ''')
        
        conn.commit()
        cursor.close()
        
        # Cargar datos desde los archivos KML si existen
        if os.path.exists('P.K'):
            kml_files = [f for f in os.listdir('P.K') if f.endswith('.kml')]
            if kml_files:
                print(f"📊 Cargando datos desde {len(kml_files)} archivos KML...")
                for file_name in kml_files:
                    kml_path = os.path.join('P.K', file_name)
                    load_kml_data_into_db(kml_path)
            else:
                print("ℹ️  No hay archivos KML para cargar en la base de datos")
        
        print("✅ Configuración de base de datos completada exitosamente")
        
    except Exception as e:
        print(f"❌ Error en setup_database: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            release_db_connection(conn)

def load_kml_data_into_db(kml_path):
    """
    Carga los puntos kilométricos desde un archivo KML a la base de datos.
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        tree = ET.parse(kml_path)
        root = tree.getroot()
        
        # Namespace de KML
        ns = {'kml': 'http://www.opengis.net/kml/2.2'}
        
        print(f"📖 Leyendo archivo: {kml_path}")
        
        # Obtener el nombre de la carretera del nombre del archivo
        carretera = os.path.basename(kml_path).replace('.kml', '').upper()
        
        # Buscar Placemarks que contienen los puntos kilométricos
        for placemark in root.findall('.//kml:Placemark', ns):
            name_elem = placemark.find('kml:name', ns)
            point_elem = placemark.find('kml:Point', ns)
            
            if name_elem is not None and point_elem is not None:
                name_text = name_elem.text.strip()
                coordinates_elem = point_elem.find('kml:coordinates', ns)
                
                if coordinates_elem is not None:
                    coords = coordinates_elem.text.strip().split(',')
                    if len(coords) >= 2:
                        longitud = float(coords[0])
                        latitud = float(coords[1])
                        
                        print(f"📍 Punto encontrado: {name_text} -> {latitud}, {longitud}")
                        
                        # Parsear el nombre para obtener el PK (formato: "4 + 300")
                        # Nuevo patrón regex más simple
                        match = re.search(r'(\d+)\s*\+\s*(\d+)', name_text)
                        if match:
                            km_entero = match.group(1)
                            km_metros = match.group(2)
                            kilometro_texto = f"{km_entero}+{km_metros}"
                            
                            print(f"   Carretera: {carretera}, PK: {kilometro_texto}")
                            
                            # Convertir PK a metros para el campo 'kilometro'
                            kilometro = int(km_entero) * 1000 + int(km_metros)
                            
                            # Insertar en base de datos
                            cursor.execute('''
                                INSERT INTO puntos_carretera (carretera, kilometro, kilometro_texto, latitud, longitud)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (kilometro_texto, carretera) DO NOTHING;
                            ''', (carretera, kilometro, kilometro_texto, latitud, longitud))
                        else:
                            print(f"⚠️  No se pudo parsear: {name_text}")
        
        conn.commit()
        print(f"✅ Datos cargados exitosamente desde {kml_path}")
        
    except Exception as e:
        print(f"❌ Error al cargar datos del KML {kml_path}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            release_db_connection(conn)

def obtener_coordenadas_interpoladas(carretera, punto_kilometrico_str):
    conn = None
    try:
        print(f"🔍 Buscando coordenadas para: {carretera} - {punto_kilometrico_str}")
        
        # Parsear el punto kilométrico
        match = re.search(r'(\d+)\+(\d+)', punto_kilometrico_str)
        if match:
            km_entero = int(match.group(1))
            km_metros = int(match.group(2))
            metros_totales = km_entero * 1000 + km_metros
            print(f"📏 Metros totales: {metros_totales}")
        else:
            try:
                kilometro_decimal = float(punto_kilometrico_str)
                metros_totales = kilometro_decimal * 1000
                print(f"📏 Metros totales (decimal): {metros_totales}")
            except ValueError:
                print("❌ Formato de PK inválido")
                return None, None

        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Verificar si hay datos para esta carretera
        cursor.execute('''
            SELECT COUNT(*) FROM puntos_carretera WHERE carretera = %s
        ''', (carretera.upper(),))
        count = cursor.fetchone()[0]
        print(f"📊 Puntos en DB para {carretera}: {count}")
        
        if count == 0:
            print("❌ No hay puntos de referencia para esta carretera")
            return None, None
        
        # Buscar puntos de referencia
        cursor.execute('''
            SELECT kilometro, kilometro_texto, latitud, longitud 
            FROM puntos_carretera 
            WHERE carretera = %s AND kilometro <= %s
            ORDER BY kilometro DESC LIMIT 1
        ''', (carretera.upper(), metros_totales))
        punto_inicial = cursor.fetchone()
        
        cursor.execute('''
            SELECT kilometro, kilometro_texto, latitud, longitud 
            FROM puntos_carretera 
            WHERE carretera = %s AND kilometro >= %s
            ORDER BY kilometro ASC LIMIT 1
        ''', (carretera.upper(), metros_totales))
        punto_final = cursor.fetchone()
        
        cursor.close()

        if punto_inicial:
            print(f"📌 Punto inicial: {punto_inicial[1]} ({punto_inicial[0]}m)")
        if punto_final:
            print(f"📌 Punto final: {punto_final[1]} ({punto_final[0]}m)")

        if punto_inicial and punto_final:
            km1, texto1, lat1, lon1 = punto_inicial
            km2, texto2, lat2, lon2 = punto_final
            
            if km1 == metros_totales:
                print("✅ Coincidencia exacta con punto inicial")
                return lat1, lon1
            if km2 == metros_totales:
                print("✅ Coincidencia exacta con punto final")
                return lat2, lon2
            
            if km2 != km1:
                proporcion = (metros_totales - km1) / (km2 - km1)
                latitud_interpolada = lat1 + (lat2 - lat1) * proporcion
                longitud_interpolada = lon1 + (lon2 - lon1) * proporcion
                print(f"📐 Interpolación: {proporcion:.3f}")
                return latitud_interpolada, longitud_interpolada
            else:
                print("✅ Mismo punto, usando coordenadas del punto")
                return lat1, lon1
        elif punto_inicial:
            print("⚠️  Solo punto inicial encontrado, usando sus coordenadas")
            return punto_inicial[2], punto_inicial[3]
        elif punto_final:
            print("⚠️  Solo punto final encontrado, usando sus coordenadas")
            return punto_final[2], punto_final[3]
        
        print("❌ No se encontraron puntos de referencia")
        return None, None
        
    except Exception as e:
        print(f"❌ Error en interpolación: {e}")
        import traceback
        traceback.print_exc()
        return None, None
    finally:
        if conn:
            release_db_connection(conn)

def crear_kml_incidencia(incidencia_id, carretera, kilometro, tipo, latitud, longitud, descripcion, fotos_urls=None):
    try:
        if fotos_urls is None:
            fotos_urls = []
        
        # Determinar el color según el tipo de incidencia
        if tipo.lower() == "accidente":
            color = "ff0000"  # Rojo
        elif tipo.lower() == "obra":
            color = "ffff00"  # Amarillo
        else:
            color = "0000ff"  # Azul

        # Crear contenido HTML para las fotos
        fotos_html = ""
        if fotos_urls:
            fotos_html = "<h4>Fotos:</h4><div style='display: flex; flex-wrap: wrap;'>"
            for foto_url in fotos_urls:
                fotos_html += f'<img src="{foto_url}" width="150" style="margin: 5px; border: 1px solid #ccc;">'
            fotos_html += "</div>"
        else:
            fotos_html = "<p>No hay fotos disponibles</p>"

        # Crear KML SIMPLIFICADO y compatible
        kml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Incidencia {incidencia_id}</name>
    <Style id="iconStyle">
      <IconStyle>
        <color>ff{color}</color>
        <scale>1.3</scale>
        <Icon>
          <href>http://maps.google.com/mapfiles/kml/pushpin/red-pushpin.png</href>
        </Icon>
        <hotSpot x="20" y="2" xunits="pixels" yunits="pixels"/>
      </IconStyle>
      <BalloonStyle>
        <text><![CDATA[
          <h2 style="color: #{color}; margin-bottom: 10px;">Incidencia Vial {incidencia_id}</h2>
          <table style="width: 100%; border-collapse: collapse;">
            <tr><td style="padding: 5px; border-bottom: 1px solid #eee;"><strong>Carretera:</strong></td><td style="padding: 5px; border-bottom: 1px solid #eee;">{carretera}</td></tr>
            <tr><td style="padding: 5px; border-bottom: 1px solid #eee;"><strong>Punto Kilométrico:</strong></td><td style="padding: 5px; border-bottom: 1px solid #eee;">{kilometro}</td></tr>
            <tr><td style="padding: 5px; border-bottom: 1px solid #eee;"><strong>Tipo:</strong></td><td style="padding: 5px; border-bottom: 1px solid #eee;">{tipo}</td></tr>
            <tr><td style="padding: 5px; border-bottom: 1px solid #eee;"><strong>Fecha:</strong></td><td style="padding: 5px; border-bottom: 1px solid #eee;">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
            <tr><td style="padding: 5px; border-bottom: 1px solid #eee;"><strong>Coordenadas:</strong></td><td style="padding: 5px; border-bottom: 1px solid #eee;">{latitud:.6f}, {longitud:.6f}</td></tr>
          </table>
          <h3 style="margin-top: 15px;">Descripción:</h3>
          <p style="background: #f9f9f9; padding: 10px; border-radius: 5px;">{descripcion}</p>
          {fotos_html}
        ]]></text>
      </BalloonStyle>
    </Style>
    <Placemark>
      <name>Incidencia {incidencia_id}</name>
      <description><![CDATA[
        <h2 style="color: #{color};">Incidencia Vial {incidencia_id}</h2>
        <p><strong>Carretera:</strong> {carretera}</p>
        <p><strong>Punto Kilométrico:</strong> {kilometro}</p>
        <p><strong>Tipo:</strong> {tipo}</p>
        <p><strong>Fecha:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <p><strong>Coordenadas:</strong> {latitud:.6f}, {longitud:.6f}</p>
        <p><strong>Descripción:</strong> {descripcion}</p>
        {fotos_html}
      ]]></description>
      <styleUrl>#iconStyle</styleUrl>
      <Point>
        <coordinates>{longitud},{latitud},0</coordinates>
      </Point>
    </Placemark>
  </Document>
</kml>"""
        
        # Guardar archivo KML
        kml_filename = f"incidencia_{incidencia_id}.kml"
        kml_path = os.path.join(app.config['UPLOAD_FOLDER'], kml_filename)
        
        with open(kml_path, 'w', encoding='utf-8') as f:
            f.write(kml_content)
        
        print(f"✅ KML creado: {kml_path}")
        
        # Verificar que el archivo se creó correctamente
        if os.path.exists(kml_path):
            with open(kml_path, 'r', encoding='utf-8') as f:
                content = f.read()
                print(f"📄 Contenido del KML (primeras 500 chars): {content[:500]}...")
        
        return kml_filename
        
    except Exception as e:
        print(f"❌ Error creando KML: {e}")
        import traceback
        traceback.print_exc()
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/incidencias', methods=['GET'])
def get_incidencias():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT i.*, COUNT(f.id) as num_fotos 
            FROM incidencias i 
            LEFT JOIN fotos_incidencia f ON i.id = f.incidencia_id 
            GROUP BY i.id 
            ORDER BY i.fecha DESC
        ''')
        
        incidencias = []
        for row in cursor.fetchall():
            incidencia = {
                "id": row[0], "carretera": row[1], "kilometro": row[2], 
                "latitud": row[3], "longitud": row[4], "tipo": row[5], 
                "fecha": row[6], "descripcion": row[7], "kml_file": row[8],
                "num_fotos": row[9]
            }
            # Generar enlace público al KML
            if incidencia['kml_file']:
                base_url = request.host_url.rstrip('/')
                incidencia['kml_url'] = f"{base_url}/static/kml_files/{incidencia['kml_file']}"
                incidencia['google_earth_url'] = f"https://earth.google.com/web/search/{incidencia['latitud']},{incidencia['longitud']}"
                incidencia['google_maps_url'] = f"https://www.google.com/maps?q={incidencia['latitud']},{incidencia['longitud']}"
            incidencias.append(incidencia)
        
        cursor.close()
        return jsonify(incidencias)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

@app.route('/api/incidencias', methods=['POST'])
def crear_incidencia():
    conn = None
    try:
        # Manejar tanto JSON como form-data (para fotos)
        if request.content_type.startswith('multipart/form-data'):
            data = request.form.to_dict()
            files = request.files.getlist('fotos')
        else:
            data = request.get_json()
            files = []
        
        incidencia_id = data.get('id')
        carretera = data.get('carretera')
        kilometro = data.get('kilometro')
        tipo = data.get('tipo')
        descripcion = data.get('descripcion', '')
        
        # Validar campos requeridos
        if not all([incidencia_id, carretera, kilometro, tipo]):
            return jsonify({'error': 'Faltan campos requeridos'}), 400
        
        # Obtener coordenadas
        latitud, longitud = obtener_coordenadas_interpoladas(carretera, kilometro)
        if not latitud or not longitud:
            return jsonify({'error': 'No se pudieron obtener las coordenadas para la carretera y PK especificados'}), 400
        
        # Procesar fotos si las hay
        fotos_urls = []
        if files:
            for file in files:
                if file.filename:
                    filename = f"{incidencia_id}_{file.filename}"
                    file_path = os.path.join('static/fotos', filename)
                    file.save(file_path)
                    base_url = request.host_url.rstrip('/')
                    fotos_urls.append(f"{base_url}/static/fotos/{filename}")
        
        # Crear KML con fotos
        kml_filename = crear_kml_incidencia(incidencia_id, carretera, kilometro, tipo, latitud, longitud, descripcion, fotos_urls)
        
        if not kml_filename:
            return jsonify({'error': 'Error creando archivo KML'}), 500
        
        # Guardar en base de datos
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO incidencias (id, carretera, kilometro, latitud, longitud, tipo, fecha, descripcion, kml_file)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (incidencia_id, carretera.upper(), kilometro, latitud, longitud, tipo, 
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'), descripcion, kml_filename))
            
            # Guardar información de fotos en la base de datos
            for foto_url in fotos_urls:
                cursor.execute('''
                    INSERT INTO fotos_incidencia (incidencia_id, ruta_foto)
                    VALUES (%s, %s)
                ''', (incidencia_id, foto_url))
            
            conn.commit()
        except psycopg2.IntegrityError:
            return jsonify({'error': 'El ID de incidencia ya existe'}), 400
        finally:
            cursor.close()
        
        # Generar URLs públicas
        base_url = request.host_url.rstrip('/')
        kml_url = f"{base_url}/static/kml_files/{kml_filename}"
        google_earth_url = f"https://earth.google.com/web/search/{latitud},{longitud}"
        google_maps_url = f"https://www.google.com/maps?q={latitud},{longitud}"
        
        return jsonify({
            'success': True,
            'kml_url': kml_url,
            'google_earth_url': google_earth_url,
            'google_maps_url': google_maps_url,
            'incidencia': {
                'id': incidencia_id,
                'carretera': carretera,
                'kilometro': kilometro,
                'tipo': tipo,
                'latitud': latitud,
                'longitud': longitud,
                'descripcion': descripcion,
                'fotos': fotos_urls
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

@app.route('/static/kml_files/<filename>')
def serve_kml(filename):
    try:
        kml_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Verificar que el archivo existe
        if not os.path.exists(kml_path):
            return jsonify({'error': 'Archivo KML no encontrado'}), 404
        
        # Leer el contenido para verificar que es válido
        with open(kml_path, 'r', encoding='utf-8') as f:
            content = f.read()
            print(f"📤 Sirviendo KML: {filename}, tamaño: {len(content)} caracteres")
        
        response = send_file(kml_path)
        response.headers['Content-Type'] = 'application/vnd.google-earth.kml+xml'
        response.headers['Content-Disposition'] = f'inline; filename={filename}'
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
        
    except Exception as e:
        print(f"❌ Error sirviendo KML: {e}")
        return jsonify({'error': f'Error al servir el archivo: {str(e)}'}), 500

@app.route('/static/fotos/<filename>')
def serve_foto(filename):
    try:
        return send_file(os.path.join('static/fotos', filename))
    except FileNotFoundError:
        return jsonify({'error': 'Foto no encontrada'}), 404

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy'})

@app.route('/api/debug/carreteras')
def debug_carreteras():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Obtener todas las carreteras disponibles
        cursor.execute('SELECT DISTINCT carretera FROM puntos_carretera ORDER BY carretera')
        carreteras = [row[0] for row in cursor.fetchall()]
        
        # Obtener conteo de puntos por carretera
        cursor.execute('''
            SELECT carretera, COUNT(*) as puntos 
            FROM puntos_carretera 
            GROUP BY carretera 
            ORDER BY carretera
        ''')
        stats = [{'carretera': row[0], 'puntos': row[1]} for row in cursor.fetchall()]
        
        cursor.close()
        
        return jsonify({
            'carreteras_disponibles': carreteras,
            'estadisticas': stats,
            'total_carreteras': len(carreteras),
            'total_puntos': sum([s['puntos'] for s in stats])
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

@app.route('/api/debug/incidencia/<incidencia_id>')
def debug_incidencia(incidencia_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM incidencias WHERE id = %s', (incidencia_id,))
        incidencia = cursor.fetchone()
        
        cursor.execute('SELECT * FROM fotos_incidencia WHERE incidencia_id = %s', (incidencia_id,))
        fotos = cursor.fetchall()
        
        cursor.close()
        
        if not incidencia:
            return jsonify({'error': 'Incidencia no encontrada'}), 404
        
        # Verificar si el archivo KML existe
        kml_filename = f"incidencia_{incidencia_id}.kml"
        kml_path = os.path.join(app.config['UPLOAD_FOLDER'], kml_filename)
        kml_exists = os.path.exists(kml_path)
        
        kml_content = None
        if kml_exists:
            with open(kml_path, 'r', encoding='utf-8') as f:
                kml_content = f.read()
        
        return jsonify({
            'incidencia': {
                'id': incidencia[0],
                'carretera': incidencia[1],
                'kilometro': incidencia[2],
                'latitud': incidencia[3],
                'longitud': incidencia[4],
                'tipo': incidencia[5],
                'fecha': incidencia[6],
                'descripcion': incidencia[7],
                'kml_file': incidencia[8]
            },
            'fotos': [{'id': foto[0], 'ruta': foto[2]} for foto in fotos],
            'kml': {
                'exists': kml_exists,
                'path': kml_path,
                'content_preview': kml_content[:1000] + '...' if kml_content else None,
                'url': f"{request.host_url.rstrip('/')}/static/kml_files/{kml_filename}" if kml_exists else None
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

# Ruta para forzar la descarga de KML desde GitHub
@app.route('/api/descargar-kml', methods=['POST'])
def descargar_kml():
    try:
        if download_kml_files_from_github():
            return jsonify({'success': True, 'message': 'Archivos KML descargados exitosamente'})
        else:
            return jsonify({'success': False, 'message': 'Error al descargar archivos KML'}), 500
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

# Ruta para reiniciar la base de datos
@app.route('/api/reset-database', methods=['POST'])
def reset_database():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Eliminar tablas existentes
        cursor.execute('DROP TABLE IF EXISTS fotos_incidencia CASCADE')
        cursor.execute('DROP TABLE IF EXISTS incidencias CASCADE')
        cursor.execute('DROP TABLE IF EXISTS puntos_carretera CASCADE')
        
        conn.commit()
        cursor.close()
        
        # Volver a crear las tablas
        setup_database()
        
        return jsonify({'success': True, 'message': 'Base de datos reiniciada exitosamente'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

# Inicializar la aplicación
try:
    init_connection_pool()
    setup_database()
    print("Application initialized successfully")
except Exception as e:
    print(f"Error initializing application: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)

