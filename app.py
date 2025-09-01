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
import urllib.parse

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
        
        # Crear tablas si no existen - MODIFICADA para nuevos campos
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
                kml_file TEXT,
                reseñable BOOLEAN,
                sentido TEXT,
                calzada TEXT,
                ubicacion TEXT,
                danos_infraestructura TEXT,
                hora_deteccion TEXT,
                reportado_por TEXT,
                hora_llegada TEXT,
                personal_llegada TEXT,
                aviso_emergencia TEXT,
                victimas BOOLEAN,
                fallecidos INTEGER,
                heridos INTEGER,
                detalles_victimas TEXT
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
            for i, foto_url in enumerate(fotos_urls):
                fotos_html += f'<img src="{foto_url}" width="150" style="margin: 5px; border: 1px solid #ccc;" alt="Foto {i+1}">'
            fotos_html += "</div>"
        else:
            fotos_html = "<p>No hay fotos disponibles</p>"

        # Fecha formateada
        fecha_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # KML extremadamente simple y compatible
        kml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Incidencia {incidencia_id}</name>
    <Placemark>
      <name>Incidencia {incidencia_id}</name>
      <description>
        <![CDATA[
        <!DOCTYPE html>
        <html>
        <body>
          <h2 style="color: #{color}; margin-bottom: 15px;">Incidencia Vial {incidencia_id}</h2>
          <div style="background: #f8f9fa; padding: 15px; border-radius: 5px; margin-bottom: 15px;">
            <p><strong>🚗 Carretera:</strong> {carretera}</p>
            <p><strong>📍 Punto Kilométrico:</strong> {kilometro}</p>
            <p><strong>🔧 Tipo:</strong> {tipo}</p>
            <p><strong>📅 Fecha:</strong> {fecha_str}</p>
            <p><strong>🌐 Coordenadas:</strong> {latitud:.6f}, {longitud:.6f}</p>
          </div>
          <div style="margin-bottom: 15px;">
            <h3 style="color: #{color};">Descripción:</h3>
            <p style="background: #e9ecef; padding: 10px; border-radius: 3px;">{descripcion}</p>
          </div>
          {fotos_html}
          <div style="margin-top: 20px; padding: 10px; background: #e3f2fd; border-radius: 3px;">
            <small>Generado automáticamente por Sistema de Incidencias Viales</small>
          </div>
        </body>
        </html>
        ]]>
      </description>
      <Point>
        <coordinates>{longitud},{latitud},0</coordinates>
      </Point>
      <Style>
        <IconStyle>
          <color>ff{color}</color>
          <scale>1.3</scale>
          <Icon>
            <href>http://maps.google.com/mapfiles/kml/pushpin/red-pushpin.png</href>
          </Icon>
        </IconStyle>
      </Style>
    </Placemark>
  </Document>
</kml>"""
        
        # Guardar archivo KML
        kml_filename = f"incidencia_{incidencia_id}.kml"
        kml_path = os.path.join(app.config['UPLOAD_FOLDER'], kml_filename)
        
        with open(kml_path, 'w', encoding='utf-8') as f:
            f.write(kml_content)
        
        print(f"✅ KML creado: {kml_path}")
        return kml_filename
        
    except Exception as e:
        print(f"❌ Error creando KML: {e}")
        import traceback
        traceback.print_exc()
        return None

# Función para generar vista de mapa personalizado con Leaflet
def generar_vista_mapa(incidencia_id, latitud, longitud, carretera, kilometro, tipo, descripcion, fecha, fotos_urls=None):
    if fotos_urls is None:
        fotos_urls = []
    
    # Crear contenido HTML para las fotos
    fotos_html = ""
    if fotos_urls:
        fotos_html = "<h4>Fotos:</h4><div style='display: flex; flex-wrap: wrap;'>"
        for i, foto_url in enumerate(fotos_urls):
            fotos_html += f'<img src="{foto_url}" width="150" style="margin: 5px; border: 1px solid #ccc; border-radius: 5px;" alt="Foto {i+1}">'
        fotos_html += "</div>"
    
    # Determinar color según el tipo
    if tipo.lower() == "accidente":
        marker_color = "red"
    elif tipo.lower() == "obra":
        marker_color = "orange"
    else:
        marker_color = "blue"
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Incidencia {incidencia_id}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
        <style>
            body {{ margin: 0; padding: 0; font-family: Arial, sans-serif; }}
            #map {{ height: 500px; width: 100%; }}
            .info-panel {{ 
                padding: 20px; 
                background: #f8f9fa; 
                border-bottom: 1px solid #ddd;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
            .fotos-container {{ 
                display: flex; 
                flex-wrap: wrap; 
                margin-top: 10px; 
                gap: 10px;
            }}
            .foto {{ 
                max-width: 150px; 
                border: 1px solid #ccc; 
                border-radius: 5px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
            .header {{
                background: #{'dc3545' if tipo.lower() == 'accidente' else 'ffc107' if tipo.lower() == 'obra' else '007bff'};
                color: white;
                padding: 15px;
                margin: -20px -20px 20px -20px;
                border-radius: 5px 5px 0 0;
            }}
            .google-maps-btn {{
                display: block;
                margin-top: 15px;
                padding: 12px 20px;
                background: #4285f4;
                color: white;
                text-align: center;
                text-decoration: none;
                border-radius: 5px;
                font-weight: bold;
                transition: background 0.3s;
            }}
            .google-maps-btn:hover {{
                background: #3367d6;
            }}
            .leaflet-popup-content {{
                max-width: 300px;
            }}
            .popup-content h3 {{
                margin: 0 0 10px 0;
                color: #{'dc3545' if tipo.lower() == 'accidente' else 'ffc107' if tipo.lower() == 'obra' else '007bff'};
            }}
        </style>
    </head>
    <body>
        <div class="info-panel">
            <div class="header">
                <h2>🚨 Incidencia {incidencia_id}</h2>
            </div>
            
            <div class="info-content">
                <p><strong>🛣️ Carretera:</strong> {carretera}</p>
                <p><strong>📍 Punto Kilométrico:</strong> {kilometro}</p>
                <p><strong>🔧 Tipo:</strong> <span style="color: {marker_color}; font-weight: bold;">{tipo.upper()}</span></p>
                <p><strong>📅 Fecha:</strong> {fecha}</p>
                <p><strong>📝 Descripción:</strong> {descripcion}</p>
                <p><strong>🌐 Coordenadas:</strong> {latitud:.6f}, {longitud:.6f}</p>
                
                {fotos_html}
                
                <a href="https://www.google.com/maps?q={latitud},{longitud}" 
                   target="_blank" class="google-maps-btn">
                   📍 Abrir en Google Maps
                </a>
            </div>
        </div>
        
        <div id="map"></div>
        
        <script>
            // Inicializar mapa centrado en las coordenadas
            var map = L.map('map').setView([{latitud}, {longitud}], 15);
            
            // Añadir capa de OpenStreetMap
            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            }}).addTo(map);
            
            // Crear icono personalizado según el tipo
            var iconUrl = 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-{marker_color}.png';
            var icon = L.icon({{
                iconUrl: iconUrl,
                shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
                iconSize: [25, 41],
                iconAnchor: [12, 41],
                popupAnchor: [1, -34],
                shadowSize: [41, 41]
            }});
            
            // Contenido detallado para el popup
            var popupContent = `
                <div class="popup-content">
                    <h3>🚨 Incidencia {incidencia_id}</h3>
                    <p><strong>🛣️ Carretera:</strong> {carretera}</p>
                    <p><strong>📍 PK:</strong> {kilometro}</p>
                    <p><strong>🔧 Tipo:</strong> <span style="color: {marker_color}; font-weight: bold;">{tipo.upper()}</span></p>
                    <p><strong>📅 Fecha:</strong> {fecha}</p>
                    <p><strong>📝 Descripción:</strong> {descripcion}</p>
                    <p><strong>🌐 Coordenadas:</strong> {latitud:.6f}, {longitud:.6f}</p>
                </div>
            `;
            
            // Añadir marcador con popup
            var marker = L.marker([{latitud}, {longitud}], {{icon: icon}})
                .addTo(map)
                .bindPopup(popupContent)
                .openPopup();
            
            // Añadir círculo para mejor visualización
            L.circle([{latitud}, {longitud}], {{
                color: '{marker_color}',
                fillColor: '{marker_color}',
                fillOpacity: 0.2,
                radius: 50
            }}).addTo(map);
            
            // Ajustar el mapa para que se vea el marcador y el popup
            map.fitBounds(marker.getBounds(), {{ padding: [50, 50] }});
        </script>
    </body>
    </html>
    """
    return html_content

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
                "reseñable": row[9], "sentido": row[10], "calzada": row[11],
                "ubicacion": row[12], "danos_infraestructura": row[13],
                "hora_deteccion": row[14], "reportado_por": row[15],
                "hora_llegada": row[16], "personal_llegada": row[17],
                "aviso_emergencia": row[18], "victimas": row[19],
                "fallecidos": row[20], "heridos": row[21],
                "detalles_victimas": row[22], "num_fotos": row[23]
            }
            # Generar enlace público al KML
            if incidencia['kml_file']:
                base_url = request.host_url.rstrip('/')
                incidencia['kml_url'] = f"{base_url}/static/kml_files/{incidencia['kml_file']}"
                # Enlaces para mapas
                incidencia['google_maps_url'] = f"https://www.google.com/maps?q={incidencia['latitud']},{incidencia['longitud']}"
                incidencia['map_view_url'] = f"{base_url}/api/map-view/{incidencia['id']}"
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
        
        # Obtener todos los campos del formulario
        incidencia_id = data.get('id', '').strip() # Strip para limpiar espacios
        carretera = data.get('carretera')
        kilometro = data.get('kilometro')
        tipo = data.get('tipo')
        descripcion = data.get('descripcion', '')
        reseñable = data.get('remarkable') == 'yes'
        sentido = data.get('sentido', '')
        calzada = data.get('calzada', '')
        ubicacion = data.get('ubicacion', '')
        danos_infraestructura = data.get('danos_infraestructura', '')
        hora_deteccion = data.get('hora_deteccion', '')
        reportado_por = data.get('reportado_por', '')
        hora_llegada = data.get('hora_llegada', '')
        personal_llegada = data.get('personal_llegada', '')
        aviso_emergencia = data.get('aviso_emergencia', '')
        victimas = data.get('victimas') == 'yes'
        fallecidos = int(data.get('fallecidos', 0))
        heridos = int(data.get('heridos', 0))
        detalles_victimas = data.get('detalles_victimas', '')
        
        # Validar campos requeridos
        if not all([incidencia_id, carretera, kilometro, tipo, descripcion]):
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
        
        # Guardar en base de datos con todos los campos
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO incidencias 
                (id, carretera, kilometro, latitud, longitud, tipo, fecha, descripcion, kml_file,
                 reseñable, sentido, calzada, ubicacion, danos_infraestructura, hora_deteccion,
                 reportado_por, hora_llegada, personal_llegada, aviso_emergencia, victimas,
                 fallecidos, heridos, detalles_victimas)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (incidencia_id, carretera.upper(), kilometro, latitud, longitud, tipo, 
                  datetime.now().strftime('%Y-%m-%d %H:%M:%S'), descripcion, kml_filename,
                  reseñable, sentido, calzada, ubicacion, danos_infraestructura, hora_deteccion,
                  reportado_por, hora_llegada, personal_llegada, aviso_emergencia, victimas,
                  fallecidos, heridos, detalles_victimas))
            
            # Guardar información de fotos en la base de datos
            for foto_url in fotos_urls:
                cursor.execute('''
                    INSERT INTO fotos_incidencia (incidencia_id, ruta_foto)
                    VALUES (%s, %s)
                ''', (incidencia_id, foto_url))
            
            conn.commit()
        except psycopg2.IntegrityError:
            return jsonify({'error': 'El ID de incidencia ya existe'}), 400
        except Exception as e:
            print(f"Error al insertar en BD: {e}")
            return jsonify({'error': f'Error de base de datos: {str(e)}'}), 500
        finally:
            cursor.close()
        
        # Generar URLs públicas
        base_url = request.host_url.rstrip('/')
        kml_url = f"{base_url}/static/kml_files/{kml_filename}"
        download_url = f"{base_url}/api/download-kml/{incidencia_id}"
        map_view_url = f"{base_url}/api/map-view/{incidencia_id}"
        google_maps_url = f"https://www.google.com/maps?q={latitud},{longitud}"
        
        return jsonify({
            'success': True,
            'message': 'Incidencia registrada correctamente',
            'kml_url': kml_url,
            'google_maps_url': google_maps_url,
            'map_view_url': map_view_url,
            'download_url': download_url,
            'incidencia': {
                'id': incidencia_id,
                'carretera': carretera,
                'kilometro': kilometro,
                'tipo': tipo,
                'latitud': latitud,
                'longitud': longitud,
                'descripcion': descripcion,
                'fotos': fotos_urls,
                'reseñable': reseñable,
                'sentido': sentido,
                'calzada': calzada,
                'ubicacion': ubicacion,
                'hora_deteccion': hora_deteccion,
                'reportado_por': reportado_por
            }
        })
        
    except Exception as e:
        print(f"Error general: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

# Ruta para vista de mapa personalizado
@app.route('/api/map-view/<incidencia_id>')
def map_view(incidencia_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Strip para limpiar espacios
        incidencia_id = incidencia_id.strip()
        
        cursor.execute('SELECT * FROM incidencias WHERE id = %s', (incidencia_id,))
        incidencia = cursor.fetchone()
        
        cursor.execute('SELECT * FROM fotos_incidencia WHERE incidencia_id = %s', (incidencia_id,))
        fotos = cursor.fetchall()
        
        cursor.close()
        release_db_connection(conn)
        
        if not incidencia:
            return jsonify({'error': 'Incidencia no encontrada'}), 404
        
        fotos_urls = [foto[2] for foto in fotos]
        
        # Generar HTML con mapa personalizado
        html_content = generar_vista_mapa(
            incidencia_id=incidencia[0],
            latitud=incidencia[3],
            longitud=incidencia[4],
            carretera=incidencia[1],
            kilometro=incidencia[2],
            tipo=incidencia[5],
            descripcion=incidencia[7],
            fecha=incidencia[6],
            fotos_urls=fotos_urls
        )
        
        return html_content
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/static/kml_files/<filename>')
def serve_kml(filename):
    try:
        kml_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        if not os.path.exists(kml_path):
            return jsonify({'error': 'Archivo KML no encontrado'}), 404
        
        response = send_file(kml_path)
        response.headers['Content-Type'] = 'application/vnd.google-earth.kml+xml'
        response.headers['Content-Disposition'] = f'inline; filename="{filename}"'
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
        
        cursor.execute('SELECT DISTINCT carretera FROM puntos_carretera ORDER BY carretera')
        carreteras = [row[0] for row in cursor.fetchall()]
        
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
        
        # Strip para limpiar espacios
        incidencia_id = incidencia_id.strip()
        
        cursor.execute('SELECT * FROM incidencias WHERE id = %s', (incidencia_id,))
        incidencia = cursor.fetchone()
        
        cursor.execute('SELECT * FROM fotos_incidencia WHERE incidencia_id = %s', (incidencia_id,))
        fotos = cursor.fetchall()
        
        cursor.close()
        
        if not incidencia:
            return jsonify({'error': 'Incidencia no encontrada'}), 404
        
        base_url = request.host_url.rstrip('/')
        kml_url = f"{base_url}/static/kml_files/incidencia_{incidencia_id}.kml"
        map_view_url = f"{base_url}/api/map-view/{incidencia_id}"
        google_maps_url = f"https://www.google.com/maps?q={incidencia[3]},{incidencia[4]}"
        
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
                'kml_file': incidencia[8],
                'reseñable': incidencia[9],
                'sentido': incidencia[10],
                'calzada': incidencia[11],
                'ubicacion': incidencia[12],
                'danos_infraestructura': incidencia[13],
                'hora_deteccion': incidencia[14],
                'reportado_por': incidencia[15],
                'hora_llegada': incidencia[16],
                'personal_llegada': incidencia[17],
                'aviso_emergencia': incidencia[18],
                'victimas': incidencia[19],
                'fallecidos': incidencia[20],
                'heridos': incidencia[21],
                'detalles_victimas': incidencia[22]
            },
            'fotos': [{'id': foto[0], 'ruta': foto[2]} for foto in fotos],
            'links': {
                'map_view': map_view_url,
                'google_maps': google_maps_url,
                'kml_file': kml_url
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

# Ruta para descargar KML individual
@app.route('/api/download-kml/<incidencia_id>')
def download_kml(incidencia_id):
    try:
        kml_filename = f"incidencia_{incidencia_id}.kml"
        kml_path = os.path.join(app.config['UPLOAD_FOLDER'], kml_filename)
        
        if not os.path.exists(kml_path):
            return jsonify({'error': 'KML no encontrado'}), 404
        
        response = send_file(kml_path, as_attachment=True, download_name=kml_filename)
        response.headers['Content-Type'] = 'application/vnd.google-earth.kml+xml'
        return response
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Ruta para reiniciar la base de datos
@app.route('/api/reset-database', methods=['POST'])
def reset_database():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('DROP TABLE IF EXISTS fotos_incidencia CASCADE')
        cursor.execute('DROP TABLE IF EXISTS incidencias CASCADE')
        cursor.execute('DROP TABLE IF EXISTS puntos_carretera CASCADE')
        
        conn.commit()
        cursor.close()
        
        setup_database()
        
        return jsonify({'success': True, 'message': 'Base de datos reiniciada exitosamente'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

# Ruta para actualizar la estructura de la base de datos sin perder datos
@app.route('/api/update-database', methods=['POST'])
def update_database():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Verificar si las columnas ya existen y agregarlas si no
        cursor.execute('''
            DO $$ 
            BEGIN
                -- Verificar y agregar columnas si no existen
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='reseñable') THEN
                    ALTER TABLE incidencias ADD COLUMN reseñable BOOLEAN;
                END IF;
                
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='sentido') THEN
                    ALTER TABLE incidencias ADD COLUMN sentido TEXT;
                END IF;
                
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='calzada') THEN
                    ALTER TABLE incidencias ADD COLUMN calzada TEXT;
                END IF;
                
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='ubicacion') THEN
                    ALTER TABLE incidencias ADD COLUMN ubicacion TEXT;
                END IF;
                
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='danos_infraestructura') THEN
                    ALTER TABLE incidencias ADD COLUMN danos_infraestructura TEXT;
                END IF;
                
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='hora_deteccion') THEN
                    ALTER TABLE incidencias ADD COLUMN hora_deteccion TEXT;
                END IF;
                
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='reportado_por') THEN
                    ALTER TABLE incidencias ADD COLUMN reportado_por TEXT;
                END IF;
                
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='hora_llegada') THEN
                    ALTER TABLE incidencias ADD COLUMN hora_llegada TEXT;
                END IF;
                
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='personal_llegada') THEN
                    ALTER TABLE incidencias ADD COLUMN personal_llegada TEXT;
                END IF;
                
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='aviso_emergencia') THEN
                    ALTER TABLE incidencias ADD COLUMN aviso_emergencia TEXT;
                END IF;
                
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='victimas') THEN
                    ALTER TABLE incidencias ADD COLUMN victimas BOOLEAN;
                END IF;
                
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='fallecidos') THEN
                    ALTER TABLE incidencias ADD COLUMN fallecidos INTEGER;
                END IF;
                
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='heridos') THEN
                    ALTER TABLE incidencias ADD COLUMN heridos INTEGER;
                END IF;
                
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='incidencias' AND column_name='detalles_victimas') THEN
                    ALTER TABLE incidencias ADD COLUMN detalles_victimas TEXT;
                END IF;
            END $$;
        ''')
        
        conn.commit()
        cursor.close()
        
        return jsonify({'success': True, 'message': 'Estructura de base de datos actualizada exitosamente'})
        
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

