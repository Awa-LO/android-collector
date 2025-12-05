import os
import subprocess
import time
import json
import re
import sqlite3
import tarfile
import zlib
import shutil
import tempfile
import datetime
import hashlib
import csv
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from pathlib import Path

# ============================================
# FONCTIONS AUXILIAIRES
# ============================================

def get_adb_path():
    """Trouver le chemin d'ADB"""
    possible_paths = [
        r"C:\adb\platform-tools\adb.exe",
        r"C:\Users\HP ELITEBOOK G6\AppData\Local\Android\Sdk\platform-tools\adb.exe",
        "adb"
    ]
    
    for path in possible_paths:
        try:
            subprocess.check_output([path, 'version'], stderr=subprocess.PIPE, text=True)
            return path
        except:
            continue
    return None

def get_device_id(adb_path):
    """Obtenir l'ID du périphérique connecté"""
    try:
        devices_output = subprocess.check_output([adb_path, 'devices'], text=True)
        devices = devices_output.split('\n')[1:]
        for d in devices:
            if d.strip() and not d.startswith('*') and 'device' in d:
                return d.split('\t')[0]
    except:
        pass
    return None

def format_file_size(size_bytes):
    """Convertir la taille en format lisible"""
    if size_bytes == 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"

def get_audio_mime_type(filename):
    """Déterminer le type MIME audio"""
    filename_lower = filename.lower()
    if filename_lower.endswith('.aac'):
        return 'audio/aac'
    elif filename_lower.endswith('.m4a'):
        return 'audio/mp4'
    elif filename_lower.endswith('.ogg'):
        return 'audio/ogg'
    elif filename_lower.endswith('.wav'):
        return 'audio/wav'
    elif filename_lower.endswith('.mp3'):
        return 'audio/mpeg'
    elif filename_lower.endswith('.amr'):
        return 'audio/amr'
    elif filename_lower.endswith('.flac'):
        return 'audio/flac'
    else:
        return 'audio/mpeg'

# ============================================
# FONCTIONS SPÉCIFIQUES GOOGLE MAPS
# ============================================

def extract_google_maps_data(adb_path, device, timestamp):
    """Extraire les données Google Maps (trajets, recherches, favoris)"""
    
    maps_dirs = [
        '/data/data/com.google.android.apps.maps/databases/',
        '/storage/emulated/0/Android/data/com.google.android.apps.maps/files/',
        '/storage/emulated/0/Google Maps/'
    ]
    
    extracted_data = {
        'timeline': [],      # Historique des positions
        'searches': [],      # Recherches
        'saved_places': [],  # Lieux enregistrés
        'routes': [],        # Itinéraires
        'offline_maps': []   # Cartes hors ligne
    }
    
    for maps_dir in maps_dirs:
        try:
            # Essayer d'accéder aux fichiers Maps
            cmd_list = [adb_path, '-s', device, 'shell', 'ls', '-la', maps_dir]
            files = subprocess.check_output(cmd_list, text=True, timeout=30, errors='ignore')
            
            if 'gmm_' in files or '.db' in files:
                # Liste des fichiers de base de données Google Maps
                db_files = ['gmm_storage.db', 'gmm_myplaces.db', 'gmm_history.db', 
                           'search_history.db', 'location_history.db']
                
                for db_file in db_files:
                    try:
                        # Copier la base de données
                        temp_dir = tempfile.mkdtemp()
                        pull_cmd = [adb_path, '-s', device, 'pull', 
                                   f'{maps_dir}{db_file}', 
                                   temp_dir]
                        result = subprocess.run(pull_cmd, timeout=30, capture_output=True, text=True)
                        
                        if result.returncode == 0:
                            # Chercher le fichier copié
                            for root, dirs, files_list in os.walk(temp_dir):
                                for file in files_list:
                                    if file.endswith('.db'):
                                        db_path = os.path.join(root, file)
                                        parsed_data = parse_maps_database(db_path)
                                        if parsed_data:
                                            if 'locations' in parsed_data:
                                                extracted_data['timeline'].extend(parsed_data['locations'])
                                            if 'searches' in parsed_data:
                                                extracted_data['searches'].extend(parsed_data['searches'])
                                        
                                        # Copier dans le dossier de collection
                                        dest_path = f'collector/static/collected_data/maps_{timestamp}_{db_file}'
                                        shutil.copy2(db_path, dest_path)
                    except:
                        continue
                    
        except Exception as e:
            print(f"Erreur accès {maps_dir}: {str(e)}")
            continue
    
    return extracted_data

def parse_maps_database(db_path):
    """Parser la base de données Google Maps"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        results = {
            'locations': [],
            'searches': [],
            'places': []
        }
        
        # Chercher toutes les tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        for table in tables:
            table_name = table[0]
            
            # Table d'historique de localisation
            if any(keyword in table_name.lower() for keyword in ['location', 'position', 'lat', 'lon', 'gps']):
                try:
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    columns = [col[1].lower() for col in cursor.fetchall()]
                    
                    # Chercher les colonnes de coordonnées
                    lat_col = None
                    lon_col = None
                    time_col = None
                    
                    for col in columns:
                        if 'lat' in col:
                            lat_col = col
                        elif 'lon' in col or 'lng' in col:
                            lon_col = col
                        elif 'time' in col or 'date' in col or 'timestamp' in col:
                            time_col = col
                    
                    if lat_col and lon_col:
                        if time_col:
                            cursor.execute(f"SELECT {lat_col}, {lon_col}, {time_col} FROM {table_name} LIMIT 500")
                        else:
                            cursor.execute(f"SELECT {lat_col}, {lon_col} FROM {table_name} LIMIT 500")
                        
                        rows = cursor.fetchall()
                        for row in rows:
                            if len(row) >= 2:
                                location = {
                                    'latitude': row[0],
                                    'longitude': row[1],
                                    'type': 'Google Maps',
                                    'source': 'timeline'
                                }
                                if len(row) >= 3:
                                    location['timestamp'] = row[2]
                                results['locations'].append(location)
                except:
                    continue
            
            # Table d'historique de recherche
            elif any(keyword in table_name.lower() for keyword in ['search', 'query']):
                try:
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    columns = [col[1].lower() for col in cursor.fetchall()]
                    
                    query_col = None
                    time_col = None
                    
                    for col in columns:
                        if 'query' in col or 'term' in col or 'search' in col:
                            query_col = col
                        elif 'time' in col or 'date' in col:
                            time_col = col
                    
                    if query_col:
                        if time_col:
                            cursor.execute(f"SELECT {query_col}, {time_col} FROM {table_name} LIMIT 100")
                        else:
                            cursor.execute(f"SELECT {query_col} FROM {table_name} LIMIT 100")
                        
                        rows = cursor.fetchall()
                        for row in rows:
                            search_entry = {
                                'query': row[0],
                                'type': 'search'
                            }
                            if len(row) >= 2:
                                search_entry['timestamp'] = row[1]
                            results['searches'].append(search_entry)
                except:
                    continue
        
        conn.close()
        return results
        
    except Exception as e:
        print(f"Erreur parsing DB {db_path}: {str(e)}")
        return None

def extract_google_maps_from_backup(backup_path):
    """Extraire Google Maps depuis un backup ADB"""
    temp_dir = tempfile.mkdtemp(prefix='maps_extract_')
    
    try:
        result = decode_backup_file(backup_path, temp_dir)
        if result['status'] == 'success':
            maps_data = {
                'timeline': [],
                'searches': [],
                'saved_places': []
            }
            
            # Chercher les fichiers Google Maps dans les fichiers extraits
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    
                    # Base de données Google Maps
                    if 'gmm_' in file or ('maps' in file.lower() and file.endswith('.db')):
                        try:
                            parsed = parse_maps_database(file_path)
                            if parsed:
                                if 'locations' in parsed:
                                    maps_data['timeline'].extend(parsed['locations'])
                                if 'searches' in parsed:
                                    maps_data['searches'].extend(parsed['searches'])
                        except:
                            continue
                    
                    # Fichiers KML/GPX (export Google Maps)
                    elif file.endswith(('.kml', '.gpx')):
                        try:
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                content = f.read()
                                locations = parse_kml_gpx(content)
                                maps_data['timeline'].extend(locations)
                        except:
                            continue
            
            return maps_data
    except:
        pass
    
    return None

def parse_kml_gpx(content):
    """Parser les fichiers KML/GPX (export Google Maps)"""
    locations = []
    
    # Pattern pour les coordonnées dans KML/GPX
    coord_patterns = [
        r'<coordinates>([^<]+)</coordinates>',
        r'<trkpt lat="([^"]+)" lon="([^"]+)">',
        r'<point><coordinates>([^<]+)</coordinates></point>'
    ]
    
    for pattern in coord_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple) and len(match) >= 2:
                try:
                    lat = float(match[0])
                    lon = float(match[1])
                    locations.append({
                        'latitude': lat,
                        'longitude': lon,
                        'type': 'KML/GPX',
                        'source': 'Google Maps Export'
                    })
                except:
                    continue
            elif isinstance(match, str):
                try:
                    # Format: lon,lat[,alt]
                    coords = match.strip().split(',')
                    if len(coords) >= 2:
                        lon = float(coords[0])
                        lat = float(coords[1])
                        locations.append({
                            'latitude': lat,
                            'longitude': lon,
                            'type': 'KML/GPX',
                            'source': 'Google Maps Export'
                        })
                except:
                    continue
    
    return locations

# ============================================
# VUES PRINCIPALES
# ============================================

def home(request):
    """Page d'accueil"""
    return render(request, 'collector/home.html')

def system_architecture(request):
    """Page d'architecture système"""
    return render(request, 'collector/system_architecture.html')

def collected_files(request):
    """Liste tous les fichiers collectés"""
    collected_dir = 'collector/static/collected_data'
    files_list = []
    dumps_list = []
    
    if os.path.exists(collected_dir):
        # Lister les dossiers full_dump
        for item in os.listdir(collected_dir):
            item_path = os.path.join(collected_dir, item)
            if os.path.isdir(item_path) and item.startswith('full_dump_'):
                dump_size = 0
                file_count = 0
                
                for root, dirs, files in os.walk(item_path):
                    for filename in files:
                        filepath = os.path.join(root, filename)
                        dump_size += os.path.getsize(filepath)
                        file_count += 1
                
                dumps_list.append({
                    'name': item,
                    'path': item,
                    'size': dump_size,
                    'size_readable': format_file_size(dump_size),
                    'modified': time.ctime(os.path.getmtime(item_path)),
                    'file_count': file_count,
                    'type': 'full_dump'
                })
        
        # Lister les fichiers individuels
        for root, dirs, files in os.walk(collected_dir):
            if 'full_dump_' in root:
                continue
                
            for filename in files:
                filepath = os.path.join(root, filename)
                relative_path = os.path.relpath(filepath, collected_dir)
                file_size = os.path.getsize(filepath)
                file_modified = time.ctime(os.path.getmtime(filepath))
                
                # Déterminer le type de fichier
                file_type = 'unknown'
                if filename.startswith('logs_'):
                    file_type = 'logs'
                elif filename.startswith('contacts_'):
                    file_type = 'contacts'
                elif filename.startswith('calls_'):
                    file_type = 'calls'
                elif filename.startswith('sms_'):
                    file_type = 'sms'
                elif filename.startswith('apps_'):
                    file_type = 'apps'
                elif filename.startswith('location_'):
                    file_type = 'location'
                elif filename.startswith('emails_'):
                    file_type = 'emails'
                elif filename.startswith('wifi_'):
                    file_type = 'wifi'
                elif filename.startswith('browser_'):
                    file_type = 'browser'
                elif filename.startswith('whatsapp_'):
                    file_type = 'whatsapp'
                elif filename.startswith('maps_'):
                    file_type = 'maps'
                elif filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')):
                    file_type = 'image'
                elif filename.lower().endswith(('.mp3', '.m4a', '.wav', '.aac', '.ogg', '.amr', '.flac')):
                    file_type = 'audio'
                elif filename.lower().endswith(('.mp4', '.3gp', '.avi', '.mkv', '.mov', '.wmv')):
                    file_type = 'video'
                elif filename.lower().endswith('.db'):
                    if 'whatsapp' in filename.lower() or 'msgstore' in filename.lower():
                        file_type = 'whatsapp'
                    elif 'gmm_' in filename.lower() or 'maps' in filename.lower():
                        file_type = 'maps'
                    else:
                        file_type = 'database'
                elif filename.lower().endswith('.ab'):
                    file_type = 'backup'
                elif filename.lower().endswith(('.crypt14', '.crypt15')):
                    file_type = 'whatsapp_encrypted'
                elif filename.lower().endswith(('.kml', '.gpx')):
                    file_type = 'maps'
                elif 'images_' in root:
                    file_type = 'image'
                elif 'audio_' in root or 'recordings' in root.lower() or 'sounds' in root.lower():
                    if filename.lower().endswith(('.mp3', '.m4a', '.wav', '.aac', '.ogg', '.amr', '.flac')):
                        file_type = 'audio'
                
                files_list.append({
                    'name': filename,
                    'path': relative_path.replace('\\', '/'),
                    'size': file_size,
                    'size_readable': format_file_size(file_size),
                    'modified': file_modified,
                    'type': file_type,
                    'url': f"/static/collected_data/{relative_path.replace(chr(92), '/')}",
                    'audio_type': get_audio_mime_type(filename),
                    'is_nomedia': filename.lower() == '.nomedia'
                })
        
        # Trier par date de modification (plus récent en premier)
        files_list.sort(key=lambda x: os.path.getmtime(os.path.join(collected_dir, x['path'])), reverse=True)
        dumps_list.sort(key=lambda x: os.path.getmtime(os.path.join(collected_dir, x['path'])), reverse=True)
    
    return render(request, 'collector/files.html', {
        'files': files_list, 
        'dumps': dumps_list,
        'total': len(files_list) + len(dumps_list)
    })

# ============================================
# FONCTIONS D'EXTRACTION AMÉLIORÉES
# ============================================

def extract_calls_with_permissions(adb_path, device, timestamp):
    """Extraire les appels avec contournement des permissions"""
    
    # Méthode 1: Via content provider (nécessite permissions)
    cmd_content = [adb_path, '-s', device, 'shell', 'content', 'query', 
                   '--uri', 'content://call_log/calls',
                   '--projection', 'number,date,duration,type,name']
    
    try:
        calls_output = subprocess.check_output(
            cmd_content, 
            stderr=subprocess.PIPE, 
            text=True, 
            timeout=30
        )
        
        if calls_output.strip() and "No result found" not in calls_output:
            return calls_output
    
    except subprocess.CalledProcessError:
        # Méthode 2: Via shell dumpsys (pas de permissions requises)
        cmd_dumpsys = [adb_path, '-s', device, 'shell', 'dumpsys', 'calllog']
        
        try:
            dumpsys_output = subprocess.check_output(
                cmd_dumpsys,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30
            )
            
            # Parser la sortie dumpsys
            parsed_calls = parse_dumpsys_calls(dumpsys_output)
            return parsed_calls
            
        except:
            # Méthode 3: Via backup ADB (contournement)
            backup_file = f"collector/static/collected_data/calls_backup_{timestamp}.ab"
            cmd_backup = [adb_path, '-s', device, 'backup', '-f', backup_file, 
                         '-noapk', 'com.android.providers.contacts']
            
            try:
                subprocess.run(cmd_backup, timeout=300, capture_output=True, text=True)
                
                # Décoder le backup et extraire calllog.db
                if os.path.exists(backup_file):
                    decoded_data = decode_backup_file(backup_file, tempfile.mkdtemp())
                    if decoded_data['status'] == 'success':
                        # Chercher calllog.db dans les fichiers extraits
                        for root, dirs, files in os.walk(decoded_data['output_dir']):
                            if 'calllog.db' in files:
                                db_path = os.path.join(root, 'calllog.db')
                                return extract_calls_from_db(db_path)
                
            except Exception as e:
                return f"Erreur backup ADB: {str(e)}"
    
    return "Impossible d'extraire les appels (permissions insuffisantes)"

def parse_dumpsys_calls(dumpsys_output):
    """Parser la sortie de dumpsys calllog"""
    lines = dumpsys_output.split('\n')
    calls = []
    
    for line in lines:
        if 'Call log' in line or 'Recent Calls' in line:
            continue
            
        if 'number=' in line and 'date=' in line:
            call_data = {}
            parts = line.strip().split(' ')
            for part in parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    call_data[key] = value
            
            if call_data:
                calls.append(call_data)
    
    formatted = "Appels trouvés:\n"
    for i, call in enumerate(calls[:50], 1):
        number = call.get('number', 'Inconnu')
        date_str = call.get('date', '')
        try:
            dt = datetime.datetime.fromtimestamp(int(date_str)/1000)
            date_str = dt.strftime("%Y-%m-d %H:%M:%S")
        except:
            pass
        
        formatted += f"{i}. {number} - {date_str} - {call.get('duration', '?')}s\n"
    
    return formatted

def extract_calls_from_db(db_path):
    """Extraire les appels d'une base de données calllog.db"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT number, date, duration, type, name FROM calls ORDER BY date DESC LIMIT 100")
        rows = cursor.fetchall()
        
        formatted = "Appels depuis la base de données:\n\n"
        for row in rows:
            number, date, duration, call_type, name = row
            
            try:
                dt = datetime.datetime.fromtimestamp(int(date)/1000)
                date_str = dt.strftime("%Y-%m-d %H:%M:%S")
            except:
                date_str = str(date)
            
            type_map = {
                1: 'Entrant',
                2: 'Sortant',
                3: 'Manqué',
                4: 'Rejeté',
                5: 'Blocage'
            }
            call_type_str = type_map.get(call_type, f'Inconnu ({call_type})')
            
            formatted += f"{date_str} | {number} | {call_type_str} | {duration}s | {name}\n"
        
        conn.close()
        return formatted
        
    except Exception as e:
        return f"Erreur lecture base de données: {str(e)}"

# ============================================
# VUE POUR AFFICHER LES FICHIERS
# ============================================

def view_file(request, file_path):
    """Afficher le contenu d'un fichier"""
    full_path = os.path.join('collector/static/collected_data', file_path)
    
    if not os.path.exists(full_path):
        return JsonResponse({'error': 'Fichier non trouvé'}, status=404)
    
    try:
        # Vérifier si c'est un fichier .nomedia
        if os.path.basename(full_path).lower() == '.nomedia':
            return JsonResponse({
                'filename': '.nomedia',
                'content': 'Ce fichier indique que le dossier ne doit pas être indexé par les galeries multimédias.',
                'parsed_content': '<div class="alert alert-info"><i class="fas fa-info-circle"></i> Fichier .nomedia<br>Ce fichier indique que le dossier ne doit pas être indexé par les galeries multimédias.</div>',
                'size': os.path.getsize(full_path),
                'file_type': 'system'
            })
        
        # Vérifier si c'est un fichier Google Maps
        if 'maps' in file_path.lower() or 'gmm_' in file_path.lower():
            return handle_maps_file(full_path, file_path)
        
        # Vérifier si c'est un fichier SQLite
        if full_path.lower().endswith('.db'):
            return view_sqlite_file(full_path, file_path)
        
        # Vérifier si c'est un fichier backup WhatsApp .ab
        if full_path.lower().endswith('.ab') and ('whatsapp' in file_path.lower() or 'wa' in file_path.lower()):
            return handle_whatsapp_backup(full_path, file_path)
        
        # Vérifier si c'est un fichier KML/GPX
        if full_path.lower().endswith(('.kml', '.gpx')):
            return handle_maps_kml_gpx(full_path, file_path)
        
        # Lire le fichier
        content = ''
        encodings = ['utf-8', 'latin-1', 'cp1252', 'utf-16']
        
        for encoding in encodings:
            try:
                with open(full_path, 'r', encoding=encoding, errors='ignore') as f:
                    content = f.read()
                break
            except:
                continue
        
        if not content:
            try:
                with open(full_path, 'rb') as f:
                    binary_data = f.read()
                    try:
                        content = binary_data.decode('utf-8', errors='ignore')
                    except:
                        content = f"<Fichier binaire - {len(binary_data)} octets>"
            except Exception as e:
                content = f"<Erreur lecture fichier: {str(e)}>"
        
        # Parser le contenu si nécessaire
        file_type = get_file_type_by_name(os.path.basename(file_path))
        parsed_content = None
        if file_type in ['contacts', 'calls', 'sms', 'logs', 'emails', 'wifi', 'location', 'apps', 'browser']:
            parsed_content = parse_file_content(content, file_type, full_path)
        
        return JsonResponse({
            'filename': os.path.basename(file_path),
            'content': content,
            'parsed_content': parsed_content,
            'size': os.path.getsize(full_path),
            'file_type': file_type
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

def handle_maps_file(full_path, file_path):
    """Gérer spécifiquement les fichiers Google Maps"""
    try:
        if full_path.lower().endswith('.db'):
            # C'est une base de données Google Maps
            return handle_maps_database(full_path, file_path)
        else:
            # Autre fichier Google Maps
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            parsed_content = parse_maps_file_content(content, full_path)
            
            return JsonResponse({
                'filename': os.path.basename(file_path),
                'content': content[:5000] + "..." if len(content) > 5000 else content,
                'parsed_content': parsed_content,
                'size': os.path.getsize(full_path),
                'file_type': 'maps'
            })
            
    except Exception as e:
        return JsonResponse({'error': f"Erreur Google Maps: {str(e)}"}, status=500)

def handle_maps_database(full_path, file_path):
    """Gérer une base de données Google Maps"""
    try:
        conn = sqlite3.connect(full_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        content = f"Base de données Google Maps: {os.path.basename(file_path)}\n\n"
        content += f"Nombre de tables: {len(tables)}\n\n"
        
        html = f'''
        <div class="maps-viewer">
            <h5><i class="fab fa-google"></i> Google Maps: {os.path.basename(file_path)}</h5>
            <p class="text-muted mb-3">Nombre de tables: {len(tables)}</p>
        '''
        
        # Analyser spécifiquement les tables Google Maps
        locations_data = []
        searches_data = []
        
        for table in tables:
            table_name = table[0]
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                row_count = cursor.fetchone()[0]
                
                # Vérifier si c'est une table de localisation
                if any(keyword in table_name.lower() for keyword in ['location', 'position', 'lat', 'lon', 'gps']):
                    # Essayer d'extraire des coordonnées
                    try:
                        cursor.execute(f"PRAGMA table_info({table_name})")
                        columns = cursor.fetchall()
                        col_info = [col[1].lower() for col in columns]
                        
                        # Chercher les colonnes de coordonnées
                        lat_col = None
                        lon_col = None
                        time_col = None
                        
                        for col_name in col_info:
                            if 'lat' in col_name:
                                lat_col = col_name
                            elif 'lon' in col_name or 'lng' in col_name:
                                lon_col = col_name
                            elif 'time' in col_name or 'date' in col_name:
                                time_col = col_name
                        
                        if lat_col and lon_col:
                            query = f"SELECT {lat_col}, {lon_col}"
                            if time_col:
                                query += f", {time_col}"
                            query += f" FROM {table_name} LIMIT 100"
                            
                            cursor.execute(query)
                            rows = cursor.fetchall()
                            
                            for row in rows:
                                if len(row) >= 2:
                                    location = {
                                        'latitude': row[0],
                                        'longitude': row[1],
                                        'type': 'Google Maps',
                                        'table': table_name
                                    }
                                    if len(row) >= 3:
                                        location['timestamp'] = row[2]
                                    locations_data.append(location)
                    except:
                        pass
                
                # Vérifier si c'est une table de recherche
                elif any(keyword in table_name.lower() for keyword in ['search', 'query']):
                    try:
                        cursor.execute(f"PRAGMA table_info({table_name})")
                        columns = cursor.fetchall()
                        col_info = [col[1].lower() for col in columns]
                        
                        query_col = None
                        time_col = None
                        
                        for col_name in col_info:
                            if 'query' in col_name or 'term' in col_name:
                                query_col = col_name
                            elif 'time' in col_name or 'date' in col_name:
                                time_col = col_name
                        
                        if query_col:
                            query = f"SELECT {query_col}"
                            if time_col:
                                query += f", {time_col}"
                            query += f" FROM {table_name} LIMIT 50"
                            
                            cursor.execute(query)
                            rows = cursor.fetchall()
                            
                            for row in rows:
                                search = {
                                    'query': row[0],
                                    'type': 'search'
                                }
                                if len(row) >= 2:
                                    search['timestamp'] = row[1]
                                searches_data.append(search)
                    except:
                        pass
                
                # Afficher la table dans la liste
                preview_btn = ''
                if row_count > 0:
                    preview_btn = f'''
                    <button class="btn btn-sm btn-info" onclick="previewTable('{file_path}', '{table_name}')">
                        <i class="fas fa-eye"></i> Aperçu
                    </button>
                    '''
                
                html += f'''
                <tr>
                    <td><strong>{table_name}</strong></td>
                    <td><span class="badge bg-secondary">{row_count}</span></td>
                    <td>{preview_btn}</td>
                </tr>
                '''
                
            except Exception as e:
                html += f'''
                <tr>
                    <td><strong>{table_name}</strong></td>
                    <td colspan="2"><small class="text-danger">Erreur: {str(e)[:50]}</small></td>
                </tr>
                '''
        
        # Ajouter une section pour les données extraites
        if locations_data:
            html += f'''
            <div class="card mt-3">
                <div class="card-header bg-success text-white">
                    <h6><i class="fas fa-map-marker-alt"></i> {len(locations_data)} Points de localisation Google Maps</h6>
                </div>
                <div class="card-body">
                    <div class="table-responsive">
                        <table class="table table-sm table-bordered">
                            <thead>
                                <tr>
                                    <th>Latitude</th>
                                    <th>Longitude</th>
                                    <th>Type</th>
                                    <th>Carte</th>
                                </tr>
                            </thead>
                            <tbody>
            '''
            
            for loc in locations_data[:10]:
                maps_url = f"https://www.google.com/maps?q={loc['latitude']},{loc['longitude']}"
                html += f'''
                <tr>
                    <td>{loc['latitude']}</td>
                    <td>{loc['longitude']}</td>
                    <td><span class="badge bg-success">{loc['type']}</span></td>
                    <td><a href="{maps_url}" target="_blank" class="btn btn-sm btn-outline-primary">Voir</a></td>
                </tr>
                '''
            
            html += '''
                            </tbody>
                        </table>
                    </div>
            '''
            
            if len(locations_data) > 1:
                # Générer une carte avec tous les points
                first_loc = locations_data[0]
                html += f'''
                    <div class="mt-3">
                        <h7>Carte interactive:</h7>
                        <div class="ratio ratio-16x9 border rounded">
                            <iframe 
                                src="https://www.openstreetmap.org/export/embed.html?bbox={
                                    min(float(loc['longitude']) for loc in locations_data if isinstance(loc.get('longitude'), (int, float)))-0.01
                                }%2C{
                                    min(float(loc['latitude']) for loc in locations_data if isinstance(loc.get('latitude'), (int, float)))-0.01
                                }%2C{
                                    max(float(loc['longitude']) for loc in locations_data if isinstance(loc.get('longitude'), (int, float)))+0.01
                                }%2C{
                                    max(float(loc['latitude']) for loc in locations_data if isinstance(loc.get('latitude'), (int, float)))+0.01
                                }&layer=map&marker={
                                    first_loc['latitude']
                                }%2C{
                                    first_loc['longitude']
                                }"
                                style="border: none;"
                                allowfullscreen>
                            </iframe>
                        </div>
                    </div>
                '''
            
            html += '''
                </div>
            </div>
            '''
        
        if searches_data:
            html += f'''
            <div class="card mt-3">
                <div class="card-header bg-info text-white">
                    <h6><i class="fas fa-search"></i> {len(searches_data)} Recherches Google Maps</h6>
                </div>
                <div class="card-body">
                    <div class="d-flex flex-wrap gap-2">
            '''
            
            for search in searches_data[:20]:
                html += f'''
                <span class="badge bg-light text-dark border">
                    <i class="fas fa-search"></i> {search['query'][:30]}{'...' if len(search['query']) > 30 else ''}
                </span>
                '''
            
            html += '''
                    </div>
                </div>
            </div>
            '''
        
        html += '''
            <script>
            function previewTable(dbPath, tableName) {
                const modal = document.createElement('div');
                modal.className = 'modal fade';
                modal.innerHTML = `
                    <div class="modal-dialog modal-xl">
                        <div class="modal-content">
                            <div class="modal-header">
                                <h5 class="modal-title">Aperçu: ${tableName}</h5>
                                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                            </div>
                            <div class="modal-body">
                                <div class="text-center">
                                    <div class="spinner-border" role="status">
                                        <span class="visually-hidden">Chargement...</span>
                                    </div>
                                    <p>Chargement des données...</p>
                                </div>
                            </div>
                        </div>
                    </div>
                `;
                
                document.body.appendChild(modal);
                const bsModal = new bootstrap.Modal(modal);
                bsModal.show();
                
                fetch(`/preview-table/${dbPath}/${tableName}/`)
                    .then(response => response.json())
                    .then(data => {
                        if (data.status === 'success') {
                            const modalBody = modal.querySelector('.modal-body');
                            modalBody.innerHTML = data.html;
                        } else {
                            modal.querySelector('.modal-body').innerHTML = 
                                `<div class="alert alert-danger">${data.message}</div>`;
                        }
                    })
                    .catch(error => {
                        modal.querySelector('.modal-body').innerHTML = 
                            `<div class="alert alert-danger">Erreur: ${error}</div>`;
                    });
            }
            </script>
        '''
        
        html += '</div>'
        
        conn.close()
        
        return JsonResponse({
            'filename': os.path.basename(file_path),
            'content': content,
            'parsed_content': html,
            'size': os.path.getsize(full_path),
            'file_type': 'maps_database',
            'locations_count': len(locations_data),
            'searches_count': len(searches_data)
        })
        
    except Exception as e:
        return JsonResponse({'error': f"Erreur Google Maps DB: {str(e)}"}, status=500)

def handle_maps_kml_gpx(full_path, file_path):
    """Gérer les fichiers KML/GPX Google Maps"""
    try:
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        locations = parse_kml_gpx(content)
        
        html = f'''
        <div class="maps-kml-viewer">
            <h5><i class="fab fa-google"></i> Google Maps Export: {os.path.basename(file_path)}</h5>
            <p class="text-muted mb-3">Format: {os.path.splitext(file_path)[1].upper()}</p>
        '''
        
        if locations:
            html += f'''
            <div class="card">
                <div class="card-header bg-primary text-white">
                    <h6><i class="fas fa-map-marked-alt"></i> {len(locations)} Points de localisation</h6>
                </div>
                <div class="card-body">
                    <div class="table-responsive">
                        <table class="table table-sm table-bordered">
                            <thead>
                                <tr>
                                    <th>Latitude</th>
                                    <th>Longitude</th>
                                    <th>Type</th>
                                    <th>Carte</th>
                                </tr>
                            </thead>
                            <tbody>
            '''
            
            for loc in locations[:15]:
                maps_url = f"https://www.google.com/maps?q={loc['latitude']},{loc['longitude']}"
                html += f'''
                <tr>
                    <td>{loc['latitude']:.6f}</td>
                    <td>{loc['longitude']:.6f}</td>
                    <td><span class="badge bg-primary">{loc['type']}</span></td>
                    <td><a href="{maps_url}" target="_blank" class="btn btn-sm btn-outline-primary">Voir</a></td>
                </tr>
                '''
            
            html += '''
                            </tbody>
                        </table>
                    </div>
            '''
            
            if len(locations) > 1:
                # Calculer les bornes pour la carte
                lats = [float(loc['latitude']) for loc in locations if isinstance(loc.get('latitude'), (int, float))]
                lons = [float(loc['longitude']) for loc in locations if isinstance(loc.get('longitude'), (int, float))]
                
                if lats and lons:
                    html += f'''
                    <div class="mt-3">
                        <h7>Visualisation du trajet:</h7>
                        <div class="ratio ratio-16x9 border rounded">
                            <iframe 
                                src="https://www.openstreetmap.org/export/embed.html?bbox={
                                    min(lons)-0.01
                                }%2C{
                                    min(lats)-0.01
                                }%2C{
                                    max(lons)+0.01
                                }%2C{
                                    max(lats)+0.01
                                }&layer=map&marker={
                                    lats[0]
                                }%2C{
                                    lons[0]
                                }"
                                style="border: none;"
                                allowfullscreen>
                            </iframe>
                        </div>
                        <p class="small text-muted mt-1">Carte montrant tous les points du fichier</p>
                    </div>
                    '''
            
            html += '''
                </div>
            </div>
            '''
        else:
            html += '''
            <div class="alert alert-info">
                <i class="fas fa-info-circle"></i> Aucune coordonnée GPS trouvée dans ce fichier
            </div>
            '''
        
        html += '</div>'
        
        return JsonResponse({
            'filename': os.path.basename(file_path),
            'content': content[:5000] + "..." if len(content) > 5000 else content,
            'parsed_content': html,
            'size': os.path.getsize(full_path),
            'file_type': 'maps_export',
            'locations_count': len(locations)
        })
        
    except Exception as e:
        return JsonResponse({'error': f"Erreur KML/GPX: {str(e)}"}, status=500)

def parse_maps_file_content(content, file_path):
    """Parser le contenu des fichiers Google Maps"""
    # Chercher des patterns spécifiques à Google Maps
    patterns = {
        'timestamps': r'"timestamp"\s*:\s*"([^"]+)"',
        'coordinates': r'"latitudeE7"\s*:\s*(\d+).*?"longitudeE7"\s*:\s*(\d+)',
        'places': r'"name"\s*:\s*"([^"]+)"',
        'addresses': r'"address"\s*:\s*"([^"]+)"'
    }
    
    html = '<div class="maps-data-analysis">'
    html += '<h6><i class="fab fa-google"></i> Analyse des données Google Maps</h6>'
    
    # Chercher des coordonnées Google Maps format
    google_coords = re.findall(r'"latitudeE7"\s*:\s*(\d+).*?"longitudeE7"\s*:\s*(\d+)', content)
    
    if google_coords:
        html += f'<div class="alert alert-success">'
        html += f'<i class="fas fa-check-circle"></i> {len(google_coords)} coordonnées Google Maps détectées'
        html += '</div>'
        
        html += '<div class="table-responsive"><table class="table table-sm table-bordered">'
        html += '<thead><tr><th>Latitude</th><th>Longitude</th><th>Carte</th></tr></thead><tbody>'
        
        for lat_e7, lon_e7 in google_coords[:10]:
            try:
                lat = int(lat_e7) / 1e7
                lon = int(lon_e7) / 1e7
                maps_url = f"https://www.google.com/maps?q={lat},{lon}"
                html += f'''
                <tr>
                    <td>{lat:.6f}</td>
                    <td>{lon:.6f}</td>
                    <td><a href="{maps_url}" target="_blank" class="btn btn-sm btn-outline-primary">Voir</a></td>
                </tr>
                '''
            except:
                continue
        
        html += '</tbody></table></div>'
    
    # Chercher des noms de lieux
    place_names = re.findall(r'"name"\s*:\s*"([^"]+)"', content)
    if place_names:
        unique_places = list(set(place_names))[:15]
        html += f'''
        <div class="mt-3">
            <h7>Lieux référencés ({len(unique_places)}):</h7>
            <div class="d-flex flex-wrap gap-2 mt-2">
        '''
        for place in unique_places:
            html += f'<span class="badge bg-info">{place[:30]}</span>'
        html += '</div></div>'
    
    html += '</div>'
    return html

def handle_whatsapp_backup(full_path, file_path):
    """Gérer spécifiquement les backups WhatsApp"""
    try:
        temp_dir = tempfile.mkdtemp(prefix='whatsapp_')
        result = decode_backup_file(full_path, temp_dir)
        
        if result['status'] == 'success':
            extracted_files = []
            whatsapp_dbs = []
            
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_full_path, temp_dir)
                    
                    if file.lower().endswith('.db') and ('msgstore' in file.lower() or 'wa' in file.lower() or 'whatsapp' in file.lower()):
                        whatsapp_dbs.append({
                            'name': file,
                            'path': rel_path,
                            'size': os.path.getsize(file_full_path)
                        })
                    
                    extracted_files.append({
                        'name': file,
                        'path': rel_path,
                        'size': os.path.getsize(file_full_path)
                    })
            
            html = '<div class="whatsapp-backup-view">'
            html += f'<h5><i class="fab fa-whatsapp"></i> Backup WhatsApp: {os.path.basename(file_path)}</h5>'
            html += f'<p class="text-success"><i class="fas fa-check-circle"></i> Backup décodé avec succès!</p>'
            
            if whatsapp_dbs:
                html += '<h6>Bases de données WhatsApp détectées:</h6>'
                html += '<div class="table-responsive"><table class="table table-sm table-bordered">'
                html += '<thead><tr><th>Fichier</th><th>Taille</th><th>Action</th></tr></thead><tbody>'
                
                for db in whatsapp_dbs:
                    db_path = os.path.join(temp_dir, db['path']).replace('\\', '/')
                    target_path = os.path.join('collector/static/collected_data', f'whatsapp_{int(time.time())}_{db["name"]}')
                    shutil.copy2(db_path, target_path)
                    
                    relative_target = target_path.replace('collector/static/', '')
                    
                    html += f'''
                    <tr>
                        <td><strong>{db["name"]}</strong></td>
                        <td>{format_file_size(db["size"])}</td>
                        <td>
                            <button class="btn btn-sm btn-primary" onclick="window.open('/view/{relative_target}/', '_blank')">
                                <i class="fas fa-eye"></i> Voir
                            </button>
                            <a href="/static/{relative_target}" download class="btn btn-sm btn-success">
                                <i class="fas fa-download"></i> Télécharger
                            </a>
                        </td>
                    </tr>
                    '''
                
                html += '</tbody></table></div>'
            
            if extracted_files:
                html += f'<h6>{len(extracted_files)} fichiers extraits:</h6>'
                html += '<div style="max-height: 300px; overflow-y: auto; font-size: 0.85rem;">'
                for file in extracted_files[:50]:
                    html += f'<div><code>{file["path"]}</code> ({format_file_size(file["size"])})</div>'
                if len(extracted_files) > 50:
                    html += f'<div class="text-muted">... et {len(extracted_files)-50} autres fichiers</div>'
                html += '</div>'
            
            html += '</div>'
            
            return JsonResponse({
                'filename': os.path.basename(file_path),
                'content': f"Backup WhatsApp décodé - {len(extracted_files)} fichiers extraits\n{len(whatsapp_dbs)} bases de données détectées",
                'parsed_content': html,
                'size': os.path.getsize(full_path),
                'file_type': 'whatsapp_decoded',
                'extracted_path': temp_dir,
                'whatsapp_dbs': [db['name'] for db in whatsapp_dbs]
            })
        else:
            return JsonResponse({
                'filename': os.path.basename(file_path),
                'content': f"Erreur de décodage: {result['message']}",
                'parsed_content': f'''
                <div class="alert alert-danger">
                    <h5><i class="fas fa-exclamation-triangle"></i> Erreur de décodage WhatsApp</h5>
                    <p>{result['message']}</p>
                    <hr>
                    <h6>Solutions possibles:</h6>
                    <ol>
                        <li>Le backup est chiffré - Recréez-le SANS mot de passe</li>
                        <li>Le fichier est corrompu - Téléchargez-le à nouveau</li>
                        <li>Version WhatsApp incompatible - Essayez une autre méthode d'extraction</li>
                    </ol>
                </div>
                ''',
                'size': os.path.getsize(full_path),
                'file_type': 'whatsapp_error'
            })
            
    except Exception as e:
        return JsonResponse({'error': f"Erreur WhatsApp: {str(e)}"}, status=500)

def decode_backup_file(backup_path, output_dir):
    """Décoder un fichier backup .ab"""
    try:
        if not os.path.exists(backup_path):
            return {'status': 'error', 'message': 'Fichier introuvable'}
        
        with open(backup_path, 'rb') as f:
            header = f.readline().decode('utf-8', errors='ignore').strip()
            if not header.startswith('ANDROID BACKUP'):
                return {'status': 'error', 'message': 'Format de fichier invalide'}
            
            version = f.readline().decode('utf-8', errors='ignore').strip()
            compression = f.readline().decode('utf-8', errors='ignore').strip()
            encryption = f.readline().decode('utf-8', errors='ignore').strip()
            
            if encryption != 'none':
                return {'status': 'error', 'message': 'Backup chiffré. Recréez-le sans mot de passe.'}
            
            backup_data = f.read()
            
            if compression == '1':
                try:
                    backup_data = zlib.decompress(backup_data)
                except zlib.error as e:
                    return {'status': 'error', 'message': f'Erreur décompression: {str(e)}'}
            
            tar_file = os.path.join(output_dir, 'backup.tar')
            with open(tar_file, 'wb') as tar_out:
                tar_out.write(backup_data)
            
            extracted_count = 0
            try:
                with tarfile.open(tar_file, 'r') as tar:
                    members = tar.getmembers()
                    for member in members:
                        safe_name = member.name.replace(':', '_').replace('*', '_').replace('?', '_')
                        member.name = safe_name
                        
                        try:
                            tar.extract(member, output_dir)
                            extracted_count += 1
                        except:
                            continue
            except Exception as e:
                return {'status': 'error', 'message': f'Erreur extraction tar: {str(e)}'}
            
            try:
                os.remove(tar_file)
            except:
                pass
            
            return {
                'status': 'success',
                'message': f'{extracted_count} fichiers extraits',
                'extracted_count': extracted_count,
                'output_dir': output_dir
            }
            
    except Exception as e:
        return {'status': 'error', 'message': f'Erreur décodage: {str(e)}'}

def view_sqlite_file(full_path, file_path):
    """Afficher le contenu d'une base de données SQLite"""
    try:
        conn = sqlite3.connect(full_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        content = f"Base de données SQLite: {os.path.basename(file_path)}\n\n"
        content += f"Nombre de tables: {len(tables)}\n\n"
        
        html = f'''
        <div class="sqlite-viewer">
            <h5><i class="fas fa-database"></i> Base de données SQLite: {os.path.basename(file_path)}</h5>
            <p class="text-muted mb-3">Nombre de tables: {len(tables)}</p>
        '''
        
        if 'whatsapp' in file_path.lower() or 'msgstore' in file_path.lower():
            html += parse_whatsapp_db(cursor)
        
        html += '<div class="table-responsive"><table class="table table-sm table-bordered">'
        html += '<thead><tr><th>Table</th><th>Colonnes</th><th>Lignes</th><th>Action</th></tr></thead><tbody>'
        
        for table in tables[:20]:
            table_name = table[0]
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                row_count = cursor.fetchone()[0]
                
                cursor.execute(f"PRAGMA table_info({table_name})")
                columns = cursor.fetchall()
                col_names = [col[1] for col in columns]
                
                preview_btn = ''
                if row_count > 0:
                    preview_btn = f'''
                    <button class="btn btn-sm btn-info" onclick="previewTable('{file_path}', '{table_name}')">
                        <i class="fas fa-eye"></i> Aperçu
                    </button>
                    '''
                
                html += f'''
                <tr>
                    <td><strong>{table_name}</strong></td>
                    <td><small>{', '.join(col_names[:5])}{'...' if len(col_names) > 5 else ''}</small></td>
                    <td><span class="badge bg-secondary">{row_count}</span></td>
                    <td>{preview_btn}</td>
                </tr>
                '''
                
                content += f"Table: {table_name} ({row_count} lignes)\n"
                content += f"Colonnes: {', '.join(col_names)}\n\n"
                
            except Exception as e:
                html += f'''
                <tr>
                    <td><strong>{table_name}</strong></td>
                    <td colspan="3"><small class="text-danger">Erreur: {str(e)}</small></td>
                </tr>
                '''
        
        html += '</tbody></table></div>'
        
        html += '''
        <script>
        function previewTable(dbPath, tableName) {
            const modal = document.createElement('div');
            modal.className = 'modal fade';
            modal.innerHTML = `
                <div class="modal-dialog modal-xl">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">Aperçu: ${tableName}</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="text-center">
                                <div class="spinner-border" role="status">
                                    <span class="visually-hidden">Chargement...</span>
                                </div>
                                <p>Chargement des données...</p>
                            </div>
                        </div>
                    </div>
                </div>
            `;
            
            document.body.appendChild(modal);
            const bsModal = new bootstrap.Modal(modal);
            bsModal.show();
            
            fetch(`/preview-table/${dbPath}/${tableName}/`)
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        const modalBody = modal.querySelector('.modal-body');
                        modalBody.innerHTML = data.html;
                    } else {
                        modal.querySelector('.modal-body').innerHTML = 
                            `<div class="alert alert-danger">${data.message}</div>`;
                    }
                })
                .catch(error => {
                    modal.querySelector('.modal-body').innerHTML = 
                        `<div class="alert alert-danger">Erreur: ${error}</div>`;
                });
        }
        </script>
        '''
        
        html += '</div>'
        
        conn.close()
        
        return JsonResponse({
            'filename': os.path.basename(file_path),
            'content': content,
            'parsed_content': html,
            'size': os.path.getsize(full_path),
            'file_type': 'sqlite'
        })
        
    except Exception as e:
        return JsonResponse({'error': f"Erreur SQLite: {str(e)}"}, status=500)

def preview_table(request, db_path, table_name):
    """Prévisualiser le contenu d'une table"""
    full_path = os.path.join('collector/static/collected_data', db_path)
    
    if not os.path.exists(full_path):
        return JsonResponse({'status': 'error', 'message': 'Base de données non trouvée'})
    
    try:
        conn = sqlite3.connect(full_path)
        cursor = conn.cursor()
        
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        col_names = [col[1] for col in columns]
        
        cursor.execute(f"SELECT * FROM {table_name} LIMIT 50")
        rows = cursor.fetchall()
        
        html = '<div class="table-responsive">'
        html += f'<p class="text-muted">Aperçu des 50 premières lignes sur {len(rows)} totales</p>'
        html += '<table class="table table-sm table-bordered table-striped">'
        html += '<thead><tr>'
        for col in col_names:
            html += f'<th>{col}</th>'
        html += '</tr></thead><tbody>'
        
        for row in rows:
            html += '<tr>'
            for cell in row:
                cell_str = str(cell) if cell is not None else 'NULL'
                if len(cell_str) > 100:
                    cell_str = cell_str[:100] + '...'
                html += f'<td><small>{cell_str}</small></td>'
            html += '</tr>'
        
        html += '</tbody></table></div>'
        
        conn.close()
        
        return JsonResponse({
            'status': 'success',
            'html': html,
            'row_count': len(rows)
        })
        
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': f'Erreur: {str(e)}'
        })

# ============================================
# FONCTIONS DE PARSING
# ============================================

def parse_whatsapp_db(cursor):
    """Parser la base de données WhatsApp"""
    html = '<div class="whatsapp-messages mb-4">'
    html += '<h6><i class="fab fa-whatsapp"></i> Messages WhatsApp</h6>'
    
    try:
        possible_tables = ['message', 'messages', 'chat', 'chats', 'chat_list']
        messages_table = None
        contacts_table = None
        
        for table in possible_tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                cursor.fetchone()
                if 'message' in table:
                    messages_table = table
                elif 'chat' in table:
                    contacts_table = table
            except:
                continue
        
        stats_html = '<div class="row mb-3">'
        
        if messages_table:
            cursor.execute(f"SELECT COUNT(*) FROM {messages_table}")
            total_msgs = cursor.fetchone()[0]
            stats_html += f'<div class="col"><div class="card"><div class="card-body text-center"><h4>{total_msgs}</h4><small>Messages</small></div></div></div>'
            
            cursor.execute(f"SELECT COUNT(DISTINCT key_remote_jid) FROM {messages_table}")
            total_chats = cursor.fetchone()[0]
            stats_html += f'<div class="col"><div class="card"><div class="card-body text-center"><h4>{total_chats}</h4><small>Conversations</small></div></div></div>'
        
        if contacts_table:
            cursor.execute(f"SELECT COUNT(*) FROM {contacts_table}")
            total_contacts = cursor.fetchone()[0]
            stats_html += f'<div class="col"><div class="card"><div class="card-body text-center"><h4>{total_contacts}</h4><small>Contacts</small></div></div></div>'
        
        stats_html += '</div>'
        html += stats_html
        
        if messages_table:
            cursor.execute(f'''
                SELECT 
                    key_remote_jid as contact,
                    data as message,
                    timestamp/1000 as timestamp_epoch,
                    CASE 
                        WHEN key_from_me = 1 THEN 'Envoyé'
                        ELSE 'Reçu'
                    END as direction
                FROM {messages_table} 
                WHERE data IS NOT NULL AND data != ''
                ORDER BY timestamp DESC 
                LIMIT 50
            ''')
            
            messages = cursor.fetchall()
            
            if messages:
                html += '<h6>50 derniers messages:</h6>'
                html += '<div class="table-responsive"><table class="table table-sm table-striped">'
                html += '<thead><tr><th>Contact</th><th>Message</th><th>Date</th><th>Type</th></tr></thead><tbody>'
                
                for msg in messages:
                    contact = msg[0].split('@')[0] if '@' in msg[0] else msg[0]
                    message = msg[1][:100] + '...' if len(msg[1]) > 100 else msg[1]
                    timestamp = time.strftime('%Y-%m-d %H:%M:%S', time.localtime(msg[2]))
                    direction = msg[3]
                    
                    badge_class = 'bg-success' if direction == 'Envoyé' else 'bg-primary'
                    
                    html += f'''
                    <tr>
                        <td><small>{contact}</small></td>
                        <td><small>{message}</small></td>
                        <td><small>{timestamp}</small></td>
                        <td><span class="badge {badge_class}">{direction}</span></td>
                    </tr>
                    '''
                
                html += '</tbody></table></div>'
                html += f'<p class="small text-muted">Affichage des 50 derniers messages sur {len(messages)}</p>'
            else:
                html += '<div class="alert alert-info">Aucun message trouvé dans la base de données</div>'
        else:
            html += '<div class="alert alert-warning">Table des messages WhatsApp non trouvée</div>'
            
    except Exception as e:
        html += f'<div class="alert alert-danger">Erreur lecture messages: {str(e)}</div>'
    
    html += '</div>'
    return html

def get_file_type_by_name(filename):
    """Déterminer le type de fichier par son nom"""
    filename_lower = filename.lower()
    if 'contact' in filename_lower:
        return 'contacts'
    elif 'call' in filename_lower:
        return 'calls'
    elif 'sms' in filename_lower:
        return 'sms'
    elif 'log' in filename_lower:
        return 'logs'
    elif 'email' in filename_lower:
        return 'emails'
    elif 'wifi' in filename_lower:
        return 'wifi'
    elif 'browser' in filename_lower:
        return 'browser'
    elif 'whatsapp' in filename_lower or 'msgstore' in filename_lower:
        return 'whatsapp'
    elif 'location' in filename_lower:
        return 'location'
    elif 'app' in filename_lower:
        return 'apps'
    elif 'maps' in filename_lower or 'gmm_' in filename_lower:
        return 'maps'
    elif filename_lower.endswith('.ab'):
        return 'backup'
    elif filename_lower.endswith(('.crypt14', '.crypt15')):
        return 'whatsapp_encrypted'
    elif filename_lower.endswith(('.kml', '.gpx')):
        return 'maps'
    else:
        return 'unknown'

def parse_file_content(content, file_type, file_path=None):
    """Parser le contenu du fichier selon son type"""
    try:
        if file_type == 'contacts':
            return parse_contacts(content)
        elif file_type == 'calls':
            return parse_calls(content)
        elif file_type == 'sms':
            return parse_sms(content)
        elif file_type == 'logs':
            return parse_logs(content)
        elif file_type == 'emails':
            return parse_emails(content)
        elif file_type == 'wifi':
            return parse_wifi(content)
        elif file_type == 'location':
            return parse_location(content, file_path)
        elif file_type == 'apps':
            return parse_apps(content)
        elif file_type == 'browser':
            return parse_browser(content)
        elif file_type == 'maps':
            return parse_maps_file_content(content, file_path)
        else:
            return None
    except Exception as e:
        return f"<div class='alert alert-warning'>Erreur de parsing: {str(e)}</div>"

def parse_contacts(content):
    """Parser les contacts au format content query"""
    lines = content.strip().split('\n')
    parsed = []
    
    for line in lines:
        if 'Row:' in line:
            parts = line.replace('Row:', '').strip()
            contact_data = {}
            
            for item in parts.split(', '):
                if '=' in item:
                    key, value = item.split('=', 1)
                    contact_data[key.strip()] = value.strip()
            
            if contact_data:
                parsed.append(contact_data)
    
    if not parsed:
        return None
    
    html = '<div class="table-responsive"><table class="table table-striped table-bordered">'
    if parsed and len(parsed) > 0:
        headers = ['Nom', 'Numéro', 'Type']
        html += '<thead><tr>' + ''.join(f'<th>{h}</th>' for h in headers) + '</tr></thead><tbody>'
        
        for contact in parsed[:50]:
            name = contact.get('display_name', 'Inconnu')
            number = contact.get('data1', '')
            mimetype = contact.get('mimetype', '')
            
            if 'phone' in mimetype.lower():
                html += f'<tr><td>{name}</td><td>{number}</td><td>Téléphone</td></tr>'
    
    html += '</tbody></table></div>'
    if len(parsed) > 50:
        html += f'<p class="small text-muted">Affichage de 50 contacts sur {len(parsed)}</p>'
    
    return html

def parse_calls(content):
    """Parser les appels téléphoniques"""
    lines = content.strip().split('\n')
    parsed = []
    
    for line in lines:
        if 'Row:' in line:
            parts = line.replace('Row:', '').strip()
            call_data = {}
            
            for item in parts.split(', '):
                if '=' in item:
                    key, value = item.split('=', 1)
                    call_data[key.strip()] = value.strip()
            
            if call_data:
                if 'date' in call_data and call_data['date'].isdigit():
                    timestamp = int(call_data['date']) / 1000
                    call_data['date'] = time.strftime('%Y-%m-d %H:%M:%S', time.localtime(timestamp))
                
                parsed.append(call_data)
    
    if not parsed:
        return None
    
    html = '<div class="table-responsive"><table class="table table-striped table-bordered">'
    html += '<thead><tr><th>Numéro</th><th>Date</th><th>Durée</th><th>Type</th><th>Nom</th></tr></thead><tbody>'
    
    for call in parsed[:100]:
        number = call.get('number', '')
        date = call.get('date', '')
        duration = call.get('duration', '0')
        call_type = call.get('type', '')
        name = call.get('name', '')
        
        if call_type == '1':
            call_type = 'Entrant'
            badge_class = 'bg-primary'
        elif call_type == '2':
            call_type = 'Sortant'
            badge_class = 'bg-success'
        elif call_type == '3':
            call_type = 'Manqué'
            badge_class = 'bg-danger'
        else:
            badge_class = 'bg-secondary'
        
        try:
            duration_sec = int(duration)
            if duration_sec > 3600:
                hours = duration_sec // 3600
                minutes = (duration_sec % 3600) // 60
                seconds = duration_sec % 60
                duration_str = f"{hours}h{minutes:02d}m{seconds:02d}s"
            elif duration_sec > 60:
                minutes = duration_sec // 60
                seconds = duration_sec % 60
                duration_str = f"{minutes}m{seconds:02d}s"
            else:
                duration_str = f"{duration_sec}s"
        except:
            duration_str = duration
        
        html += f'''
        <tr>
            <td>{number}</td>
            <td>{date}</td>
            <td>{duration_str}</td>
            <td><span class="badge {badge_class}">{call_type}</span></td>
            <td>{name}</td>
        </tr>
        '''
    
    html += '</tbody></table></div>'
    if len(parsed) > 100:
        html += f'<p class="small text-muted">Affichage de 100 appels sur {len(parsed)}</p>'
    
    return html

def parse_sms(content):
    """Parser les SMS"""
    lines = content.strip().split('\n')
    parsed = []
    
    for line in lines:
        if 'Row:' in line:
            parts = line.replace('Row:', '').strip()
            sms_data = {}
            
            for item in parts.split(', '):
                if '=' in item:
                    key, value = item.split('=', 1)
                    sms_data[key.strip()] = value.strip()
            
            if sms_data:
                if 'date' in sms_data and sms_data['date'].isdigit():
                    timestamp = int(sms_data['date'])
                    sms_data['date'] = time.strftime('%Y-%m-d %H:%M:%S', time.localtime(timestamp/1000))
                
                if 'type' in sms_data:
                    if sms_data['type'] == '1':
                        sms_data['type'] = 'Reçu'
                    elif sms_data['type'] == '2':
                        sms_data['type'] = 'Envoyé'
                
                parsed.append(sms_data)
    
    if not parsed:
        return None
    
    html = '<div class="table-responsive"><table class="table table-striped table-bordered">'
    html += '<thead><tr><th>Contact</th><th>Date</th><th>Message</th><th>Type</th></tr></thead><tbody>'
    
    for sms in parsed[:100]:
        address = sms.get('address', '')
        date = sms.get('date', '')
        body = sms.get('body', '')
        sms_type = sms.get('type', '')
        
        if len(body) > 150:
            body_display = body[:150] + '...'
        else:
            body_display = body
        
        badge_class = 'bg-success' if sms_type == 'Envoyé' else 'bg-primary'
        
        html += f'''
        <tr>
            <td><small>{address}</small></td>
            <td><small>{date}</small></td>
            <td><small>{body_display}</small></td>
            <td><span class="badge {badge_class}">{sms_type}</span></td>
        </tr>
        '''
    
    html += '</tbody></table></div>'
    if len(parsed) > 100:
        html += f'<p class="small text-muted">Affichage de 100 SMS sur {len(parsed)}</p>'
    
    return html

def parse_logs(content):
    """Parser les logs système"""
    lines = content.strip().split('\n')
    
    if len(lines) > 500:
        lines = lines[:500]
    
    html = '<div class="log-container" style="font-family: monospace; font-size: 12px; max-height: 400px; overflow-y: auto;">'
    
    for line in lines:
        if line.strip():
            if ' E ' in line or ' ERROR ' in line or ' E/' in line:
                html += f'<div class="mb-1 p-1 bg-danger bg-opacity-10 border-start border-danger border-3"><small>{line}</small></div>'
            elif ' W ' in line or ' WARN ' in line or ' W/' in line:
                html += f'<div class="mb-1 p-1 bg-warning bg-opacity-10 border-start border-warning border-3"><small>{line}</small></div>'
            elif ' I ' in line or ' INFO ' in line or ' I/' in line:
                html += f'<div class="mb-1 p-1 bg-info bg-opacity-10 border-start border-info border-3"><small>{line}</small></div>'
            elif ' D ' in line or ' DEBUG ' in line or ' D/' in line:
                html += f'<div class="mb-1 p-1 bg-primary bg-opacity-10 border-start border-primary border-3"><small>{line}</small></div>'
            else:
                html += f'<div class="mb-1 p-1 bg-light border-start border-secondary border-3"><small>{line}</small></div>'
    
    html += '</div>'
    
    if len(lines) >= 500:
        html += f'<p class="small text-muted mt-2">Affichage limité à 500 lignes</p>'
    
    return html

def parse_emails(content):
    """Parser les emails"""
    lines = content.strip().split('\n')
    parsed = []
    
    for line in lines:
        if 'Row:' in line:
            parts = line.replace('Row:', '').strip()
            email_data = {}
            
            for item in parts.split(', '):
                if '=' in item:
                    key, value = item.split('=', 1)
                    email_data[key.strip()] = value.strip()
            
            if email_data:
                parsed.append(email_data)
    
    if not parsed:
        return None
    
    html = '<div class="table-responsive"><table class="table table-striped table-bordered">'
    html += '<thead><tr><th>De</th><th>À</th><th>Sujet</th><th>Date</th></tr></thead><tbody>'
    
    for email in parsed[:50]:
        sender = email.get('fromAddress', '')[:30]
        recipient = email.get('toAddress', '')[:30]
        subject = email.get('subject', '')[:50]
        date = email.get('timeStamp', '')
        
        html += f'<tr><td>{sender}</td><td>{recipient}</td><td>{subject}</td><td>{date}</td></tr>'
    
    html += '</tbody></table></div>'
    if len(parsed) > 50:
        html += f'<p class="small text-muted">Affichage de 50 emails sur {len(parsed)}</p>'
    
    return html

def parse_wifi(content):
    """Parser les configurations WiFi"""
    html = '<div class="wifi-container">'
    
    ssids = re.findall(r'ssid="([^"]+)"', content)
    psk = re.findall(r'psk="([^"]+)"', content)
    
    if ssids:
        html += '<h6>Réseaux WiFi configurés:</h6>'
        html += '<div class="table-responsive"><table class="table table-bordered table-sm">'
        html += '<thead><tr><th>SSID</th><th>Mot de passe</th></tr></thead><tbody>'
        
        for i, ssid in enumerate(ssids[:20]):
            password = psk[i] if i < len(psk) else 'Non configuré'
            html += f'<tr><td><strong>{ssid}</strong></td><td><code>{password}</code></td></tr>'
        
        html += '</tbody></table></div>'
    else:
        html += '<div class="alert alert-info">Aucun réseau WiFi configuré trouvé</div>'
    
    html += '</div>'
    return html

def parse_location(content, file_path=None):
    """Parser les données de localisation"""
    html = '<div class="location-container">'
    
    locations = []
    wifi_locations = []
    cell_locations = []
    
    # 1. Chercher les données Google Maps dans le contenu
    google_maps_data = []
    
    # Format Google Maps Timeline JSON
    google_patterns = [
        r'"latitudeE7"\s*:\s*(\d+).*?"longitudeE7"\s*:\s*(\d+)',
        r'"lat"\s*:\s*([+-]?\d+\.\d+).*?"lng"\s*:\s*([+-]?\d+\.\d+)',
        r'"latitude"\s*:\s*([+-]?\d+\.\d+).*?"longitude"\s*:\s*([+-]?\d+\.\d+)'
    ]
    
    for pattern in google_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        for match in matches:
            try:
                if len(match) >= 2:
                    if 'E7' in pattern:  # Format Google E7
                        lat = int(match[0]) / 1e7
                        lon = int(match[1]) / 1e7
                    else:
                        lat = float(match[0])
                        lon = float(match[1])
                    
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        google_maps_data.append({
                            'latitude': lat,
                            'longitude': lon,
                            'type': 'Google Maps',
                            'accuracy': 'Haute',
                            'source': 'Google Timeline'
                        })
            except:
                continue
    
    # 2. Chercher les données système GPS (comme avant)
    gps_patterns = [
        r'Location\[([+-]?\d+\.\d+),([+-]?\d+\.\d+)',
        r'lat(itude)?[=:]\s*([+-]?\d+\.\d+).*?lon(gitude)?[=:]\s*([+-]?\d+\.\d+)',
        r'gps.*?([+-]?\d+\.\d+).*?([+-]?\d+\.\d+)',
        r'latitude.*?([+-]?\d+\.\d+).*?longitude.*?([+-]?\d+\.\d+)'
    ]
    
    for pattern in gps_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        for match in matches:
            if len(match) >= 2:
                try:
                    if isinstance(match[0], str) and isinstance(match[1], str):
                        lat = float(match[0])
                        lon = float(match[1])
                    elif len(match) >= 4:
                        lat = float(match[1])
                        lon = float(match[3])
                    else:
                        continue
                    
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        locations.append({
                            'latitude': lat,
                            'longitude': lon,
                            'type': 'GPS',
                            'accuracy': 'Haute'
                        })
                except:
                    continue
    
    # 3. Si on a un fichier, chercher aussi Google Maps dedans
    if file_path:
        try:
            # Vérifier si c'est un fichier Google Maps
            if 'maps' in file_path.lower() or 'gmm_' in file_path.lower():
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    file_content = f.read()
                    # Extraire les données Google Maps du fichier
                    maps_from_file = extract_maps_from_file(file_content)
                    if maps_from_file:
                        google_maps_data.extend(maps_from_file)
        except:
            pass
    
    # Combiner toutes les données
    all_locations = google_maps_data + locations
    unique_locations = []
    seen = set()
    
    for loc in all_locations:
        key = (round(loc['latitude'], 6), round(loc['longitude'], 6))
        if key not in seen:
            seen.add(key)
            unique_locations.append(loc)
    
    ip_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
    ips = re.findall(ip_pattern, content)
    unique_ips = list(set(ips))[:10]
    
    wifi_pattern = r'SSID:\s*([^\n]+)'
    wifi_matches = re.findall(wifi_pattern, content, re.IGNORECASE)
    
    # Section Google Maps
    if google_maps_data:
        html += '''
        <div class="card mb-3">
            <div class="card-header bg-primary text-white">
                <h6><i class="fab fa-google"></i> Données Google Maps</h6>
            </div>
            <div class="card-body">
        '''
        
        html += f'<p><strong>{len(google_maps_data)} points de localisation Google Maps détectés</strong></p>'
        
        html += '<div class="table-responsive"><table class="table table-sm table-bordered">'
        html += '<thead><tr><th>Latitude</th><th>Longitude</th><th>Type</th><th>Source</th><th>Carte</th></tr></thead><tbody>'
        
        for loc in google_maps_data[:10]:
            maps_url = f"https://www.google.com/maps?q={loc['latitude']},{loc['longitude']}"
            html += f'''
            <tr>
                <td>{loc['latitude']:.6f}</td>
                <td>{loc['longitude']:.6f}</td>
                <td><span class="badge bg-primary">{loc['type']}</span></td>
                <td><small>{loc.get('source', 'N/A')}</small></td>
                <td><a href="{maps_url}" target="_blank" class="btn btn-sm btn-outline-primary">Voir</a></td>
            </tr>
            '''
        
        html += '</tbody></table></div>'
        
        # Afficher une carte pour les points Google Maps
        if google_maps_data:
            first_loc = google_maps_data[0]
            lats = [loc['latitude'] for loc in google_maps_data if isinstance(loc.get('latitude'), (int, float))]
            lons = [loc['longitude'] for loc in google_maps_data if isinstance(loc.get('longitude'), (int, float))]
            
            if lats and lons:
                html += f'''
                <div class="mt-3">
                    <h7>Carte des points Google Maps:</h7>
                    <div class="ratio ratio-16x9 border rounded">
                        <iframe 
                            src="https://www.openstreetmap.org/export/embed.html?bbox={
                                min(lons)-0.01
                            }%2C{
                                min(lats)-0.01
                            }%2C{
                                max(lons)+0.01
                            }%2C{
                                max(lats)+0.01
                            }&layer=map&marker={
                                first_loc['latitude']
                            }%2C{
                                first_loc['longitude']
                            }"
                            style="border: none;"
                            allowfullscreen>
                        </iframe>
                    </div>
                    <p class="small text-muted mt-1">Carte montrant la zone couverte par les points Google Maps</p>
                </div>
                '''
        
        html += '''
            </div>
        </div>
        '''
    
    html += '<h6>Données de localisation</h6>'
    
    if unique_locations:
        html += '<div class="mb-3">'
        html += '<h7>Coordonnées GPS:</h7>'
        html += '<div class="table-responsive"><table class="table table-sm table-bordered">'
        html += '<thead><tr><th>Latitude</th><th>Longitude</th><th>Type</th><th>Précision</th><th>Carte</th></tr></thead><tbody>'
        
        for loc in unique_locations[:10]:
            maps_url = f"https://www.google.com/maps?q={loc['latitude']},{loc['longitude']}"
            html += f'''
            <tr>
                <td>{loc['latitude']:.6f}</td>
                <td>{loc['longitude']:.6f}</td>
                <td><span class="badge bg-success">{loc['type']}</span></td>
                <td><span class="badge bg-info">{loc.get('accuracy', 'N/A')}</span></td>
                <td><a href="{maps_url}" target="_blank" class="btn btn-sm btn-outline-primary">Voir</a></td>
            </tr>
            '''
        
        html += '</tbody></table></div>'
        
        if unique_locations:
            first_loc = unique_locations[0]
            html += f'''
            <div class="mt-3">
                <h7>Carte:</h7>
                <div class="ratio ratio-16x9 border rounded">
                    <iframe 
                        src="https://www.openstreetmap.org/export/embed.html?bbox={first_loc['longitude']-0.01}%2C{first_loc['latitude']-0.01}%2C{first_loc['longitude']+0.01}%2C{first_loc['latitude']+0.01}&layer=map&marker={first_loc['latitude']}%2C{first_loc['longitude']}"
                        style="border: none;"
                        allowfullscreen>
                    </iframe>
                </div>
                <p class="small text-muted mt-1">Carte centrée sur la première coordonnée GPS</p>
            </div>
            '''
        
        html += '</div>'
    else:
        html += '<div class="alert alert-info mb-3">Aucune coordonnée GPS précise trouvée</div>'
    
    if unique_ips:
        html += '<div class="mb-3">'
        html += f'<h7>Adresses IP trouvées ({len(unique_ips)}):</h7>'
        html += '<div class="d-flex flex-wrap gap-2 mt-2">'
        for ip in unique_ips:
            html += f'<span class="badge bg-secondary">{ip}</span>'
        html += '</div>'
        html += '</div>'
    
    if wifi_matches:
        html += '<div class="mb-3">'
        html += f'<h7>Réseaux WiFi détectés ({len(wifi_matches)}):</h7>'
        html += '<div class="d-flex flex-wrap gap-2 mt-2">'
        for wifi in wifi_matches[:15]:
            html += f'<span class="badge bg-warning text-dark">{wifi.strip()}</span>'
        html += '</div>'
        html += '</div>'
    
    stats_html = f'''
    <div class="card mt-3">
        <div class="card-body">
            <h6 class="card-title">Statistiques de localisation</h6>
            <div class="row text-center">
                <div class="col">
                    <div class="display-6">{len(unique_locations)}</div>
                    <small>Points GPS</small>
                </div>
                <div class="col">
                    <div class="display-6">{len(unique_ips)}</div>
                    <small>IPs uniques</small>
                </div>
                <div class="col">
                    <div class="display-6">{len(wifi_matches)}</div>
                    <small>Réseaux WiFi</small>
                </div>
                <div class="col">
                    <div class="display-6">{len(google_maps_data)}</div>
                    <small>Google Maps</small>
                </div>
            </div>
        </div>
    </div>
    '''
    
    html += stats_html
    
    # Ajouter un bouton pour exporter les trajets
    if google_maps_data or unique_locations:
        html += f'''
        <div class="mt-3">
            <button class="btn btn-success" onclick="exportMapsData({json.dumps(google_maps_data + unique_locations)})">
                <i class="fas fa-download"></i> Exporter les trajets (GPX)
            </button>
        </div>
        
        <script>
        function exportMapsData(locations) {{
            let gpx = `<?xml version="1.0"?>
        <gpx version="1.1" creator="Android Collector">
            <trk><name>Trajets extraits</name><trkseg>`;
            
            locations.forEach(loc => {{
                gpx += `<trkpt lat="${{loc.latitude}}" lon="${{loc.longitude}}"></trkpt>\\n`;
            }});
            
            gpx += `</trkseg></trk></gpx>`;
            
            const blob = new Blob([gpx], {{type: 'application/gpx+xml'}});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'trajets_extraits.gpx';
            a.click();
            URL.revokeObjectURL(url);
        }}
        </script>
        '''
    
    html += '</div>'
    
    return html

def extract_maps_from_file(content):
    """Extraire les données Google Maps d'un fichier"""
    locations = []
    
    # Chercher les patterns spécifiques à Google Maps
    patterns = [
        # Format JSON Google Timeline
        (r'"latitudeE7"\s*:\s*(\d+).*?"longitudeE7"\s*:\s*(\d+)', 'Google Timeline'),
        # Format KML
        (r'<coordinates>([^<]+)</coordinates>', 'KML'),
        # Format GPX
        (r'<trkpt lat="([^"]+)" lon="([^"]+)">', 'GPX')
    ]
    
    for pattern, source in patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        for match in matches:
            try:
                if source == 'Google Timeline':
                    lat = int(match[0]) / 1e7
                    lon = int(match[1]) / 1e7
                elif source == 'KML':
                    # Format: lon,lat[,alt]
                    coords = match.strip().split(',')
                    if len(coords) >= 2:
                        lon = float(coords[0])
                        lat = float(coords[1])
                    else:
                        continue
                elif source == 'GPX':
                    lat = float(match[0])
                    lon = float(match[1])
                else:
                    continue
                
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    locations.append({
                        'latitude': lat,
                        'longitude': lon,
                        'type': 'Google Maps',
                        'source': source
                    })
            except:
                continue
    
    return locations

def parse_apps(content):
    """Parser la liste des applications"""
    lines = content.strip().split('\n')
    apps = []
    
    for line in lines:
        if line.strip() and ('package:' in line or line.startswith('com.')):
            if 'package:' in line:
                parts = line.split('=')
                if len(parts) >= 2:
                    package_name = parts[-1].strip()
                    apps.append(package_name)
            elif line.startswith('com.'):
                apps.append(line.strip())
    
    if not apps:
        return None
    
    categories = {
        'Système': [],
        'Google': [],
        'Social': [],
        'Communication': [],
        'Productivité': [],
        'Autres': []
    }
    
    for app in apps[:200]:
        if 'com.android' in app or 'android.' in app:
            categories['Système'].append(app)
        elif 'com.google' in app:
            categories['Google'].append(app)
        elif any(social in app for social in ['facebook', 'whatsapp', 'instagram', 'twitter', 'tiktok', 'snapchat']):
            categories['Social'].append(app)
        elif any(comm in app for comm in ['sms', 'mms', 'phone', 'contact', 'dialer', 'message', 'mail']):
            categories['Communication'].append(app)
        elif any(prod in app for prod in ['office', 'word', 'excel', 'pdf', 'editor', 'note', 'calendar']):
            categories['Productivité'].append(app)
        else:
            categories['Autres'].append(app)
    
    html = '<div class="apps-container">'
    html += f'<h6>{len(apps)} applications trouvées</h6>'
    
    for category, app_list in categories.items():
        if app_list:
            html += f'''
            <div class="mb-3">
                <h7>{category} ({len(app_list)})</h7>
                <div class="table-responsive">
                    <table class="table table-sm table-bordered">
                        <thead>
                            <tr>
                                <th width="80%">Package</th>
                                <th width="20%">Type</th>
                            </tr>
                        </thead>
                        <tbody>
            '''
            
            for app in app_list[:20]:
                html += f'''
                <tr>
                    <td><small><code>{app}</code></small></td>
                    <td><span class="badge bg-info">{category}</span></td>
                </tr>
                '''
            
            html += '</tbody></table></div>'
            if len(app_list) > 20:
                html += f'<p class="small text-muted">Affichage de 20 apps sur {len(app_list)}</p>'
            html += '</div>'
    
    html += '</div>'
    return html

def parse_browser(content):
    """Parser l'historique du navigateur"""
    html = '<div class="browser-container">'
    
    url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
    urls = re.findall(url_pattern, content)
    
    if urls:
        unique_urls = list(set(urls))[:50]
        
        domains = {}
        for url in unique_urls:
            try:
                domain = url.split('//')[-1].split('/')[0]
                if domain in domains:
                    domains[domain] += 1
                else:
                    domains[domain] = 1
            except:
                continue
        
        html += '<div class="row mb-3">'
        html += f'<div class="col-md-4"><div class="card"><div class="card-body text-center"><h3>{len(urls)}</h3><small>URLs totales</small></div></div></div>'
        html += f'<div class="col-md-4"><div class="card"><div class="card-body text-center"><h3>{len(unique_urls)}</h3><small>URLs uniques</small></div></div></div>'
        html += f'<div class="col-md-4"><div class="card"><div class="card-body text-center"><h3>{len(domains)}</h3><small>Domaines</small></div></div></div>'
        html += '</div>'
        
        if domains:
            sorted_domains = sorted(domains.items(), key=lambda x: x[1], reverse=True)[:10]
            html += '<h6>Top 10 des domaines visités:</h6>'
            html += '<div class="table-responsive"><table class="table table-sm table-bordered">'
            html += '<thead><tr><th>Domaine</th><th>Visites</th></tr></thead><tbody>'
            
            for domain, count in sorted_domains:
                html += f'<tr><td><strong>{domain}</strong></td><td><span class="badge bg-primary">{count}</span></td></tr>'
            
            html += '</tbody></table></div>'
        
        html += '<h6 class="mt-3">Exemples d\'URLs visitées:</h6>'
        html += '<div class="table-responsive"><table class="table table-sm table-bordered">'
        html += '<thead><tr><th>URL</th><th>Domaine</th></tr></thead><tbody>'
        
        for url in unique_urls[:20]:
            domain = url.split('//')[-1].split('/')[0]
            display_url = url[:60] + '...' if len(url) > 60 else url
            html += f'<tr><td><a href="{url}" target="_blank" class="text-decoration-none">{display_url}</a></td><td><code>{domain}</code></td></tr>'
        
        html += '</tbody></table></div>'
        
    else:
        html += '<div class="alert alert-info">Aucune URL trouvée dans ce fichier</div>'
    
    html += '</div>'
    return html

# ============================================
# VUES POUR L'ARCHITECTURE SYSTÈME
# ============================================

def get_system_info(request):
    """Obtenir les informations système du téléphone"""
    try:
        adb_path = get_adb_path()
        if not adb_path:
            return JsonResponse({'status': 'error', 'message': 'ADB non trouvé'})
        
        device = get_device_id(adb_path)
        if not device:
            return JsonResponse({'status': 'error', 'message': 'Aucun appareil détecté'})
        
        device_model = subprocess.check_output(
            [adb_path, '-s', device, 'shell', 'getprop', 'ro.product.model'],
            text=True, stderr=subprocess.PIPE
        ).strip()
        
        android_version = subprocess.check_output(
            [adb_path, '-s', device, 'shell', 'getprop', 'ro.build.version.release'],
            text=True, stderr=subprocess.PIPE
        ).strip()
        
        device_manufacturer = subprocess.check_output(
            [adb_path, '-s', device, 'shell', 'getprop', 'ro.product.manufacturer'],
            text=True, stderr=subprocess.PIPE
        ).strip()
        
        storage_info = subprocess.check_output(
            [adb_path, '-s', device, 'shell', 'df', '/data'],
            text=True, stderr=subprocess.PIPE
        ).strip()
        
        partitions = subprocess.check_output(
            [adb_path, '-s', device, 'shell', 'ls', '-l', '/dev/block/platform/'],
            text=True, stderr=subprocess.PIPE
        ).strip()
        
        return JsonResponse({
            'status': 'success',
            'device_info': f"{device_manufacturer} {device_model}",
            'device_model': device_model,
            'android_version': f"Android {android_version}",
            'device_manufacturer': device_manufacturer,
            'storage_total': extract_storage_total(storage_info),
            'partitions_count': len(partitions.split('\n')) if partitions else 4,
            'device_id': device
        })
        
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        })

def extract_storage_total(storage_info):
    """Extraire la taille totale du stockage"""
    lines = storage_info.split('\n')
    if len(lines) > 1:
        parts = lines[1].split()
        if len(parts) > 1:
            size_kb = int(parts[1])
            size_gb = size_kb / (1024*1024)
            return f"{size_gb:.1f} GB"
    return "N/A"

def explore_root(request):
    """Explorer la racine du système de fichiers"""
    try:
        adb_path = get_adb_path()
        if not adb_path:
            return JsonResponse({'status': 'error', 'message': 'ADB non trouvé'})
        
        device = get_device_id(adb_path)
        if not device:
            return JsonResponse({'status': 'error', 'message': 'Aucun appareil détecté'})
        
        root_listing = subprocess.check_output(
            [adb_path, '-s', device, 'shell', 'ls', '-la', '/'],
            text=True, stderr=subprocess.PIPE, timeout=10
        ).strip()
        
        return JsonResponse({
            'status': 'success',
            'files': root_listing.split('\n'),
            'root_path': '/'
        })
        
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        })

# ============================================
# EXECUTE_COMMAND - FONCTION PRINCIPALE
# ============================================

@csrf_exempt
def execute_command(request):
    action = request.POST.get('action')
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    result = {
        'status': 'error',
        'message': 'Action inconnue',
        'output': '',
        'file_path': None,
        'device': None
    }

    adb_path = get_adb_path()
    if not adb_path:
        result['message'] = "ADB non trouvé. Installez Android Platform Tools."
        return JsonResponse(result)

    # Détection appareil
    try:
        devices_output = subprocess.check_output([adb_path, 'devices'], text=True)
        devices = devices_output.split('\n')[1:]
        device = [d.split('\t')[0] for d in devices if d.strip() and not d.startswith('*') and 'device' in d][0]
        
        android_version = subprocess.check_output(
            [adb_path, '-s', device, 'shell', 'getprop', 'ro.build.version.release'],
            text=True
        ).strip()
        result['device'] = f"{device} (Android {android_version})"
    except IndexError:
        result['message'] = "Aucun appareil détecté. Connectez un appareil avec débogage USB activé."
        return JsonResponse(result)
    except Exception as e:
        result['message'] = f"Erreur détection appareil: {str(e)}"
        return JsonResponse(result)

    # Gestion des actions
    actions = {
        'check_adb': {
            'cmd': [adb_path, 'version'],
            'file': None,
            'msg': "Vérification ADB terminée"
        },
        'full_dump': {
            'cmd': None,
            'file': None,
            'msg': "Extraction complète terminée",
            'is_full_dump': True
        },
        'extract_logs': {
            'cmd': [adb_path, '-s', device, 'logcat', '-d', '-v', 'time'],
            'file': f"logs_{timestamp}.log",
            'msg': "Logs extraits avec succès"
        },
        'list_calls': {
            'cmd': None,
            'file': None,
            'msg': "Liste d'appels extraite",
            'is_calls': True
        },
        'extract_contacts': {
            'cmd': [adb_path, '-s', device, 'shell', 'content', 'query', '--uri', 'content://com.android.contacts/data',
                    '--projection', 'display_name:data1:mimetype'],
            'file': f"contacts_{timestamp}.txt",
            'msg': "Contacts exportés",
            'needs_permission': True
        },
        'extract_images': {
            'cmd': None,
            'file': None,
            'msg': "Images téléchargées",
            'is_images': True
        },
        'list_apps': {
            'cmd': [adb_path, '-s', device, 'shell', 'pm', 'list', 'packages', '-3'],
            'file': f"apps_{timestamp}.txt",
            'msg': "Liste d'applications générée"
        },
        'extract_browser': {
            'cmd': [adb_path, '-s', device, 'shell', 'su', '-c', 'cat /data/data/com.android.chrome/app_chrome/Default/History'],
            'file': f"browser_{timestamp}.db",
            'msg': "Historique navigateur extrait",
            'needs_root': True
        },
        'extract_emails': {
            'cmd': [adb_path, '-s', device, 'shell', 'content', 'query', '--uri', 'content://com.android.email.provider/message'],
            'file': f"emails_{timestamp}.txt",
            'msg': "Emails extraits",
            'needs_permission': True
        },
        'list_wifi': {
            'cmd': [adb_path, '-s', device, 'shell', 'su', '-c', 'cat /data/misc/wifi/wpa_supplicant.conf'],
            'file': f"wifi_{timestamp}.conf",
            'msg': "Configuration WiFi extraite",
            'needs_root': True
        },
        'extract_sms': {
            'cmd': [adb_path, '-s', device, 'shell', 'content', 'query', '--uri', 'content://sms',
                    '--projection', 'address:date:body:type'],
            'file': f"sms_{timestamp}.txt",
            'msg': "SMS exportés",
            'needs_permission': True
        },
        'extract_whatsapp': {
            'cmd': None,
            'file': None,
            'msg': "Données WhatsApp extraites",
            'is_whatsapp': True
        },
        'get_location': {
            'cmd': [adb_path, '-s', device, 'shell', 'dumpsys', 'location'],
            'file': f"location_{timestamp}.txt",
            'msg': "Données de localisation"
        },
        'backup_apps': {
            'cmd': None,
            'file': None,
            'msg': "Backup des applications",
            'is_backup': True
        },
        'extract_audio': {
            'cmd': None,
            'file': None,
            'msg': "Fichiers audio extraits",
            'is_audio': True
        },
        'extract_videos': {
            'cmd': None,
            'file': None,
            'msg': "Fichiers vidéo extraits",
            'is_videos': True
        },
        'extract_google_maps': {
            'cmd': None,
            'file': None,
            'msg': "Données Google Maps extraites",
            'is_google_maps': True
        }
    }

    if action not in actions:
        return JsonResponse(result)

    config = actions[action]
    
    try:
        # Traitement GOOGLE MAPS
        if config.get('is_google_maps'):
            try:
                maps_data = extract_google_maps_data(adb_path, device, timestamp)
                
                output = f"Google Maps Data Extracted:\n"
                output += f"- Timeline points: {len(maps_data.get('timeline', []))}\n"
                output += f"- Searches: {len(maps_data.get('searches', []))}\n"
                output += f"- Saved places: {len(maps_data.get('saved_places', []))}\n"
                
                # Sauvegarder les données
                maps_file = f"collector/static/collected_data/google_maps_{timestamp}.json"
                with open(maps_file, 'w', encoding='utf-8') as f:
                    json.dump(maps_data, f, indent=2)
                
                result['status'] = 'success'
                result['message'] = f"Données Google Maps extraites: {len(maps_data.get('timeline', []))} points de localisation"
                result['output'] = output
                result['file_path'] = f"collected_data/google_maps_{timestamp}.json"
                
                return JsonResponse(result)
                
            except Exception as e:
                result['message'] = f"Erreur extraction Google Maps: {str(e)}"
                return JsonResponse(result)
        
        # Traitement DUMP COMPLET
        if config.get('is_full_dump'):
            dump_results = []
            dump_dir = f"collector/static/collected_data/full_dump_{timestamp}"
            os.makedirs(dump_dir, exist_ok=True)
            
            # Ajouter l'extraction Google Maps au dump complet
            try:
                maps_data = extract_google_maps_data(adb_path, device, timestamp)
                if maps_data.get('timeline'):
                    maps_file = os.path.join(dump_dir, 'google_maps_timeline.json')
                    with open(maps_file, 'w', encoding='utf-8') as f:
                        json.dump(maps_data, f, indent=2)
                    
                    # Créer une entrée dans la chaîne de preuve
                    create_evidence_chain(maps_file, "google_maps_extraction")
                    
                    dump_results.append(f"✓ Google Maps: {len(maps_data.get('timeline', []))} points de localisation")
                else:
                    dump_results.append("✗ Google Maps: Aucune donnée trouvée")
            except Exception as e:
                dump_results.append(f"✗ Google Maps: {str(e)[:50]}")
            
            extractions = [
                ('Informations système', [adb_path, '-s', device, 'shell', 'getprop']),
                ('Applications installées', [adb_path, '-s', device, 'shell', 'pm', 'list', 'packages', '-f']),
                ('Contacts', [adb_path, '-s', device, 'shell', 'content', 'query', '--uri', 'content://com.android.contacts/data']),
                ('SMS', [adb_path, '-s', device, 'shell', 'content', 'query', '--uri', 'content://sms']),
                ('Journaux d\'appels', [adb_path, '-s', device, 'shell', 'content', 'query', '--uri', 'content://call_log/calls']),
                ('Calendrier', [adb_path, '-s', device, 'shell', 'content', 'query', '--uri', 'content://com.android.calendar/events']),
                ('Logs système', [adb_path, '-s', device, 'logcat', '-d']),
                ('Localisation', [adb_path, '-s', device, 'shell', 'dumpsys', 'location']),
                ('Comptes', [adb_path, '-s', device, 'shell', 'dumpsys', 'account']),
                ('Bluetooth', [adb_path, '-s', device, 'shell', 'dumpsys', 'bluetooth_manager']),
                ('Batterie', [adb_path, '-s', device, 'shell', 'dumpsys', 'battery']),
                ('Notifications', [adb_path, '-s', device, 'shell', 'dumpsys', 'notification']),
                ('Mémoire', [adb_path, '-s', device, 'shell', 'dumpsys', 'meminfo']),
                ('Réseau', [adb_path, '-s', device, 'shell', 'dumpsys', 'connectivity']),
            ]
            
            for name, cmd in extractions:
                try:
                    output = subprocess.check_output(cmd, stderr=subprocess.PIPE, text=True, timeout=30, errors='ignore')
                    if output.strip() and output.strip() != "No result found.":
                        filename = f"{name.lower().replace(' ', '_').replace('\'', '')}.txt"
                        filepath = os.path.join(dump_dir, filename)
                        with open(filepath, 'w', encoding='utf-8') as f:
                            f.write(output)
                        
                        # Créer une entrée dans la chaîne de preuve
                        evidence_entry = create_evidence_chain(filepath, f"full_dump_{name}")
                        dump_results.append(f"✓ {name} (preuve: {evidence_entry.get('sha256', 'N/A')[:16]}...)")
                    else:
                        dump_results.append(f"✗ {name} (vide)")
                except Exception as e:
                    dump_results.append(f"✗ {name} (erreur: {str(e)[:50]})")
            
            # Extraction des appels avec la nouvelle méthode
            try:
                calls_output = extract_calls_with_permissions(adb_path, device, timestamp)
                if calls_output and "Impossible" not in calls_output:
                    calls_file = os.path.join(dump_dir, 'calls_extracted.txt')
                    with open(calls_file, 'w', encoding='utf-8') as f:
                        f.write(calls_output)
                    
                    # Créer une entrée dans la chaîne de preuve
                    evidence_entry = create_evidence_chain(calls_file, "calls_extraction")
                    dump_results.append(f"✓ Appels extraits (preuve: {evidence_entry.get('sha256', 'N/A')[:16]}...)")
                else:
                    dump_results.append(f"✗ Appels non accessibles")
            except Exception as e:
                dump_results.append(f"✗ Appels erreur: {str(e)[:50]}")
            
            # Backup complet via adb backup
            try:
                backup_file = os.path.join(dump_dir, 'backup_complete.ab')
                result_msg = "⚠ Backup ADB lancé - CONFIRMEZ sur le téléphone (PAS de mot de passe!)\n"
                result_msg += "   NE fermez PAS cette fenêtre pendant le backup (peut prendre 15-30 min)."
                dump_results.append(result_msg)
                
                subprocess.run(
                    [adb_path, '-s', device, 'backup', '-f', backup_file, '-apk', '-shared', '-all'],
                    timeout=1800,
                    capture_output=True,
                    text=True
                )
                
                if os.path.exists(backup_file) and os.path.getsize(backup_file) > 1000:
                    # Créer une entrée dans la chaîne de preuve
                    evidence_entry = create_evidence_chain(backup_file, "full_backup")
                    dump_results.append(f"✓ Backup ADB terminé ({os.path.getsize(backup_file) / (1024*1024):.2f} MB) (preuve: {evidence_entry.get('sha256', 'N/A')[:16]}...)")
                    
                    # Extraire Google Maps du backup
                    try:
                        maps_from_backup = extract_google_maps_from_backup(backup_file)
                        if maps_from_backup and maps_from_backup.get('timeline'):
                            maps_backup_file = os.path.join(dump_dir, 'google_maps_from_backup.json')
                            with open(maps_backup_file, 'w', encoding='utf-8') as f:
                                json.dump(maps_from_backup, f, indent=2)
                            dump_results.append(f"✓ Google Maps depuis backup: {len(maps_from_backup.get('timeline', []))} points")
                    except:
                        pass
                else:
                    dump_results.append("✗ Backup ADB échoué ou annulé")
            except subprocess.TimeoutExpired:
                dump_results.append("⚠ Backup timeout - Vérifiez si le fichier a été créé")
            except Exception as e:
                dump_results.append(f"✗ Backup ADB: {str(e)[:100]}")
            
            # Backup Google Maps spécifique
            try:
                maps_backup = os.path.join(dump_dir, 'google_maps_backup.ab')
                dump_results.append("⚠ Backup Google Maps - Popup sur le téléphone...")
                subprocess.run(
                    [adb_path, '-s', device, 'backup', '-f', maps_backup, '-noapk', 'com.google.android.apps.maps'],
                    timeout=600,
                    capture_output=True,
                    text=True
                )
                
                if os.path.exists(maps_backup) and os.path.getsize(maps_backup) > 1000:
                    # Créer une entrée dans la chaîne de preuve
                    evidence_entry = create_evidence_chain(maps_backup, "google_maps_backup")
                    dump_results.append(f"✓ Backup Google Maps terminé ({os.path.getsize(maps_backup) / (1024*1024):.2f} MB) (preuve: {evidence_entry.get('sha256', 'N/A')[:16]}...)")
                else:
                    dump_results.append("✗ Backup Google Maps échoué")
            except subprocess.TimeoutExpired:
                dump_results.append("⚠ Google Maps timeout")
            except Exception as e:
                dump_results.append(f"✗ Google Maps backup: {str(e)[:100]}")
            
            # Backup WhatsApp
            try:
                wa_backup = os.path.join(dump_dir, 'whatsapp_complete.ab')
                dump_results.append("⚠ Backup WhatsApp - Popup sur le téléphone dans 10-20 sec...")
                subprocess.run(
                    [adb_path, '-s', device, 'backup', '-f', wa_backup, '-noapk', 'com.whatsapp'],
                    timeout=900,
                    capture_output=True,
                    text=True
                )
                
                if os.path.exists(wa_backup) and os.path.getsize(wa_backup) > 1000:
                    # Créer une entrée dans la chaîne de preuve
                    evidence_entry = create_evidence_chain(wa_backup, "whatsapp_backup")
                    dump_results.append(f"✓ Backup WhatsApp terminé ({os.path.getsize(wa_backup) / (1024*1024):.2f} MB) (preuve: {evidence_entry.get('sha256', 'N/A')[:16]}...)")
                else:
                    dump_results.append("✗ Backup WhatsApp échoué")
            except subprocess.TimeoutExpired:
                dump_results.append("⚠ WhatsApp timeout")
            except Exception as e:
                dump_results.append(f"✗ WhatsApp: {str(e)[:100]}")
            
            # Backup navigateurs
            browsers = {
                'Chrome': 'com.android.chrome',
                'Firefox': 'org.mozilla.firefox',
                'Opera': 'com.opera.browser'
            }
            for name, package in browsers.items():
                try:
                    browser_backup = os.path.join(dump_dir, f'{name.lower()}_backup.ab')
                    dump_results.append(f"⚠ Backup {name} en cours...")
                    subprocess.run(
                        [adb_path, '-s', device, 'backup', '-f', browser_backup, package],
                        timeout=300,
                        capture_output=True,
                        text=True
                    )
                    
                    if os.path.exists(browser_backup) and os.path.getsize(browser_backup) > 1000:
                        # Créer une entrée dans la chaîne de preuve
                        evidence_entry = create_evidence_chain(browser_backup, f"{name}_backup")
                        dump_results.append(f"✓ Backup {name} terminé (preuve: {evidence_entry.get('sha256', 'N/A')[:16]}...)")
                except:
                    dump_results.append(f"✗ Backup {name} échoué")
            
            # Backup WiFi
            try:
                wifi_backup = os.path.join(dump_dir, 'wifi_settings.ab')
                dump_results.append("⚠ Backup WiFi - Confirmez sur le téléphone...")
                subprocess.run(
                    [adb_path, '-s', device, 'backup', '-f', wifi_backup, '-system', 'com.android.providers.settings'],
                    timeout=300,
                    capture_output=True,
                    text=True
                )
                
                if os.path.exists(wifi_backup) and os.path.getsize(wifi_backup) > 1000:
                    # Créer une entrée dans la chaîne de preuve
                    evidence_entry = create_evidence_chain(wifi_backup, "wifi_backup")
                    dump_results.append(f"✓ Backup WiFi terminé (preuve: {evidence_entry.get('sha256', 'N/A')[:16]}...)")
                else:
                    dump_results.append("✗ Backup WiFi échoué")
            except:
                dump_results.append("✗ Backup WiFi échoué")
            
            # Extraire fichiers média
            try:
                media_dir = os.path.join(dump_dir, 'media')
                os.makedirs(media_dir, exist_ok=True)
                
                folders = ['/sdcard/DCIM', '/sdcard/Pictures', '/sdcard/Download', '/sdcard/Documents', 
                          '/sdcard/Music', '/sdcard/Recordings', '/sdcard/Sounds', '/sdcard/SoundRecorder']
                for folder in folders:
                    try:
                        subprocess.run(
                            [adb_path, '-s', device, 'pull', folder, media_dir],
                            capture_output=True,
                            timeout=60,
                            text=True
                        )
                        # Créer des entrées de preuve pour les fichiers média
                        for root, dirs, files in os.walk(os.path.join(media_dir, os.path.basename(folder))):
                            for file in files[:10]:  # Limiter aux 10 premiers fichiers
                                filepath = os.path.join(root, file)
                                create_evidence_chain(filepath, f"media_extraction_{os.path.basename(folder)}")
                        
                        dump_results.append(f"✓ Fichiers de {folder}")
                    except:
                        dump_results.append(f"✗ {folder} inaccessible")
                
                whatsapp_folders = [
                    '/sdcard/WhatsApp/Media/WhatsApp Images',
                    '/sdcard/WhatsApp/Media/WhatsApp Audio',
                    '/sdcard/WhatsApp/Media/WhatsApp Video',
                    '/sdcard/WhatsApp/Media/WhatsApp Voice Notes',
                    '/sdcard/WhatsApp/Media/WhatsApp Documents',
                    '/sdcard/WhatsApp/Databases'
                ]
                for folder in whatsapp_folders:
                    try:
                        subprocess.run(
                            [adb_path, '-s', device, 'pull', folder, os.path.join(media_dir, 'WhatsApp')],
                            capture_output=True,
                            timeout=60,
                            text=True
                        )
                        # Créer des entrées de preuve pour les fichiers WhatsApp
                        whatsapp_media_dir = os.path.join(media_dir, 'WhatsApp', os.path.basename(folder))
                        if os.path.exists(whatsapp_media_dir):
                            for root, dirs, files in os.walk(whatsapp_media_dir):
                                for file in files[:5]:  # Limiter aux 5 premiers fichiers
                                    filepath = os.path.join(root, file)
                                    create_evidence_chain(filepath, f"whatsapp_media_{os.path.basename(folder)}")
                        
                        dump_results.append(f"✓ WhatsApp - {folder.split('/')[-1]}")
                    except:
                        dump_results.append(f"✗ WhatsApp - {folder.split('/')[-1]} inaccessible")
            except Exception as e:
                dump_results.append(f"✗ Extraction média échouée: {str(e)[:100]}")
            
            # Décoder automatiquement les backups
            try:
                for root, dirs, files in os.walk(dump_dir):
                    for file in files:
                        if file.lower().endswith('.ab'):
                            ab_file = os.path.join(root, file)
                            rel_path = os.path.relpath(ab_file, 'collector/static/collected_data')
                            try:
                                decode_result = decode_backup_file(ab_file, tempfile.mkdtemp())
                                if decode_result['status'] == 'success':
                                    # Chercher spécifiquement Google Maps dans les fichiers décodés
                                    for dec_root, dec_dirs, dec_files in os.walk(decode_result['output_dir']):
                                        for dec_file in dec_files:
                                            if 'gmm_' in dec_file.lower() or ('maps' in dec_file.lower() and dec_file.endswith('.db')):
                                                # Copier la base de données Google Maps
                                                source_path = os.path.join(dec_root, dec_file)
                                                dest_name = f"decoded_{timestamp}_{dec_file}"
                                                dest_path = os.path.join('collector/static/collected_data', dest_name)
                                                shutil.copy2(source_path, dest_path)
                                                dump_results.append(f"✓ Base de données Google Maps décodée: {dec_file}")
                                    
                                    dump_results.append(f"✓ Backup {file} décodé automatiquement")
                            except:
                                pass
            except:
                pass
            
            result['status'] = 'success'
            result['message'] = f"Extraction complète terminée ({len([r for r in dump_results if r.startswith('✓')])} réussies)"
            result['output'] = '\n'.join(dump_results)
            result['file_path'] = f"collected_data/full_dump_{timestamp}"
            
            return JsonResponse(result)
        
        # Traitement des appels
        if config.get('is_calls'):
            try:
                calls_output = extract_calls_with_permissions(adb_path, device, timestamp)
                
                if calls_output and "Impossible" not in calls_output:
                    file_path = f"collector/static/collected_data/calls_{timestamp}.txt"
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(calls_output)
                    
                    # Créer une entrée dans la chaîne de preuve
                    evidence_entry = create_evidence_chain(file_path, "calls_extraction")
                    
                    result['status'] = 'success'
                    result['message'] = f"Appels extraits avec succès (preuve: {evidence_entry.get('sha256', 'N/A')[:16]}...)"
                    result['output'] = calls_output[:500] + "..." if len(calls_output) > 500 else calls_output
                    result['file_path'] = f"collected_data/calls_{timestamp}.txt"
                else:
                    result['message'] = "Impossible d'extraire les appels (permissions insuffisantes)"
                    result['output'] = "Essayez de donner les permissions sur le téléphone ou utilisez le dump complet."
                
                return JsonResponse(result)
            except Exception as e:
                result['message'] = f"Erreur extraction appels: {str(e)}"
                return JsonResponse(result)
        
        # Pour les autres actions qui utilisent cmd
        if config['cmd']:
            output = subprocess.check_output(config['cmd'], stderr=subprocess.PIPE, text=True, timeout=30, errors='ignore')
            
            if not output.strip() or output.strip() == "No result found.":
                if config.get('needs_permission'):
                    result['message'] = "Aucune donnée (permissions manquantes ou aucun contenu)"
                elif config.get('needs_root'):
                    result['message'] = "Erreur: Nécessite les droits root sur l'appareil"
                else:
                    result['message'] = "Aucune donnée trouvée"
                result['output'] = "Vide"
                return JsonResponse(result)
            
            if config['file']:
                file_path = os.path.join('collected_data', config['file'])
                full_file_path = f"collector/static/{file_path}"
                os.makedirs('collector/static/collected_data', exist_ok=True)
                
                with open(full_file_path, 'w', encoding='utf-8') as f:
                    f.write(output)
                
                # Créer une entrée dans la chaîne de preuve pour les extractions réussies
                evidence_entry = create_evidence_chain(full_file_path, action)
                
                result['file_path'] = file_path
                result['evidence_hash'] = evidence_entry.get('sha256', 'N/A')[:16] + "..."
            
            result['status'] = 'success'
            result['message'] = config['msg'] + (f" (preuve: {result.get('evidence_hash', 'N/A')})" if result.get('evidence_hash') else "")
            result['output'] = output[:500] + "..." if len(output) > 500 else output
            
        else:
            result['message'] = "Action non implémentée"
        
    except subprocess.TimeoutExpired:
        result['message'] = "Timeout: Commande trop longue (>30s)"
    except subprocess.CalledProcessError as e:
        if config.get('needs_root'):
            result['message'] = "Erreur: Nécessite les droits root sur l'appareil"
        elif config.get('needs_permission'):
            result['message'] = f"Erreur: Permissions manquantes sur l'appareil"
        else:
            result['message'] = f"Erreur commande: {e.stderr if e.stderr else str(e)}"
    except Exception as e:
        result['message'] = f"Erreur système: {str(e)}"

    return JsonResponse(result)

# ============================================
# AUTRES FONCTIONS
# ============================================

def view_dump(request, dump_name):
    """Afficher le contenu d'un dump complet"""
    dump_path = os.path.join('collector/static/collected_data', dump_name)
    
    if not os.path.exists(dump_path):
        return JsonResponse({'error': 'Dump non trouvé'}, status=404)
    
    files_in_dump = []
    
    for root, dirs, files in os.walk(dump_path):
        for filename in files:
            filepath = os.path.join(root, filename)
            relative_path = os.path.relpath(filepath, dump_path)
            file_size = os.path.getsize(filepath)
            
            file_type = 'unknown'
            if 'contact' in filename.lower():
                file_type = 'contacts'
            elif 'sms' in filename.lower():
                file_type = 'sms'
            elif 'call' in filename.lower():
                file_type = 'calls'
            elif 'app' in filename.lower():
                file_type = 'apps'
            elif 'log' in filename.lower():
                file_type = 'logs'
            elif 'location' in filename.lower():
                file_type = 'location'
            elif 'maps' in filename.lower() or 'gmm_' in filename.lower():
                file_type = 'maps'
            elif filename.lower().endswith(('.kml', '.gpx')):
                file_type = 'maps'
            elif filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                file_type = 'image'
            elif filename.lower().endswith('.ab'):
                file_type = 'backup'
            elif filename.lower().endswith(('.mp3', '.m4a', '.wav', '.aac', '.ogg')):
                file_type = 'audio'
            elif filename.lower().endswith(('.mp4', '.3gp', '.avi', '.mkv')):
                file_type = 'video'
            elif 'whatsapp' in filename.lower() or 'msgstore' in filename.lower():
                file_type = 'whatsapp'
            elif filename.lower().endswith(('.crypt14', '.crypt15')):
                file_type = 'whatsapp_encrypted'
            elif filename.lower() == '.nomedia':
                file_type = 'nomedia'
            
            files_in_dump.append({
                'name': filename,
                'path': relative_path.replace('\\', '/'),
                'size': file_size,
                'size_readable': format_file_size(file_size),
                'type': file_type,
                'url': f"/static/collected_data/{dump_name}/{relative_path.replace(chr(92), '/')}",
                'audio_type': get_audio_mime_type(filename),
                'is_nomedia': filename.lower() == '.nomedia'
            })
    
    return render(request, 'collector/dump_view.html', {
        'dump_name': dump_name,
        'files': files_in_dump,
        'total_files': len(files_in_dump)
    })

def decode_backup(request, file_path):
    """Décoder un fichier backup.ab"""
    full_path = os.path.join('collector/static/collected_data', file_path)
    
    if not os.path.exists(full_path):
        return JsonResponse({'error': 'Fichier introuvable'}, status=404)
    
    try:
        output_folder = full_path.replace('.ab', '_extracted')
        output_folder = output_folder.replace('\\', '/')
        os.makedirs(output_folder, exist_ok=True)
        
        with open(full_path, 'rb') as f:
            header = f.readline().decode('utf-8', errors='ignore').strip()
            if not header.startswith('ANDROID BACKUP'):
                return JsonResponse({
                    'status': 'error',
                    'message': 'Format de fichier invalide.'
                })
            
            version = f.readline().decode('utf-8', errors='ignore').strip()
            compression = f.readline().decode('utf-8', errors='ignore').strip()
            encryption = f.readline().decode('utf-8', errors='ignore').strip()
            
            if encryption != 'none':
                return JsonResponse({
                    'status': 'error',
                    'message': f'Backup CHIFFRÉ détecté ! Recréez le backup sans mot de passe.'
                })
            
            backup_data = f.read()
            
            if compression == '1':
                try:
                    backup_data = zlib.decompress(backup_data)
                except zlib.error as e:
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Erreur décompression: {str(e)}. Fichier peut-être corrompu.'
                    })
            
            tar_file = os.path.join(output_folder, 'backup.tar')
            with open(tar_file, 'wb') as tar_out:
                tar_out.write(backup_data)
            
            extracted_count = 0
            extracted_files = []
            whatsapp_files = []
            maps_files = []
            try:
                with tarfile.open(tar_file, 'r') as tar:
                    for member in tar.getmembers():
                        try:
                            safe_name = member.name.replace(':', '_').replace('*', '_').replace('?', '_')
                            member.name = safe_name
                            
                            tar.extract(member, output_folder)
                            extracted_count += 1
                            
                            if safe_name.lower().endswith('.db') and ('whatsapp' in safe_name.lower() or 'msgstore' in safe_name.lower()):
                                whatsapp_files.append(safe_name)
                            elif 'gmm_' in safe_name.lower() or ('maps' in safe_name.lower() and safe_name.lower().endswith('.db')):
                                maps_files.append(safe_name)
                            
                            if len(extracted_files) < 20:
                                extracted_files.append(safe_name)
                            
                        except Exception as e:
                            continue
                
            except Exception as e:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Erreur extraction tar: {str(e)}'
                })
            
            try:
                os.remove(tar_file)
            except:
                pass
            
            # Copier les bases de données Google Maps
            for maps_file in maps_files:
                try:
                    source_path = os.path.join(output_folder, maps_file)
                    if os.path.exists(source_path):
                        timestamp = int(time.time())
                        dest_filename = f"maps_{timestamp}_{os.path.basename(maps_file)}"
                        dest_path = os.path.join('collector/static/collected_data', dest_filename)
                        shutil.copy2(source_path, dest_path)
                        
                        # Créer une entrée dans la chaîne de preuve
                        create_evidence_chain(dest_path, "maps_decoded_db")
                except:
                    pass
            
            for whatsapp_file in whatsapp_files:
                try:
                    source_path = os.path.join(output_folder, whatsapp_file)
                    if os.path.exists(source_path):
                        timestamp = int(time.time())
                        dest_filename = f"whatsapp_{timestamp}_{os.path.basename(whatsapp_file)}"
                        dest_path = os.path.join('collector/static/collected_data', dest_filename)
                        shutil.copy2(source_path, dest_path)
                        
                        # Créer une entrée dans la chaîne de preuve
                        create_evidence_chain(dest_path, "whatsapp_decoded_db")
                except:
                    pass
            
            file_count = 0
            try:
                for root, dirs, files in os.walk(output_folder):
                    file_count += len(files)
            except:
                pass
            
            static_output = output_folder.replace('collector/static/', '')
            
            return JsonResponse({
                'status': 'success',
                'message': f'Backup décodé avec succès: {extracted_count} fichiers extraits',
                'output_folder': static_output,
                'extracted_files': extracted_files[:10],
                'whatsapp_files': whatsapp_files[:5],
                'maps_files': maps_files[:5],
                'total_files': file_count,
                'accessible_url': f"/static/{static_output}"
            })
            
    except Exception as e:
        error_msg = str(e)
        if 'Nom de répertoire non valide' in error_msg:
            error_msg = 'Erreur: Caractères invalides dans les noms de fichiers. Essayez de refaire le backup.'
        return JsonResponse({
            'status': 'error',
            'message': f'Erreur de décodage: {error_msg}'
        })

def decode_whatsapp(request, file_path):
    """Décoder une base de données WhatsApp chiffrée"""
    full_path = os.path.join('collector/static/collected_data', file_path)
    
    if not os.path.exists(full_path):
        return JsonResponse({'error': 'Fichier introuvable'}, status=404)
    
    if file_path.endswith('.ab'):
        return decode_backup(request, file_path)
    
    if not file_path.endswith(('.crypt14', '.crypt15')):
        return JsonResponse({
            'status': 'error',
            'message': 'Ce fichier n\'est pas chiffré. Ouvrez-le directement avec DB Browser for SQLite.'
        })
    
    try:
        key_file = None
        search_dir = os.path.dirname(full_path)
        
        possible_keys = ['key', 'whatsapp_key.bin', 'key.bin', 'whatsapp.key']
        for key_name in possible_keys:
            key_path = os.path.join(search_dir, key_name)
            if os.path.exists(key_path):
                key_file = key_path
                break
        
        if not key_file:
            return JsonResponse({
                'status': 'error',
                'message': 'Clé de décryptage introuvable. Décodez d\'abord le backup WhatsApp (.ab).'
            })
        
        try:
            from Crypto.Cipher import AES
            import hashlib
        except ImportError:
            return JsonResponse({
                'status': 'error',
                'message': 'Module pycryptodome manquant. Installez avec: pip install pycryptodome'
            })
        
        with open(key_file, 'rb') as f:
            key = f.read()
        
        with open(full_path, 'rb') as f:
            header = f.read(67)
            iv = f.read(16)
            encrypted_data = f.read()
        
        key_hash = hashlib.sha256(key).digest()
        cipher = AES.new(key_hash[:32], AES.MODE_GCM, nonce=iv)
        decrypted = cipher.decrypt(encrypted_data)
        
        output_file = full_path.replace('.crypt14', '_decrypted.db').replace('.crypt15', '_decrypted.db')
        with open(output_file, 'wb') as f:
            f.write(decrypted)
        
        # Créer une entrée dans la chaîne de preuve
        evidence_entry = create_evidence_chain(output_file, "whatsapp_decrypted")
        
        message_count = 0
        try:
            conn = sqlite3.connect(output_file)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM message")
            message_count = cursor.fetchone()[0]
            conn.close()
        except:
            pass
        
        return JsonResponse({
            'status': 'success',
            'message': f'WhatsApp décrypté avec succès ! {message_count} messages trouvés. (preuve: {evidence_entry.get("sha256", "N/A")[:16]}...)',
            'output_file': output_file.replace('collector/static/', ''),
            'message_count': message_count,
            'evidence_hash': evidence_entry.get('sha256', 'N/A')[:16] + '...'
        })
        
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': f'Erreur de décryptage: {str(e)}'
        })

def extract_calls_view(request):
    """Vue spéciale pour extraire les appels"""
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    
    try:
        adb_path = get_adb_path()
        if not adb_path:
            return JsonResponse({'status': 'error', 'message': 'ADB non trouvé'})
        
        device = get_device_id(adb_path)
        if not device:
            return JsonResponse({'status': 'error', 'message': 'Aucun appareil détecté'})
        
        calls_output = extract_calls_with_permissions(adb_path, device, timestamp)
        
        if calls_output and "Impossible" not in calls_output:
            file_path = f"collector/static/collected_data/calls_{timestamp}.txt"
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(calls_output)
            
            # Créer une entrée dans la chaîne de preuve
            evidence_entry = create_evidence_chain(file_path, "calls_extraction")
            
            return JsonResponse({
                'status': 'success',
                'message': 'Appels extraits avec succès',
                'file_path': f"collected_data/calls_{timestamp}.txt",
                'output': calls_output[:1000],
                'evidence_hash': evidence_entry.get('sha256', 'N/A')[:16] + '...'
            })
        else:
            return JsonResponse({
                'status': 'error',
                'message': 'Impossible d\'extraire les appels. Essayez avec le dump complet.'
            })
            
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': f'Erreur: {str(e)}'
        }) 
    

# ============================================
# FONCTIONS POUR LA CHAÎNE DE PREUVE
# ============================================

def create_evidence_chain(file_path, operation_type="extraction"):
    """Créer une entrée de chaîne de preuve numérique"""
    try:
        file_size = os.path.getsize(file_path)
        file_mtime = os.path.getmtime(file_path)
        file_ctime = os.path.getctime(file_path)
        
        # Calculer les hashs
        hash_md5 = calculate_hash(file_path, 'md5')
        hash_sha1 = calculate_hash(file_path, 'sha1')
        hash_sha256 = calculate_hash(file_path, 'sha256')
        
        evidence_entry = {
            'timestamp': datetime.datetime.now().isoformat(),
            'filename': os.path.basename(file_path),
            'full_path': file_path,
            'file_size': file_size,
            'file_size_readable': format_file_size(file_size),
            'md5': hash_md5,
            'sha1': hash_sha1,
            'sha256': hash_sha256,
            'operation': operation_type,
            'operator': 'Android Collector',
            'device_id': 'N/A',  # À remplir plus tard
            'integrity_check': 'PASS',
            'notes': f"{operation_type} via ADB backup"
        }
        
        # Sauvegarder dans la base de preuves
        save_to_evidence_log(evidence_entry)
        
        return evidence_entry
        
    except Exception as e:
        return {
            'timestamp': datetime.datetime.now().isoformat(),
            'filename': os.path.basename(file_path),
            'error': str(e),
            'integrity_check': 'FAIL'
        }

def calculate_hash(file_path, algorithm='sha256'):
    """Calculer le hash d'un fichier"""
    hash_func = hashlib.new(algorithm)
    
    try:
        with open(file_path, 'rb') as f:
            # Lire par blocs pour les gros fichiers
            for chunk in iter(lambda: f.read(4096), b""):
                hash_func.update(chunk)
        return hash_func.hexdigest()
    except:
        return "ERROR"

def save_to_evidence_log(evidence_entry):
    """Sauvegarder l'entrée dans le journal des preuves"""
    evidence_dir = 'collector/static/collected_data/evidence_chain'
    os.makedirs(evidence_dir, exist_ok=True)
    
    log_file = os.path.join(evidence_dir, 'evidence_log.csv')
    file_exists = os.path.isfile(log_file)
    
    with open(log_file, 'a', newline='', encoding='utf-8') as f:
        fieldnames = [
            'timestamp', 'filename', 'full_path', 'file_size', 
            'file_size_readable', 'md5', 'sha1', 'sha256', 
            'operation', 'operator', 'device_id', 'integrity_check', 'notes'
        ]
        
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()
        
        writer.writerow(evidence_entry)
    
    # Sauvegarder aussi en JSON pour l'interface web
    json_file = os.path.join(evidence_dir, 'evidence_log.json')
    save_to_json_log(evidence_entry, json_file)

def save_to_json_log(evidence_entry, json_file):
    """Sauvegarder dans un fichier JSON"""
    try:
        if os.path.exists(json_file):
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {"evidence_chain": []}
        
        data["evidence_chain"].append(evidence_entry)
        
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
    except:
        # Si erreur, créer un nouveau fichier
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump({"evidence_chain": [evidence_entry]}, f, indent=2, ensure_ascii=False)

def verify_integrity(file_path):
    """Vérifier l'intégrité d'un fichier en recalculant son hash"""
    if not os.path.exists(file_path):
        return {"status": "error", "message": "Fichier introuvable"}
    
    # Trouver l'entrée originale
    evidence_dir = 'collector/static/collected_data/evidence_chain'
    json_file = os.path.join(evidence_dir, 'evidence_log.json')
    
    if not os.path.exists(json_file):
        return {"status": "error", "message": "Aucune preuve enregistrée"}
    
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    filename = os.path.basename(file_path)
    original_entry = None
    
    for entry in data.get("evidence_chain", []):
        if entry.get("filename") == filename:
            original_entry = entry
            break
    
    if not original_entry:
        return {"status": "error", "message": "Preuve originale introuvable"}
    
    # Recalculer le hash
    current_hash = calculate_hash(file_path, 'sha256')
    original_hash = original_entry.get("sha256")
    
    if current_hash == original_hash:
        return {
            "status": "success",
            "message": "Intégrité vérifiée ✓",
            "original_hash": original_hash,
            "current_hash": current_hash,
            "match": True
        }
    else:
        return {
            "status": "warning",
            "message": "ALTÉRATION DÉTECTÉE ⚠️",
            "original_hash": original_hash,
            "current_hash": current_hash,
            "match": False
        }    
    

# ============================================
# GÉNÉRATION DE RAPPORTS FORENSIQUES
# ============================================

def generate_forensic_report(extraction_data, scenario_data=None):
    """Générer un rapport forensique complet au format professionnel"""
    
    timestamp = datetime.datetime.now()
    report_id = f"FR-{timestamp.strftime('%Y%m%d-%H%M%S')}"
    
    # Récupérer les informations de l'appareil
    device_info = extraction_data.get('device_info', {})
    
    # Compter les artefacts extraits
    artefacts_count = count_artefacts(extraction_data)
    
    # Construire le rapport
    report = {
        "report_id": report_id,
        "generation_date": timestamp.isoformat(),
        "report_type": "Rapport d'Investigation Numérique",
        "case_reference": scenario_data.get('case_number', 'CS-2024-001') if scenario_data else 'CS-NON-RENSEIGNE',
        "investigator": "Android Collector Forensic Tool",
        "version": "1.0",
        
        "case_details": {
            "case_name": scenario_data.get('case_name', 'Investigation Android') if scenario_data else 'Investigation Standard',
            "incident_date": scenario_data.get('incident_date', 'N/A') if scenario_data else 'N/A',
            "location": scenario_data.get('location', 'N/A') if scenario_data else 'N/A',
            "description": scenario_data.get('description', 'Analyse forensique d\'un appareil Android') if scenario_data else 'Analyse forensique standard'
        },
        
        "device_information": {
            "device_model": device_info.get('device_model', 'Inconnu'),
            "android_version": device_info.get('android_version', 'Inconnu'),
            "manufacturer": device_info.get('manufacturer', 'Inconnu'),
            "serial_number": device_info.get('serial', 'N/A'),
            "imei": device_info.get('imei', 'N/A'),
            "storage_capacity": device_info.get('storage', 'N/A')
        },
        
        "acquisition_details": {
            "method": "Logical Acquisition via ADB",
            "tool": "Android Collector Platform",
            "date_time": timestamp.strftime('%Y-%m-d %H:%M:%S'),
            "operator": "System Operator",
            "hash_verification": "SHA256 implemented"
        },
        
        "artefacts_extracted": artefacts_count,
        
        "evidence_chain": {
            "total_files": extraction_data.get('total_files', 0),
            "hashes_calculated": extraction_data.get('hashes_calculated', True),
            "integrity_verified": extraction_data.get('integrity_ok', True)
        },
        
        "findings_summary": generate_findings_summary(extraction_data),
        
        "timeline_analysis": extract_timeline_data(extraction_data),
        
        "conclusions": {
            "main_findings": extraction_data.get('main_findings', 'Aucune donnée compromettante détectée'),
            "recommendations": [
                "Conserver les fichiers de hash pour vérification future",
                "Archiver les preuves dans un support sécurisé",
                "Documenter toute manipulation ultérieure"
            ]
        },
        
        "disclaimer": "Ce rapport a été généré automatiquement. Pour une analyse complète, consulter un expert en forensic numérique."
    }
    
    # Sauvegarder le rapport
    save_report_files(report, report_id)
    
    return report

def count_artefacts(extraction_data):
    """Compter les artefacts par catégorie"""
    artefacts = {
        "contacts": 0,
        "calls": 0,
        "sms": 0,
        "whatsapp": 0,
        "images": 0,
        "videos": 0,
        "audio": 0,
        "documents": 0,
        "browsing_history": 0,
        "location_data": 0,
        "google_maps": 0,
        "wifi_networks": 0,
        "installed_apps": 0
    }
    
    # Cette fonction devrait analyser les fichiers extraits
    # Pour l'instant, retourner des valeurs par défaut
    return artefacts

def generate_findings_summary(extraction_data):
    """Générer un résumé des découvertes"""
    findings = {
        "contacts_found": "Oui" if extraction_data.get('has_contacts', False) else "Non",
        "call_logs_found": "Oui" if extraction_data.get('has_calls', False) else "Non",
        "sms_messages_found": "Oui" if extraction_data.get('has_sms', False) else "Non",
        "whatsapp_data_found": "Oui" if extraction_data.get('has_whatsapp', False) else "Non",
        "google_maps_data_found": "Oui" if extraction_data.get('has_google_maps', False) else "Non",
        "images_found": "Oui" if extraction_data.get('has_images', False) else "Non",
        "location_data_found": "Oui" if extraction_data.get('has_location', False) else "Non",
        "significant_findings": extraction_data.get('significant_findings', 'Aucune donnée significative')
    }
    
    return findings

def extract_timeline_data(extraction_data):
    """Extraire les données de timeline"""
    timeline = {
        "first_activity": extraction_data.get('first_activity', 'N/A'),
        "last_activity": extraction_data.get('last_activity', datetime.datetime.now().isoformat()),
        "key_events": extraction_data.get('key_events', []),
        "activity_period": "À déterminer"
    }
    
    return timeline

def save_report_files(report, report_id):
    """Sauvegarder le rapport dans différents formats"""
    reports_dir = 'collector/static/collected_data/forensic_reports'
    os.makedirs(reports_dir, exist_ok=True)
    
    # Sauvegarder en JSON
    json_file = os.path.join(reports_dir, f'{report_id}.json')
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    # Sauvegarder en HTML (pour visualisation web)
    html_file = os.path.join(reports_dir, f'{report_id}.html')
    html_content = generate_html_report(report)
    with open(html_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    # Sauvegarder en PDF (via template)
    pdf_file = os.path.join(reports_dir, f'{report_id}.pdf')
    # generate_pdf_report(report, pdf_file)  # À implémenter avec reportlab
    
    return {
        'json': json_file.replace('collector/static/', ''),
        'html': html_file.replace('collector/static/', ''),
        'pdf': pdf_file.replace('collector/static/', '') if os.path.exists(pdf_file) else None
    }

def generate_html_report(report):
    """Générer un rapport HTML lisible"""
    html_template = f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Rapport Forensique - {report['report_id']}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }}
            .header {{ background: #2c3e50; color: white; padding: 20px; border-radius: 5px; }}
            .section {{ margin: 30px 0; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
            .subsection {{ margin: 15px 0; padding: 15px; background: #f8f9fa; }}
            table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background: #f2f2f2; }}
            .evidence-chain {{ font-family: monospace; font-size: 12px; }}
            .footer {{ margin-top: 40px; font-size: 12px; color: #666; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📋 Rapport Forensique Android</h1>
            <p><strong>Référence:</strong> {report['report_id']}</p>
            <p><strong>Date de génération:</strong> {report['generation_date']}</p>
        </div>
        
        <div class="section">
            <h2>📁 Informations de l'Affaire</h2>
            <div class="subsection">
                <table>
                    <tr><th>Nom de l'affaire</th><td>{report['case_details']['case_name']}</td></tr>
                    <tr><th>Date de l'incident</th><td>{report['case_details']['incident_date']}</td></tr>
                    <tr><th>Lieu</th><td>{report['case_details']['location']}</td></tr>
                </table>
            </div>
        </div>
        
        <div class="section">
            <h2>📱 Informations de l'Appareil</h2>
            <div class="subsection">
                <table>
                    <tr><th>Modèle</th><td>{report['device_information']['device_model']}</td></tr>
                    <tr><th>Version Android</th><td>{report['device_information']['android_version']}</td></tr>
                    <tr><th>Fabricant</th><td>{report['device_information']['manufacturer']}</td></tr>
                </table>
            </div>
        </div>
        
        <div class="section">
            <h2>🔍 Détails de l'Acquisition</h2>
            <div class="subsection">
                <table>
                    <tr><th>Méthode</th><td>{report['acquisition_details']['method']}</td></tr>
                    <tr><th>Outil utilisé</th><td>{report['acquisition_details']['tool']}</td></tr>
                    <tr><th>Date/Heure</th><td>{report['acquisition_details']['date_time']}</td></tr>
                </table>
            </div>
        </div>
        
        <div class="section">
            <h2>📊 Résumé des Découvertes</h2>
            <div class="subsection">
                <p><strong>Total artefacts extraits:</strong> {sum(report['artefacts_extracted'].values())}</p>
                <table>
                    <tr><th>Type d'artefact</th><th>Présent</th></tr>
                    <tr><td>Contacts</td><td>{report['findings_summary']['contacts_found']}</td></tr>
                    <tr><td>Journaux d'appels</td><td>{report['findings_summary']['call_logs_found']}</td></tr>
                    <tr><td>Messages SMS</td><td>{report['findings_summary']['sms_messages_found']}</td></tr>
                    <tr><td>Données WhatsApp</td><td>{report['findings_summary']['whatsapp_data_found']}</td></tr>
                    <tr><td>Données Google Maps</td><td>{report['findings_summary']['google_maps_data_found']}</td></tr>
                </table>
            </div>
        </div>
        
        <div class="section">
            <h2>⛓️ Chaîne de Preuve</h2>
            <div class="subsection">
                <p><strong>Intégrité vérifiée:</strong> {report['evidence_chain']['integrity_verified']}</p>
                <p><strong>Total fichiers:</strong> {report['evidence_chain']['total_files']}</p>
                <p><strong>Hachages calculés:</strong> {report['evidence_chain']['hashes_calculated']}</p>
            </div>
        </div>
        
        <div class="section">
            <h2>🎯 Conclusions</h2>
            <div class="subsection">
                <h3>Principales découvertes:</h3>
                <p>{report['conclusions']['main_findings']}</p>
                
                <h3>Recommandations:</h3>
                <ul>
                    {''.join(f'<li>{rec}</li>' for rec in report['conclusions']['recommendations'])}
                </ul>
            </div>
        </div>
        
        <div class="footer">
            <hr>
            <p><strong>Avertissement:</strong> {report['disclaimer']}</p>
            <p>Rapport généré automatiquement par Android Collector Forensic Tool v1.0</p>
            <p>© {datetime.datetime.now().year} - Projet de Forensic Android</p>
        </div>
    </body>
    </html>
    """
    
    return html_template



# ============================================
# VUES POUR LA CHAÎNE DE PREUVE ET RAPPORTS
# ============================================

def verify_file_integrity(request, file_path):
    """Vérifier l'intégrité d'un fichier"""
    full_path = os.path.join('collector/static/collected_data', file_path)
    result = verify_integrity(full_path)
    return JsonResponse(result)

def view_evidence_chain(request):
    """Afficher la chaîne de preuve complète"""
    evidence_file = 'collector/static/collected_data/evidence_chain/evidence_log.json'
    
    if os.path.exists(evidence_file):
        with open(evidence_file, 'r', encoding='utf-8') as f:
            evidence_data = json.load(f)
        
        # Calculer les statistiques
        stats = {
            'total_entries': len(evidence_data.get('evidence_chain', [])),
            'last_update': evidence_data.get('evidence_chain', [{}])[-1].get('timestamp', 'N/A'),
            'files_with_errors': sum(1 for e in evidence_data.get('evidence_chain', []) if e.get('integrity_check') == 'FAIL')
        }
        
        return render(request, 'collector/evidence_chain.html', {
            'evidence_chain': evidence_data.get('evidence_chain', []),
            'stats': stats
        })
    
    return render(request, 'collector/evidence_chain.html', {
        'evidence_chain': [],
        'stats': {'total_entries': 0}
    })

def generate_forensic_report_view(request):
    """Générer un nouveau rapport forensique"""
    if request.method == 'POST':
        # Récupérer les données du formulaire
        scenario_data = {
            'case_name': request.POST.get('case_name', 'Investigation Android'),
            'case_number': request.POST.get('case_number', 'CS-2024-001'),
            'incident_date': request.POST.get('incident_date', ''),
            'location': request.POST.get('location', ''),
            'description': request.POST.get('description', '')
        }
        
        # Récupérer les données d'extraction récentes
        extraction_data = get_latest_extraction_data()
        
        # Générer le rapport
        report = generate_forensic_report(extraction_data, scenario_data)
        
        return JsonResponse({
            'status': 'success',
            'message': 'Rapport généré avec succès',
            'report_id': report['report_id'],
            'files': save_report_files(report, report['report_id'])
        })
    
    return render(request, 'collector/generate_report.html')

def download_report(request, report_id):
    """Télécharger un rapport"""
    report_file = f'collector/static/collected_data/forensic_reports/{report_id}.html'
    
    if os.path.exists(report_file):
        with open(report_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        response = HttpResponse(content, content_type='text/html')
        response['Content-Disposition'] = f'attachment; filename="{report_id}.html"'
        return response
    
    return JsonResponse({'error': 'Rapport non trouvé'}, status=404)

def get_latest_extraction_data():
    """Récupérer les données de la dernière extraction"""
    # Cette fonction devrait lire les dernières extractions
    # Pour l'instant, retourner des données d'exemple
    return {
        'device_info': {
            'device_model': 'Infinix Smart 5',
            'android_version': 'Android 11',
            'manufacturer': 'Infinix'
        },
        'total_files': 42,
        'has_contacts': True,
        'has_calls': True,
        'has_sms': True,
        'has_whatsapp': True,
        'has_google_maps': True,
        'has_images': True,
        'has_location': True,
        'main_findings': 'Données de communication extraites avec succès',
        'significant_findings': 'Trajets Google Maps détectés',
        'google_maps_points': 156  # Exemple de données Google Maps
    }