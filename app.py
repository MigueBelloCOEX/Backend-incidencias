from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import psycopg2
from psycopg2 import pool
import os
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime
import re

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

def setup_database():
    """
    Crea las tablas de la base de datos si no existen y carga los datos iniciales.
    """
    conn = None
    try:
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
        
        # Verificar si ya hay datos en la tabla
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM puntos_carretera')
        count = cursor.fetchone()[0]
        cursor.close()
        
        print(f"Puntos de carretera en BD: {count}")
        
        # Solo cargar datos si la tabla está vacía
        if count == 0:
            print("Cargando datos desde archivos KML...")
            kml_dir = 'P.K'
            if os.path.exists(kml_dir):
                for file_name in os.listdir(kml_dir):
                    if file_name.endswith('.kml'):
                        kml_path = os.path.join(kml_dir, file_name)
                        print(f"Procesando: {kml_path}")
                        load_kml_data_into_db(kml_path)
            else:
                print(f"Directorio {kml_dir} no existe")
        else:
            print("La tabla ya contiene datos, omitiendo carga inicial")
        
    except Exception as e:
        print(f"Error setting up database: {e}")
        raise
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
        
        # Buscar Placemarks que contienen los puntos kilométricos
        for placemark in root.findall('.//{http://www.opengis.net/kml/2.2}Placemark'):
            name_elem = placemark.find('{http://www.opengis.net/kml/2.2}name')
            point_elem = placemark.find('{http://www.opengis.net/kml/2.2}Point')
            
            if name_elem is not None and point_elem is not None:
                name_text = name_elem.text.strip()
                coordinates_elem = point_elem.find('{http://www.opengis.net/kml/2.2}coordinates')
                
                if coordinates_elem is not None:
                    coords = coordinates_elem.text.strip().split(',')
                    longitud = float(coords[0])
                    latitud = float(coords[1])
                    
                    # Parsear el nombre para obtener la carretera y el PK
                    match = re.search(r'([A-Z]+-\d+)\s+([\d+]+)', name_text)
                    if match:
                        carretera = match.group(1)
                        kilometro_texto = match.group(2)
                        
                        # Convertir PK a metros para el campo 'kilometro'
                        km_partes = kilometro_texto.split('+')
                        km_entero = int(km_partes[0])
                        km_metros = int(km_partes[1])
                        kilometro = km_entero * 1000 + km_metros
                        
                        # Usamos INSERT ON CONFLICT para evitar duplicados si el script se ejecuta más de una vez
                        cursor.execute('''
                            INSERT INTO puntos_carretera (carretera, kilometro, kilometro_texto, latitud, longitud)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (kilometro_texto, carretera) DO NOTHING;
                        ''', (carretera, kilometro, kilometro_texto, latitud, longitud))
                        
        conn.commit()
        print(f"Datos cargados exitosamente desde {kml_path}")
        
    except Exception as e:
        print(f"Error al cargar datos del KML {kml_path}: {e}")
    finally:
        if conn:
            release_db_connection(conn)

def obtener_coordenadas_interpoladas(carretera, punto_kilometrico_str):
    conn = None
    try:
        print(f"Buscando coordenadas para: {carretera} - PK: {punto_kilometrico_str}")
        
        # Parsear el punto kilométrico
        match = re.search(r'(\d+)\+(\d+)', punto_kilometrico_str)
        if match:
            km_entero = int(match.group(1))
            km_metros = int(match.group(2))
            metros_totales = km_entero * 1000 + km_metros
            print(f"PK parseado: {km_entero} km + {km_metros} m = {metros_totales} m totales")
        else:
            try:
                kilometro_decimal = float(punto_kilometrico_str)
                metros_totales = kilometro_decimal * 1000
                print(f"PK decimal: {kilometro_decimal} km = {metros_totales} m totales")
            except ValueError:
                print(f"Formato de PK inválido: {punto_kilometrico_str}")
                return None, None

        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Verificar si existen puntos para esta carretera
        cursor.execute('''
            SELECT COUNT(*) FROM puntos_carretera WHERE carretera = %s
        ''', (carretera.upper(),))
        count = cursor.fetchone()[0]
        print(f"Puntos encontrados para carretera {carretera}: {count}")
        
        if count == 0:
            # Listar todas las carreteras disponibles
            cursor.execute('SELECT DISTINCT carretera FROM puntos_carretera ORDER BY carretera')
            carreteras_disponibles = [row[0] for row in cursor.fetchall()]
            print(f"Carreteras disponibles: {carreteras_disponibles}")
            return None, None
        
        # Buscar puntos de referencia
        cursor.execute('''
            SELECT kilometro, latitud, longitud, kilometro_texto
            FROM puntos_carretera 
            WHERE carretera = %s AND kilometro <= %s
            ORDER BY kilometro DESC LIMIT 1
        ''', (carretera.upper(), metros_totales))
        punto_inicial = cursor.fetchone()
        
        cursor.execute('''
            SELECT kilometro, latitud, longitud, kilometro_texto
            FROM puntos_carretera 
            WHERE carretera = %s AND kilometro >= %s
            ORDER BY kilometro ASC LIMIT 1
        ''', (carretera.upper(), metros_totales))
        punto_final = cursor.fetchone()
        
        cursor.close()

        print(f"Punto inicial: {punto_inicial}")
        print(f"Punto final: {punto_final}")

        if punto_inicial and punto_final:
            km1, lat1, lon1, texto1 = punto_inicial
            km2, lat2, lon2, texto2 = punto_final
            
            print(f"Puntos encontrados: {texto1} ({km1}m) y {texto2} ({km2}m)")
            
            if km1 == metros_totales:
                print(f"Coincidencia exacta con {texto1}")
                return lat1, lon1
            if km2 == metros_totales:
                print(f"Coincidencia exacta con {texto2}")
                return lat2, lon2
            
            if km2 != km1:
                proporcion = (metros_totales - km1) / (km2 - km1)
                latitud_interpolada = lat1 + (lat2 - lat1) * proporcion
                longitud_interpolada = lon1 + (lon2 - lon1) * proporcion
                print(f"Interpolación: proporción {proporcion:.3f}, lat: {latitud_interpolada:.6f}, lon: {longitud_interpolada:.6f}")
                return latitud_interpolada, longitud_interpolada
            else:
                print("Puntos idénticos, usando punto inicial")
                return lat1, lon1
        else:
            print("No se encontraron puntos de referencia para interpolación")
            return None, None
        
    except Exception as e:
        print(f"Error en interpolación: {e}")
        return None, None
    finally:
        if conn:
            release_db_connection(conn)

def crear_kml_incidencia(incidencia_id, carretera, kilometro, tipo, latitud, longitud, descripcion):
    try:
        # Crear elemento KML
        kml = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
        document = ET.SubElement(kml, "Document")
        
        placemark = ET.SubElement(document, "Placemark")
        name = ET.SubElement(placemark, "name")
        name.text = f"Incidencia {incidencia_id}"
        
        description_elem = ET.SubElement(placemark, "description")
        
        # Crear contenido HTML
        desc_html = f"""
        <h3>Incidencia Vial {incidencia_id}</h3>
        <p><strong>Carretera:</strong> {carretera}</p>
        <p><strong>Punto Kilométrico:</strong> {kilometro}</p>
        <p><strong>Tipo:</strong> {tipo}</p>
        <p><strong>Fecha:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <p><strong>Descripción:</strong> {descripcion}</p>
        <p><strong>Coordenadas:</strong> {latitud:.6f}, {longitud:.6f}</p>
        """
        
        description_elem.text = f"<![CDATA[{desc_html}]]>"
        
        # Punto con coordenadas
        point = ET.SubElement(placemark, "Point")
        coordinates = ET.SubElement(point, "coordinates")
        coordinates.text = f"{longitud},{latitud},0"
        
        # Estilo según tipo
        style = ET.SubElement(placemark, "Style")
        icon_style = ET.SubElement(style, "IconStyle")
        icon = ET.SubElement(icon_style, "Icon")
        href = ET.SubElement(icon, "href")
        
        if tipo == "accidente":
            href.text = "http://maps.google.com/mapfiles/kml/pushpin/red-pushpin.png"
        elif tipo == "obra":
            href.text = "http://maps.google.com/mapfiles/kml/pushpin/ylw-pushpin.png"
        else:
            href.text = "http://maps.google.com/mapfiles/kml/pushpin/blue-pushpin.png"
        
        # Convertir a XML
        rough_string = ET.tostring(kml, 'utf-8')
        reparsed = minidom.parseString(rough_string)
        kml_content = reparsed.toprettyxml(indent="  ")
        
        # Guardar archivo KML
        kml_filename = f"incidencia_{incidencia_id}.kml"
        kml_path = os.path.join(app.config['UPLOAD_FOLDER'], kml_filename)
        
        with open(kml_path, 'w', encoding='utf-8') as f:
            f.write(kml_content)
        
        return kml_filename
        
    except Exception as e:
        print(f"Error creando KML: {e}")
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
        data = request.get_json()
        
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
        
        # Crear KML
        kml_filename = crear_kml_incidencia(incidencia_id, carretera, kilometro, tipo, latitud, longitud, descripcion)
        
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
                'descripcion': descripcion
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

@app.route('/api/debug/carreteras', methods=['GET'])
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
        estadisticas = []
        for row in cursor.fetchall():
            estadisticas.append({
                'carretera': row[0],
                'puntos': row[1],
                'rango_km': obtener_rango_kilometros(row[0])
            })
        
        cursor.close()
        
        return jsonify({
            'carreteras_disponibles': carreteras,
            'estadisticas': estadisticas,
            'total_carreteras': len(carreteras),
            'total_puntos': sum(stat['puntos'] for stat in estadisticas)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

def obtener_rango_kilometros(carretera):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT MIN(kilometro_texto), MAX(kilometro_texto) 
            FROM puntos_carretera 
            WHERE carretera = %s
        ''', (carretera,))
        
        resultado = cursor.fetchone()
        cursor.close()
        
        if resultado and resultado[0] and resultado[1]:
            return f"{resultado[0]} - {resultado[1]}"
        else:
            return "No disponible"
            
    except Exception as e:
        return f"Error: {str(e)}"
    finally:
        if conn:
            release_db_connection(conn)

@app.route('/api/debug/puntos/<carretera>', methods=['GET'])
def debug_puntos_carretera(carretera):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT kilometro_texto, kilometro, latitud, longitud 
            FROM puntos_carretera 
            WHERE carretera = %s 
            ORDER BY kilometro
        ''', (carretera.upper(),))
        
        puntos = []
        for row in cursor.fetchall():
            puntos.append({
                'kilometro_texto': row[0],
                'kilometro_metros': row[1],
                'latitud': row[2],
                'longitud': row[3]
            })
        
        cursor.close()
        
        return jsonify({
            'carretera': carretera.upper(),
            'puntos': puntos,
            'total_puntos': len(puntos)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

@app.route('/static/kml_files/<filename>')
def serve_kml(filename):
    try:
        return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    except FileNotFoundError:
        return jsonify({'error': 'Archivo KML no encontrado'}), 404

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy'})

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
