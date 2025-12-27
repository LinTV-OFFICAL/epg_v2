import os
import sys
import json
import requests
import gzip
import re
import argparse
import hashlib
import time
import signal
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from functools import partial
from collections import defaultdict
from contextlib import contextmanager

try:
    from lxml import etree
except ImportError:
    sys.exit("Ошибка: lxml не установлен. Установите: pip install lxml")

MAX_WORKERS = 50  
ICON_DOWNLOAD_WORKERS = 10  
REQUEST_TIMEOUT = 45  
ICON_TIMEOUT = 15  
MAX_ICON_DOWNLOAD_TIME = 1200  

SOURCES_FILE = 'sources.json'
DATA_DIR = Path('data')
ICONS_DIR = Path('icons')
ICONS_MAP_FILE = Path('icons_map.json')
README_FILE = 'README.md'
RAW_BASE_URL = "https://raw.githubusercontent.com/{owner}/{repo}/main/{filepath}"

class TimeoutHandler:
    """Обработчик таймаутов для предотвращения зависания"""
    def __init__(self):
        self.timeout_occurred = False
    
    def handler(self, signum, frame):
        self.timeout_occurred = True
        print(f"\n⚠️  Получен сигнал таймаута ({signum}). Завершаю текущую операцию...")

timeout_handler = TimeoutHandler()

@contextmanager
def timeout_context(seconds):
    """Контекст-менеджер для установки таймаута на операцию"""
    old_handler = signal.signal(signal.SIGALRM, timeout_handler.handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

def is_gzipped(file_path):
    """Проверяет, является ли файл gzipped."""
    try:
        with open(file_path, 'rb') as f:
            return f.read(2) == b'\x1f\x8b'
    except:
        return False

class CustomEncoder(json.JSONEncoder):
    """Класс для сериализации Path объектов и множеств в JSON."""
    def default(self, obj):
        if isinstance(obj, Path):
            return str(obj).replace('\\', '/') 
        if isinstance(obj, set):
            return list(obj)
        return json.JSONEncoder.default(self, obj)

def read_sources_and_notes():
    """Читает источники и заметки из sources.json."""
    try:
        with open(SOURCES_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            sources, notes = config.get('sources', []), config.get('notes', '')
            if not sources:
                sys.exit("Ошибка: в sources.json нет источников.")
            return sources, notes
    except Exception as e:
        sys.exit(f"Ошибка чтения {SOURCES_FILE}: {e}")

def clear_directory(dir_path: Path):
    """Очищает указанную директорию от файлов и поддиректорий."""
    if dir_path.exists():
        for item in dir_path.iterdir():
            try:
                if item.is_dir():
                    clear_directory(item)
                    item.rmdir()
                else:
                    item.unlink()
            except Exception as e:
                print(f"Предупреждение: не удалось удалить {item}: {e}")
    else:
        dir_path.mkdir(parents=True, exist_ok=True)

def download_one(entry):
    """Скачивает один EPG файл БЕЗ лишнего спама в логи."""
    url, desc = entry['url'], entry['desc']
    temp_path = DATA_DIR / ("tmp_" + os.urandom(4).hex())
    result = {'entry': entry, 'error': None}
    
    try:
        print(f"🔄 Загружаю: {desc}")
        # print(f"   URL: {url}") # Можно закомментировать, чтобы лог был еще чище
        
        with requests.get(url, stream=True, timeout=REQUEST_TIMEOUT) as r:
            r.raise_for_status()
            # Убрали total_size и downloaded, они здесь больше не нужны для принта
            
            with open(temp_path, 'wb') as f:
                for chunk in r.iter_content(32 * 1024):  
                    if timeout_handler.timeout_occurred:
                        raise TimeoutError("Операция прервана по таймауту")
                    
                    f.write(chunk)
                    # ЗДЕСЬ НЕ ДОЛЖНО БЫТЬ НИКАКИХ PRINT!
        
        # Файл закрыт, теперь проверяем размер
        size_bytes = temp_path.stat().st_size
        if size_bytes == 0:
            raise ValueError("Файл пустой.")
            
        size_mb = round(size_bytes / (1024 * 1024), 2)
        
        # Единственный принт по завершении загрузки
        print(f"✅ Загружено: {desc} ({size_mb} MB)")
        
        result.update({'size_mb': size_mb, 'temp_path': temp_path})
        return result
        
    except Exception as e:
        result['error'] = f"Ошибка загрузки: {e}"
        print(f"❌ Ошибка для {desc}: {result['error']}")
        if temp_path.exists():
            temp_path.unlink()
    return result

def download_icon_batch(session, batch_items):
    """Скачивает пакет иконок с прогрессом."""
    successful = 0
    for url, save_path in batch_items:
        if timeout_handler.timeout_occurred:
            break
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with session.get(url, stream=True, timeout=ICON_TIMEOUT) as r:
                r.raise_for_status()
                with open(save_path, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
            successful += 1
        except:
            continue
    return successful

def get_icon_signature_fast(file_path):
    """Быстрое создание сигнатуры EPG-файла на основе URL-ов иконок."""
    icon_urls = set()
    try:
        open_func = gzip.open if is_gzipped(file_path) else open
        with open_func(file_path, 'rb') as f:
            context = etree.iterparse(f, tag='icon', events=('end',))
            count = 0
            for _, element in context:
                if count > 10000:  
                    break
                if 'src' in element.attrib:
                    icon_urls.add(element.attrib['src'])
                element.clear()
                count += 1
                
                if count % 1000 == 0 and timeout_handler.timeout_occurred:
                    break
        
        if not icon_urls:
            return None
            
        sorted_urls = sorted(list(icon_urls))
        return hashlib.sha256(''.join(sorted_urls).encode('utf-8')).hexdigest()
        
    except Exception as e:
        print(f"⚠️  Ошибка при создании сигнатуры для {file_path.name}: {e}")
        return None

def perform_full_update(download_results):
    """Выполняет полное обновление с оптимизированной загрузкой иконок."""
    print("\n--- Этап 1: Группировка источников по наборам иконок ---")
    groups = defaultdict(list)
    
    for i, res in enumerate(download_results):
        if res.get('error'):
            continue
            
        print(f"🔍 Анализирую файл {i+1}/{len(download_results)}: {res['entry']['desc']}")
        signature = get_icon_signature_fast(res['temp_path'])
        groups[signature].append(res)
        
        if timeout_handler.timeout_occurred:
            print("⚠️  Операция прервана по таймауту на этапе группировки")
            break
    
    print(f"✅ Найдено {len(groups)} уникальных групп источников.")

    icon_data = {
        "icon_pool": {},
        "groups": {},
        "source_to_group": {}
    }
    all_unique_urls = set()

    print("\n--- Этап 2: Создание карт иконок ---")
    for i, (signature, sources_in_group) in enumerate(groups.items()):
        if timeout_handler.timeout_occurred:
            break
            
        print(f"📋 Обрабатываю группу {i+1}/{len(groups)}")
        
        if signature is None:
            for res in sources_in_group:
                icon_data["source_to_group"][res['entry']['url']] = None
            continue
        
        icon_map_for_group = {}
        representative_file = sources_in_group[0]['temp_path']
        
        try:
            open_func = gzip.open if is_gzipped(representative_file) else open
            with open_func(representative_file, 'rb') as f:
                context = etree.iterparse(f, tag='channel', events=('end',))
                count = 0
                for _, channel in context:
                    if count > 5000 or timeout_handler.timeout_occurred:
                        break
                    channel_id = channel.get('id')
                    icon_tag = channel.find('icon')
                    if channel_id and icon_tag is not None and 'src' in icon_tag.attrib:
                        icon_url = icon_tag.get('src')
                        icon_map_for_group[channel_id] = icon_url
                        all_unique_urls.add(icon_url)
                    channel.clear()
                    count += 1
        except Exception as e:
            print(f"⚠️  Ошибка парсинга файла {representative_file.name}: {e}")
            continue

        icon_data["groups"][signature] = {"icon_map": icon_map_for_group}
        for res in sources_in_group:
            icon_data["source_to_group"][res['entry']['url']] = signature
        
        print(f"   ✅ Группа обработана: {len(icon_map_for_group)} иконок")

    icon_pool_dir = ICONS_DIR / "pool"
    urls_to_download = {}
    
    print(f"\n--- Этап 2.1: Подготовка загрузки {len(all_unique_urls)} уникальных иконок ---")
    
    for url in all_unique_urls:
        url_hash = hashlib.sha1(url.encode('utf-8')).hexdigest()
        original_ext = "".join(Path(urlparse(url).path).suffixes) if Path(urlparse(url).path).suffixes else ".png"
        pool_path = icon_pool_dir / f"{url_hash}{original_ext}"
        
        icon_data["icon_pool"][url] = pool_path
        if not pool_path.exists():
            urls_to_download[url] = pool_path

    print(f"📥 Нужно скачать {len(urls_to_download)} новых иконок")

    if urls_to_download:
        items = list(urls_to_download.items())
        batch_size = 50
        batches = [items[i:i+batch_size] for i in range(0, len(items), batch_size)]
        
        total_downloaded = 0
        start_time = time.time()
        
        with timeout_context(MAX_ICON_DOWNLOAD_TIME):
            with requests.Session() as session:
                adapter = requests.adapters.HTTPAdapter(
                    pool_connections=ICON_DOWNLOAD_WORKERS, 
                    pool_maxsize=ICON_DOWNLOAD_WORKERS,
                    max_retries=1
                )
                session.mount('http://', adapter)
                session.mount('https://', adapter)
                
                with ThreadPoolExecutor(max_workers=ICON_DOWNLOAD_WORKERS) as executor:
                    future_to_batch = {
                        executor.submit(download_icon_batch, session, batch): i 
                        for i, batch in enumerate(batches)
                    }
                    
                    for future in as_completed(future_to_batch, timeout=MAX_ICON_DOWNLOAD_TIME):
                        if timeout_handler.timeout_occurred:
                            break
                        try:
                            batch_idx = future_to_batch[future]
                            successful = future.result(timeout=60)
                            total_downloaded += successful
                            
                            elapsed = time.time() - start_time
                            progress = ((batch_idx + 1) / len(batches)) * 100
                            print(f"   📊 Прогресс: {progress:.1f}% | Загружено: {total_downloaded} | Время: {elapsed:.0f}с")
                            
                        except TimeoutError:
                            print(f"   ⚠️  Таймаут пакета {batch_idx + 1}")
                        except Exception as e:
                            print(f"   ⚠️  Ошибка пакета: {e}")
        
        print(f"✅ Загрузка иконок завершена: {total_downloaded} из {len(urls_to_download)}")

    print(f"\n💾 Сохранение карты иконок...")
    try:
        with open(ICONS_MAP_FILE, 'w', encoding='utf-8') as f:
            json.dump(icon_data, f, ensure_ascii=False, indent=2, cls=CustomEncoder)
        print("✅ Карта иконок сохранена")
    except Exception as e:
        print(f"❌ Ошибка сохранения карты: {e}")
    
    return icon_data

def load_icon_data_for_daily_update():
    """Загружает существующую карту иконок для ежедневного обновления."""
    print("\n--- Этап 1: Загрузка существующей карты иконок ---")
    if not ICONS_MAP_FILE.is_file():
        print(f"📁 Файл {ICONS_MAP_FILE} не найден. Иконки не будут заменены.")
        print("💡 Рекомендуется запустить полное обновление для создания карты иконок.")
        return None
    try:
        with open(ICONS_MAP_FILE, 'r', encoding='utf-8') as f:
            icon_data = json.load(f)
        
        if 'icon_pool' in icon_data:
            icon_data['icon_pool'] = {k: Path(v) for k, v in icon_data['icon_pool'].items()}
        
        groups_count = len(icon_data.get('groups', {}))
        pool_count = len(icon_data.get('icon_pool', {}))
        print(f"✅ Карта иконок загружена: {groups_count} групп, {pool_count} иконок в пуле")
        return icon_data
    except Exception as e:
        print(f"❌ Ошибка загрузки {ICONS_MAP_FILE}: {e}")
        return None

def process_epg_file(file_path, group_map, icon_pool, owner, repo_name, entry):
    """Обрабатывает EPG-файл с заменой URL иконок."""
    print(f"🔧 Обрабатываю: {entry['desc']}")
    
    if not group_map or not icon_pool:
        print(f"   ⚠️  Нет карты иконок, пропускаю замену")
        return True

    try:
        was_gzipped = is_gzipped(file_path)
        open_func = gzip.open if was_gzipped else open
        parser = etree.XMLParser(remove_blank_text=True, recover=True)
        
        with open_func(file_path, 'rb') as f:
            tree = etree.parse(f, parser)
        root = tree.getroot()
        
        changes_made = 0
        processed_channels = 0
        
        for channel in root.findall('channel'):
            if timeout_handler.timeout_occurred:
                break
                
            processed_channels += 1
            if processed_channels % 1000 == 0:
                print(f"   📊 Обработано каналов: {processed_channels}")
            
            channel_id = channel.get('id')
            icon_url_pointer = group_map.get(channel_id)
            
            if icon_url_pointer:
                matched_icon_path = icon_pool.get(icon_url_pointer)
                
                if matched_icon_path and matched_icon_path.exists():
                    new_icon_url = RAW_BASE_URL.format(
                        owner=owner, 
                        repo=repo_name, 
                        filepath=str(matched_icon_path).replace('\\', '/')
                    )
                    icon_tag = channel.find('icon')
                    if icon_tag is None:
                        icon_tag = etree.SubElement(channel, 'icon')
                    
                    if icon_tag.get('src') != new_icon_url:
                        icon_tag.set('src', new_icon_url)
                        changes_made += 1
        
        if changes_made > 0:
            print(f"   ✅ Внесено изменений: {changes_made}")
            
            original_filename = Path(urlparse(entry['url']).path).name
            if original_filename.lower().endswith('.gz'):
                archive_internal_name = original_filename[:-3]
            else:
                archive_internal_name = f"{original_filename}.xml"
            
            doctype_str = '<!DOCTYPE tv SYSTEM "https://iptvx.one/xmltv.dtd">'
            xml_bytes = etree.tostring(
                tree, 
                pretty_print=True, 
                xml_declaration=True, 
                encoding='UTF-8', 
                doctype=doctype_str
            )
            
            if was_gzipped:
                with gzip.GzipFile(
                    filename=archive_internal_name, 
                    mode='wb', 
                    fileobj=open(file_path, 'wb'), 
                    mtime=0
                ) as f_out:
                    f_out.write(xml_bytes)
            else:
                with open(file_path, 'wb') as f_out:
                    f_out.write(xml_bytes)
        else:
            print(f"   ℹ️  Изменений не требуется")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка обработки {file_path}: {e}")
        return False

def update_readme(results, notes):
    """Обновляет README.md с результатами выполнения."""
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M %Z')
    lines = []
    
    if notes:
        lines.extend([notes, "\n---"])
    
    lines.append(f"\n# 🔄 Обновлено: {timestamp}\n")
    
    successful = sum(1 for r in results if not r.get('error'))
    failed = len(results) - successful
    
    for idx, r in enumerate(results, 1):
        lines.append(f"**{idx}. {r['entry']['desc']}**\n")
        if r.get('error'):
            lines.extend([
                f"**Статус:** ❌ Ошибка",
                f"**Источник:** `{r['entry']['url']}`",
                f"**Причина:** {r.get('error')}",
                "\n---"
            ])
        else:
            lines.extend([
                f"**Размер:** {r['size_mb']} MB",
                "",
                "**Ссылка для плеера (GitHub Raw):**",
                f"`{r['raw_url']}`",
                "\n---"
            ])
    
    try:
        with open(README_FILE, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))
        print(f"✅ README.md обновлён ({len(results)} записей)")
    except Exception as e:
        print(f"❌ Ошибка обновления README: {e}")

def main():
    print("🚀 Запуск EPG Updater Script")
    
    parser = argparse.ArgumentParser(description="EPG Updater Script")
    parser.add_argument('--full-update', action='store_true', 
                       help='Выполнить полное обновление включая иконки')
    args = parser.parse_args()

    repo = os.getenv('GITHUB_REPOSITORY')
    if not repo or '/' not in repo:
        sys.exit("❌ Ошибка: Переменная окружения GITHUB_REPOSITORY не определена.")
    owner, repo_name = repo.split('/')
    
    print(f"📁 Репозиторий: {owner}/{repo_name}")
    
    sources, notes = read_sources_and_notes()
    print(f"📋 Найдено источников: {len(sources)}")
    
    print("\n--- Этап 0: Загрузка EPG файлов ---")
    clear_directory(DATA_DIR)
    
    download_results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_entry = {executor.submit(download_one, entry): entry for entry in sources}
        
        for future in as_completed(future_to_entry):
            if timeout_handler.timeout_occurred:
                print("⚠️  Загрузка прервана по таймауту")
                break
            try:
                result = future.result(timeout=REQUEST_TIMEOUT * 2)
                download_results.append(result)
            except TimeoutError:
                entry = future_to_entry[future]
                download_results.append({
                    'entry': entry, 
                    'error': 'Таймаут загрузки'
                })

    icon_data = None
    if args.full_update:
        print("\n🔄 Режим: ПОЛНОЕ ОБНОВЛЕНИЕ")
        clear_directory(ICONS_DIR)
        icon_data = perform_full_update(download_results)
    else:
        print("\n📅 Режим: ЕЖЕДНЕВНОЕ ОБНОВЛЕНИЕ")  
        icon_data = load_icon_data_for_daily_update()

    print("\n--- Этап 3: Замена ссылок на иконки ---")
    if icon_data:
        icon_pool = icon_data.get('icon_pool', {})
        processing_futures = []
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for res in download_results:
                if res.get('error') or timeout_handler.timeout_occurred: 
                    continue
                
                source_url = res['entry']['url']
                group_hash = icon_data['source_to_group'].get(source_url)
                
                group_map = {}
                if group_hash and group_hash in icon_data['groups']:
                    group_map = icon_data['groups'][group_hash].get('icon_map', {})
                
                future = executor.submit(
                    process_epg_file, 
                    res['temp_path'], 
                    group_map, 
                    icon_pool, 
                    owner, 
                    repo_name, 
                    res['entry']
                )
                processing_futures.append(future)
            
            for future in as_completed(processing_futures):
                if timeout_handler.timeout_occurred:
                    break
                try:
                    future.result(timeout=300)
                except TimeoutError:
                    print("⚠️  Таймаут обработки файла")
    else:
        print("ℹ️  Данные об иконках отсутствуют, замена пропущена")

    print("\n--- Этап 4: Финализация ---")
    
    url_to_result = {res['entry']['url']: res for res in download_results}
    ordered_results = [url_to_result[s['url']] for s in sources]
    
    final_results = []
    used_names = set()

    for res in ordered_results:
        if res.get('error'):
            final_results.append(res)
            continue
            
        final_filename_from_url = Path(urlparse(res['entry']['url']).path).name
        if not Path(final_filename_from_url).suffix:
            ext = '.xml.gz' if is_gzipped(res['temp_path']) else '.xml'
            proposed_filename = f"{final_filename_from_url}{ext}"
        else:
            proposed_filename = final_filename_from_url

        final_name, counter = proposed_filename, 1
        while final_name in used_names:
            p_stem = Path(proposed_filename).stem
            p_suffixes = "".join(Path(proposed_filename).suffixes)
            final_name = f"{p_stem}-{counter}{p_suffixes}"
            counter += 1
        used_names.add(final_name)
        
        target_path = DATA_DIR / final_name
        try:
            res['temp_path'].rename(target_path)
            res['raw_url'] = RAW_BASE_URL.format(
                owner=owner, 
                repo=repo_name, 
                filepath=str(target_path).replace('\\', '/')
            )
        except Exception as e:
            res['error'] = f"Ошибка перемещения файла: {e}"
        
        final_results.append(res)

    update_readme(final_results, notes)
    
    successful = sum(1 for r in final_results if not r.get('error'))
    failed = len(final_results) - successful
    
    print(f"\n🎉 Скрипт завершен!")
    print(f"📊 Результат: {successful} успешно, {failed} ошибок")
    
    if timeout_handler.timeout_occurred:
        print("⚠️  Внимание: выполнение было прервано по таймауту")
        sys.exit(1)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Выполнение прервано пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Критическая ошибка: {e}")
        sys.exit(1)
