from flask import Flask, render_template, request, jsonify, send_file
from flask_cors import CORS
import sqlite3
import os
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime
import base64
import re

app = Flask(__name__)
CORS(app)

# Configuración para Render
app.config['UPLOAD_FOLDER'] = 'static/kml_files'
app.config['DATABASE'] = '/tmp/incidencias.db'  # Usar /tmp en Render

# Crear directorios si no existen
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('static/fotos', exist_ok=True)

def get_db_connection():
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Crear tablas si no existen
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS puntos_carretera (
            id INTEGER PRIMARY KEY,
            carretera TEXT,
            kilometro REAL,
            kilometro_texto TEXT,
            latitud REAL,
            longitud REAL
        )
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
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fotos_incidencia (
            id INTEGER PRIMARY KEY,
            incidencia_id TEXT,
            ruta_foto TEXT,
            FOREIGN KEY(incidencia_id) REFERENCES incidencias(id)
        )
    ''')
    
    # Insertar datos de ejemplo de puntos kilométricos
    cursor.execute('''
        INSERT OR IGNORE INTO puntos_carretera (carretera, kilometro, kilometro_texto, latitud, longitud)
        VALUES 
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
    ''')
    
    conn.commit()
    conn.close()

def obtener_coordenadas_interpoladas(carretera, punto_kilometrico_str):
    try:
        # Parsear el punto kilométrico
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
            WHERE carretera = ? AND kilometro <= ? 
            ORDER BY kilometro DESC LIMIT 1
        ''', (carretera.upper(), metros_totales))
        punto_inicial = cursor.fetchone()
        
        cursor.execute('''
            SELECT kilometro, latitud, longitud 
            FROM puntos_carretera 
            WHERE carretera = ? AND kilometro >= ? 
            ORDER BY kilometro ASC LIMIT 1
        ''', (carretera.upper(), metros_totales))
        punto_final = cursor.fetchone()
        
        conn.close()

        if punto_inicial and punto_final:
            km1, lat1, lon1 = punto_inicial['kilometro'], punto_inicial['latitud'], punto_inicial['longitud']
            km2, lat2, lon2 = punto_final['kilometro'], punto_final['latitud'], punto_final['longitud']
            
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
            incidencia = dict(row)
            # Generar enlace público al KML
            if incidencia['kml_file']:
                base_url = request.host_url.rstrip('/')
                incidencia['kml_url'] = f"{base_url}/static/kml_files/{incidencia['kml_file']}"
                # Google Earth no acepta URLs con KML embebido, usamos coordenadas simples
                incidencia['google_earth_url'] = f"https://earth.google.com/web/search/{incidencia['latitud']},{incidencia['longitud']}"
                incidencia['google_maps_url'] = f"https://www.google.com/maps?q={incidencia['latitud']},{incidencia['longitud']}"
            incidencias.append(incidencia)
        
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (incidencia_id, carretera.upper(), kilometro, latitud, longitud, tipo, 
                  datetime.now().strftime('%Y-%m-%d %H:%M:%S'), descripcion, kml_filename))
            
            conn.commit()
        except sqlite3.IntegrityError:
            return jsonify({'error': 'El ID de incidencia ya existe'}), 400
        finally:
            conn.close()
        
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

@app.route('/static/kml_files/<filename>')
def serve_kml(filename):
    try:
        return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    except FileNotFoundError:
        return jsonify({'error': 'Archivo KML no encontrado'}), 404

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy'})

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)