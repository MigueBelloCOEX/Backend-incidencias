from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import psycopg2
import os
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime
import re
from urllib.parse import urlparse

# Configuración de la aplicación
app = Flask(__name__)
CORS(app)

# Configuración para PostgreSQL
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL no está configurada en las variables de entorno.")

# Crear el directorio para los archivos KML
os.makedirs('static/kml_files', exist_ok=True)

def get_db_connection():
    """Establece la conexión a la base de datos PostgreSQL."""
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """
    Inicializa la base de datos, crea las tablas si no existen
    y carga los datos de puntos de carretera.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Crear tablas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS puntos_carretera (
            id SERIAL PRIMARY KEY,
            carretera TEXT,
            kilometro REAL,
            kilometro_texto TEXT,
            latitud REAL,
            longitud REAL
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
    
    # Insertar datos de ejemplo de puntos kilométricos
    puntos = [
        ('CA-35', 0, '0+000', 36.7196, -4.4200),
        ('CA-35', 1000, '1+000', 36.7234, -4.4156),
        ('CA-35', 2000, '2+000', 36.7272, -4.4112),
        ('CA-35', 3000, '3+000', 36.7310, -4.4068),
        ('CA-35', 4000, '4+000', 36.7348, -4.4024),
        ('CA-36', 0, '0+000', 36.5283, -6.2887),
        ('CA-36', 1000, '1+000', 36.5321, -6.2843),
        ('CA-36', 2000, '2+000', 36.5359, -6.2799),
        ('CA-36', 3000, '3+000', 36.5397, -6.2755),
        ('CA-36', 4000, '4+000', 36.5435, -6.2711)
    ]
    
    # Usar un bloque try-except para evitar duplicados en reinicios
    try:
        cursor.executemany('''
            INSERT INTO puntos_carretera (carretera, kilometro, kilometro_texto, latitud, longitud)
            VALUES (%s, %s, %s, %s, %s);
        ''', puntos)
        conn.commit()
    except psycopg2.IntegrityError:
        # Los datos ya existen, no hacemos nada
        conn.rollback()
    
    cursor.close()
    conn.close()

def obtener_coordenadas_interpoladas(carretera, punto_kilometrico_str):
    """Calcula la latitud y longitud interpoladas para un punto kilométrico."""
    try:
        match = re.search(r'(\d+)\+(\d+)', punto_kilometrico_str)
        if match:
            km_entero = int(match.group(1))
            km_metros = int(match.group(2))
            metros_totales = km_entero * 1000 + km_metros
        else:
            try:
                kilometro_decimal = float(punto_kilometrico_str)
                metros_totales = kilometro_decimal * 1000
            except ValueError:
                return None, None

        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Buscar puntos de referencia
        cursor.execute('''
            SELECT kilometro, latitud, longitud 
            FROM puntos_carretera 
            WHERE carretera = %s AND kilometro <= %s 
            ORDER BY kilometro DESC LIMIT 1;
        ''', (carretera.upper(), metros_totales))
        punto_inicial = cursor.fetchone()
        
        cursor.execute('''
            SELECT kilometro, latitud, longitud 
            FROM puntos_carretera 
            WHERE carretera = %s AND kilometro >= %s 
            ORDER BY kilometro ASC LIMIT 1;
        ''', (carretera.upper(), metros_totales))
        punto_final = cursor.fetchone()
        
        cursor.close()
        conn.close()

        if punto_inicial and punto_final:
            km1, lat1, lon1 = punto_inicial[0], punto_inicial[1], punto_inicial[2]
            km2, lat2, lon2 = punto_final[0], punto_final[1], punto_final[2]
            
            if km1 == metros_totales:
                return lat1, lon1
            if km2 == metros_totales:
                return lat2, lon2
            
            if km2 != km1:
                proporcion = (metros_totales - km1) / (km2 - km1)
                latitud_interpolada = lat1 + (lat2 - lat1) * proporcion
                longitud_interpolada = lon1 + (lon2 - lon1) * proporcion
                return latitud_interpolada, longitud_interpolada
            else:
                return lat1, lon1
        
        return None, None
        
    except Exception as e:
        print(f"Error en interpolación: {e}")
        return None, None

def crear_kml_incidencia(incidencia_id, carretera, kilometro, tipo, latitud, longitud, descripcion):
    """Genera el archivo KML para una incidencia."""
    try:
        kml = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
        document = ET.SubElement(kml, "Document")
        
        placemark = ET.SubElement(document, "Placemark")
        name = ET.SubElement(placemark, "name")
        name.text = f"Incidencia {incidencia_id}"
        
        description_elem = ET.SubElement(placemark, "description")
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
        
        point = ET.SubElement(placemark, "Point")
        coordinates = ET.SubElement(point, "coordinates")
        coordinates.text = f"{longitud},{latitud},0"
        
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
        
        rough_string = ET.tostring(kml, 'utf-8')
        reparsed = minidom.parseString(rough_string)
        kml_content = reparsed.toprettyxml(indent="  ")
        
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
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM incidencias ORDER BY fecha DESC;')
        
        incidencias = []
        for row in cursor.fetchall():
            incidencia = {col[0]: row[i] for i, col in enumerate(cursor.description)}
            
            if incidencia.get('kml_file'):
                base_url = request.host_url.rstrip('/')
                incidencia['kml_url'] = f"{base_url}/static/kml_files/{incidencia['kml_file']}"
                incidencia['google_earth_url'] = f"https://earth.google.com/web/search/{incidencia['latitud']},{incidencia['longitud']}"
                incidencia['google_maps_url'] = f"https://www.google.com/maps?q={incidencia['latitud']},{incidencia['longitud']}"
            
            incidencias.append(incidencia)
        
        cursor.close()
        conn.close()
        return jsonify(incidencias)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/incidencias', methods=['POST'])
def crear_incidencia():
    try:
        data = request.get_json()
        
        incidencia_id = data.get('id')
        carretera = data.get('carretera')
        kilometro = data.get('kilometro')
        tipo = data.get('tipo')
        descripcion = data.get('descripcion', '')
        
        if not all([incidencia_id, carretera, kilometro, tipo]):
            return jsonify({'error': 'Faltan campos requeridos'}), 400
        
        latitud, longitud = obtener_coordenadas_interpoladas(carretera, kilometro)
        if not latitud or not longitud:
            return jsonify({'error': 'No se pudieron obtener las coordenadas para la carretera y PK especificados'}), 400
        
        kml_filename = crear_kml_incidencia(incidencia_id, carretera, kilometro, tipo, latitud, longitud, descripcion)
        
        if not kml_filename:
            return jsonify({'error': 'Error creando archivo KML'}), 500
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO incidencias (id, carretera, kilometro, latitud, longitud, tipo, fecha, descripcion, kml_file)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
            ''', (incidencia_id, carretera.upper(), kilometro, latitud, longitud, tipo, 
                  datetime.now().strftime('%Y-%m-%d %H:%M:%S'), descripcion, kml_filename))
            conn.commit()
        except psycopg2.IntegrityError:
            conn.rollback()
            return jsonify({'error': 'El ID de incidencia ya existe'}), 400
        finally:
            cursor.close()
            conn.close()
        
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

@app.route('/static/kml_files/<filename>')
def serve_kml(filename):
    try:
        return send_file(os.path.join('static/kml_files', filename))
    except FileNotFoundError:
        return jsonify({'error': 'Archivo KML no encontrado'}), 404

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy'})

if __name__ == '__main__':
    # init_db() se llama aquí para el entorno local.
    # En Render, esto debería hacerse a través de un "job" o un script de inicio.
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)