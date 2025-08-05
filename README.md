Это новый репозиторий-зеркало EPG файлов, где я попытался исправить отображение иконок. В старом репозитории (https://github.com/Lorax121/everyday_epg_update) отображение иконок не подразумевалось, но в текущем я не просто перегружаю оригинальные epg файлы, но редактирую их так, чтобы иконки тоже грузились. 

UPD 22.07.2025: теперь во всех источниках epg используются собственные (оригинальные) иконки каналов. 

Нужно тестировать, пробуйте и пишите отзывы там, где вы узнали про репозиторий. 

### Отказ от ответственности

Этот репозиторий работает как автоматизированное зеркало для сбора общедоступных EPG (Electronic Program Guide) файлов. Я не являюсь владельцем или автором этих данных и не претендую на какие-либо авторские права на них. 

---

# 🔄 Обновлено: 2025-08-05 02:08 UTC

**1. основной файл EPG.ONE с прямоугольными пиконами с прозрачным фоном**

**Статус:** ❌ Ошибка
**Источник:** `http://epg.one/epg2.xml.gz`
**Причина:** Ошибка загрузки: HTTPConnectionPool(host='epg.one', port=80): Max retries exceeded with url: /epg2.xml.gz (Caused by ConnectTimeoutError(<urllib3.connection.HTTPConnection object at 0x7f78457e5350>, 'Connection to epg.one timed out. (connect timeout=45)'))

---
**2. основной файл EPG.ONE с квадратными пиконами с темным фоном**

**Статус:** ❌ Ошибка
**Источник:** `http://epg.one/epg.xml.gz`
**Причина:** Ошибка загрузки: HTTPConnectionPool(host='epg.one', port=80): Max retries exceeded with url: /epg.xml.gz (Caused by ConnectTimeoutError(<urllib3.connection.HTTPConnection object at 0x7f7844c6b550>, 'Connection to epg.one timed out. (connect timeout=45)'))

---
**3. облегченный файл EPG.ONE с квадратными пиконами с темным фоном**

**Статус:** ❌ Ошибка
**Источник:** `http://epg.one/epg.xml`
**Причина:** Ошибка загрузки: HTTPConnectionPool(host='epg.one', port=80): Max retries exceeded with url: /epg.xml (Caused by ConnectTimeoutError(<urllib3.connection.HTTPConnection object at 0x7f7844c6cb90>, 'Connection to epg.one timed out. (connect timeout=45)'))

---
**4. облегченный файл iptvx.one (архив на 14 дней / без описаний)**

**Размер:** 23.09 MB

**Ссылка для плеера (GitHub Raw):**
`https://raw.githubusercontent.com/Lorax121/epg_v2/main/data/EPG_LITE.xml.gz`

---